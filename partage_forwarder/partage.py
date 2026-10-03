"""Talk to Partage (Zimbra) over plain HTTP.

Logging in means walking Bordeaux INP's single sign-on chain, which is made
entirely of ordinary HTML forms:

    partage/mail -> CAS username/password form -> Shibboleth attribute
    release consent -> auto-submitted SAMLResponse form -> partage/mail

Each page is fetched, its form filled in and submitted, until Zimbra hands out
its ZM_AUTH_TOKEN cookie. After that, everything goes through Zimbra's REST
endpoint (/service/home/~/), which serves search results as JSON and messages
as raw RFC 822, so no browser is needed at all.
"""
import logging
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__
from .rawmail import looks_like_rfc822

log = logging.getLogger(__name__)

AUTH_COOKIE = 'ZM_AUTH_TOKEN'
MAX_LOGIN_STEPS = 10
# Zimbra answers an expired session on the REST endpoint by bouncing the
# request back into the SSO chain (a redirect) or with a plain 401.
SESSION_EXPIRED = {301, 302, 303, 307, 308, 401}


class LoginError(Exception):
    """The SSO chain could not be completed."""


class BadCredentials(LoginError):
    """CAS rejected the username/password. Retrying will not help, and
    hammering CAS with a wrong password risks locking the account."""


class MessageGone(Exception):
    """The message was deleted or moved between listing and download."""


@dataclass(frozen=True)
class Summary:
    """One search hit, before downloading the message itself."""
    id: str
    conversation_id: str
    received: float  # seconds since the epoch
    unread: bool
    subject: str
    sender: str


# -- HTML form handling -----------------------------------------------------

@dataclass
class Form:
    action: str
    method: str
    # A list, not a dict: the consent page sends one _shib_idp_consentIds per
    # released attribute, and dropping any of them makes the SP reject the
    # login (it then lacks the eduPersonPrincipalName it keys accounts on).
    fields: list
    buttons: list

    @property
    def wants_password(self) -> bool:
        return any(name == 'password' for name, _ in self.fields)


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form':
            self._current = Form(
                action=a.get('action') or '',
                method=(a.get('method') or 'get').lower(),
                fields=[], buttons=[],
            )
            self.forms.append(self._current)
            return
        if self._current is None or tag not in ('input', 'button') or not a.get('name'):
            return
        kind = (a.get('type') or ('submit' if tag == 'button' else 'text')).lower()
        if kind == 'submit':
            self._current.buttons.append((a['name'], a.get('value') or ''))
        elif kind in ('checkbox', 'radio') and 'checked' not in a:
            return  # a browser only submits ticked boxes
        else:
            self._current.fields.append((a['name'], a.get('value') or ''))

    def handle_endtag(self, tag):
        if tag == 'form':
            self._current = None


def parse_forms(html: str) -> list:
    parser = _FormParser()
    parser.feed(html)
    return parser.forms


def _pick_button(form: Form):
    """The button a person would press: 'proceed' on the consent page, the
    login button on CAS; never the reject/cancel one."""
    for name, value in form.buttons:
        if 'proceed' in name.lower():
            return name, value
    for name, value in form.buttons:
        if not any(word in name.lower() for word in ('reject', 'cancel', 'deny')):
            return name, value
    return None


# -- client -------------------------------------------------------------------

class PartageClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self._session = None
        self._host = urlsplit(cfg.partage_url).hostname

    def _new_session(self) -> requests.Session:
        s = requests.Session()
        s.headers['User-Agent'] = f'partage-forwarder/{__version__} (+python-requests)'
        # The SSO servers now and then drop a connection mid-login. Retrying
        # every method is safe here: at worst CAS sees the form twice.
        retry = Retry(total=3, backoff_factor=2, allowed_methods=None,
                      status_forcelist=(502, 503, 504), raise_on_status=False)
        s.mount('https://', HTTPAdapter(max_retries=retry))
        s.mount('http://', HTTPAdapter(max_retries=retry))
        return s

    def _logged_in(self, s: requests.Session, resp: requests.Response) -> bool:
        return (
            urlsplit(resp.url).hostname == self._host
            and s.cookies.get(AUTH_COOKIE, domain=self._host) is not None
        )

    def login(self):
        """Walk the SSO chain from scratch. Starting from a clean session
        keeps a half-expired one from sending us down an odd branch."""
        s = self._new_session()
        timeout = self.cfg.http_timeout
        resp = s.get(self.cfg.partage_url, timeout=timeout)
        sent_password = False

        for _ in range(MAX_LOGIN_STEPS):
            resp.raise_for_status()
            if self._logged_in(s, resp):
                self._session = s
                log.info('Logged in to Partage as %s', self.cfg.username)
                return

            forms = [f for f in parse_forms(resp.text) if f.fields or f.buttons]
            if not forms:
                raise LoginError(f'Login stalled on a page without a form: {resp.url}')
            form = next((f for f in forms if f.wants_password), forms[0])

            if form.wants_password:
                # Being shown the password form a second time means CAS
                # turned the first attempt down.
                if sent_password:
                    raise BadCredentials(
                        'CAS rejected the username/password - check PARTAGE_USERNAME '
                        '(your CAS login, not an email address) and PARTAGE_PASSWORD')
                sent_password = True

            creds = {'username': self.cfg.username, 'password': self.cfg.password}
            data = [(k, creds.get(k, v)) for k, v in form.fields]
            button = _pick_button(form)
            if button:
                data.append(button)

            url = urljoin(resp.url, form.action or resp.url)
            host = urlsplit(url).hostname
            log.debug('Login step: %s %s', form.method.upper(), host)
            if form.method == 'post':
                resp = s.post(url, data=data, timeout=timeout)
            else:
                resp = s.get(url, params=data, timeout=timeout)

        raise LoginError(f'Login did not finish after {MAX_LOGIN_STEPS} steps (last page: {resp.url})')

    def _rest(self, params: dict) -> requests.Response:
        url = f'{self.cfg.partage_base}/service/home/~/'
        for attempt in (1, 2):
            if self._session is None:
                self.login()
            resp = self._session.get(
                url, params=params, timeout=self.cfg.http_timeout, allow_redirects=False)
            if resp.status_code not in SESSION_EXPIRED:
                return resp
            log.info('Partage session expired (HTTP %s) - logging in again', resp.status_code)
            self._session = None
        raise LoginError('Partage rejected a session it had just issued')

    def search(self, query: str) -> list:
        """Messages matching a Zimbra search query, oldest first. Zimbra's
        REST endpoint returns every hit (it ignores 'limit'), so keep the
        query narrow."""
        resp = self._rest({'fmt': 'json', 'query': query, 'types': 'message'})
        resp.raise_for_status()
        data = resp.json()
        # The REST endpoint returns {} for no results; some versions wrap the
        # result in a SOAP-style envelope.
        hits = data.get('m')
        if hits is None:
            hits = data.get('Body', {}).get('SearchResponse', {}).get('m', [])
        summaries = []
        for m in hits:
            if 'id' not in m:
                continue
            sender = next((e.get('p') or e.get('a') or '' for e in m.get('e', []) if e.get('t') == 'f'), '')
            summaries.append(Summary(
                id=str(m['id']),
                conversation_id=str(m.get('cid', '')),
                received=m.get('d', 0) / 1000,
                unread='u' in m.get('f', ''),
                subject=m.get('su', ''),
                sender=sender,
            ))
        return sorted(summaries, key=lambda s: s.received)

    def fetch_raw(self, msg_id: str) -> bytes:
        """The message exactly as it was received, as RFC 822 bytes."""
        resp = self._rest({'id': msg_id})
        if resp.status_code == 404:
            raise MessageGone(msg_id)
        resp.raise_for_status()
        if not looks_like_rfc822(resp.content):
            raise ValueError(
                f'Partage returned something other than a mail for message {msg_id} '
                f'({resp.headers.get("content-type")})')
        return resp.content

    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None


def age(summary: Summary) -> float:
    """Seconds since the message arrived."""
    return time.time() - summary.received
