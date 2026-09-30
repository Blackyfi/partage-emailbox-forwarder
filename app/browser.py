import logging
import re
import time
from html import unescape
from urllib.parse import unquote, urljoin

from playwright.sync_api import sync_playwright

from rawmail import looks_like_rfc822, parse

log = logging.getLogger(__name__)

ROW_SELECTOR = '[id^="zli__CLV"]'
PARTAGE_HOST = 'partage.bordeaux-inp.fr'
BASE_URL = f'https://{PARTAGE_HOST}'
ON_PARTAGE = f"() => window.location.hostname === '{PARTAGE_HOST}'"


class PartageSession:
    def __init__(self, cfg):
        self.cfg = cfg
        self._pw = self._browser = self._context = self._page = None

    def start(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        # Otherwise clicks and queries silently use Playwright's 30s default
        # instead of the configured timeout.
        self._page.set_default_timeout(self.cfg['browser_timeout'])
        self._login()

    def _login(self):
        p = self._page
        p.goto(self.cfg['partage_url'], timeout=self.cfg['browser_timeout'])
        p.fill('#username', self.cfg['username'])
        p.fill('#password', self.cfg['password'])
        p.click('[type=submit]')
        # SSO may show an "Information Release" consent page before redirecting
        p.wait_for_url(
            re.compile(r'(sso|partage)\.bordeaux-inp\.fr'),
            timeout=self.cfg['browser_timeout'],
            wait_until='commit',
        )
        if 'sso.bordeaux-inp.fr' in p.url:
            p.click('button[name="_eventId_proceed"]')
        p.wait_for_function(ON_PARTAGE, timeout=self.cfg['browser_timeout'])
        log.info('CAS login successful')

    def get_new_emails(self, known_ids: set) -> list:
        """Unread inbox messages not forwarded yet, with full content.

        Fetching the raw RFC 822 message is the reliable path: it carries the
        real body, inline images and attachments. If that endpoint misbehaves,
        fall back to scraping the reading pane so mail still gets through.
        """
        p = self._page
        p.goto(self.cfg['partage_url'], timeout=self.cfg['browser_timeout'])
        p.wait_for_function(ON_PARTAGE, timeout=self.cfg['browser_timeout'])
        p.wait_for_selector(ROW_SELECTOR, timeout=self.cfg['browser_timeout'])
        try:
            return self._get_new_emails_raw(known_ids)
        except Exception:
            log.warning('Raw message fetch failed - falling back to reading-pane scrape',
                        exc_info=True)
            return self._get_new_emails_dom(known_ids)

    # -- raw RFC 822 path ---------------------------------------------------

    def _get(self, params: dict):
        return self._context.request.get(
            f'{BASE_URL}/service/home/~/',
            params=params,
            timeout=self.cfg['browser_timeout'],
        )

    def _list_unread_ids(self) -> list:
        resp = self._get({
            'fmt': 'json',
            'query': 'in:inbox is:unread',
            'types': 'message',
            'limit': '50',
        })
        if not resp.ok:
            raise RuntimeError(f'Unread search returned HTTP {resp.status}')
        data = resp.json()
        msgs = data.get('m')
        if msgs is None:
            msgs = data.get('Body', {}).get('SearchResponse', {}).get('m', [])
        msgs = sorted(msgs, key=lambda m: m.get('d', 0))  # oldest first
        return [str(m['id']) for m in msgs if 'id' in m]

    def _fetch_raw(self, msg_id: str) -> bytes:
        # Zimbra serves a message item as RFC 822; the explicit formats are
        # tried in case a deployment does not default to it.
        for extra in ({}, {'fmt': 'native'}, {'fmt': 'eml'}):
            resp = self._get({'id': msg_id, **extra})
            if resp.ok and looks_like_rfc822(resp.body()):
                return resp.body()
        raise RuntimeError(f'No raw RFC 822 content for message {msg_id}')

    def _get_new_emails_raw(self, known_ids: set) -> list:
        emails = []
        for msg_id in self._list_unread_ids():
            key = f'msg:{msg_id}'
            if key in known_ids:
                continue
            emails.append(parse(self._fetch_raw(msg_id), key))
        return emails

    # -- reading-pane fallback ---------------------------------------------

    def _read_body(self) -> str:
        """HTML of the open message. Zimbra renders HTML mail inside an
        iframe (a bare page.query_selector never sees it) and plain-text mail
        directly in the page, so check every frame."""
        found = ''
        for frame in self._page.frames:
            try:
                el = frame.query_selector('.MsgBody')
                html = el.inner_html() if el else ''
            except Exception:
                continue
            if html.strip():
                found = html
                if frame is not self._page.main_frame:
                    break
        return found

    def _download(self, url: str):
        resp = self._context.request.get(
            urljoin(BASE_URL, url), timeout=self.cfg['browser_timeout'])
        if not resp.ok:
            raise RuntimeError(f'HTTP {resp.status} for {url}')
        ctype = (resp.headers.get('content-type') or 'application/octet-stream').split(';')[0]
        disp = resp.headers.get('content-disposition') or ''
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disp)
        return resp.body(), ctype, unquote(m.group(1)) if m else None

    def _collect_media(self, body: str):
        """Fetch authenticated inline images and attachment links, which
        Gmail cannot load itself."""
        inline, attachments = [], []

        def swap(match):
            try:
                data, ctype, _ = self._download(unescape(match.group(2)))
            except Exception:
                log.warning('Could not fetch inline image %s', match.group(2), exc_info=True)
                return match.group(0)
            cid = f'img{len(inline)}@partage'
            inline.append({'cid': cid, 'content_type': ctype, 'data': data})
            return f'{match.group(1)}cid:{cid}{match.group(3)}'

        body = re.sub(r'(src=["\'])((?:https?://[^"\'/]+)?/service/[^"\']+)(["\'])', swap, body)

        seen = set()
        for frame in self._page.frames:
            for a in frame.query_selector_all('a[href*="/service/home/"][href*="disp=a"]'):
                href = a.get_attribute('href')
                if not href or href in seen:
                    continue
                seen.add(href)
                try:
                    data, ctype, name = self._download(href)
                except Exception:
                    log.warning('Could not download attachment %s', href, exc_info=True)
                    continue
                attachments.append({
                    'filename': name or (a.inner_text() or 'attachment').strip(),
                    'content_type': ctype,
                    'data': data,
                })
        return body, inline, attachments

    def _get_new_emails_dom(self, known_ids: set) -> list:
        p = self._page

        # Collect the ids first. Opening a message re-renders the list (the row
        # loses its unread styling), which detaches every element handle taken
        # before the click, so handles cannot be held across iterations.
        pending = []
        for row in p.query_selector_all(ROW_SELECTOR):
            row_id = row.get_attribute('id')
            if not row_id:
                continue
            conv_id = row_id.split('__')[-1]
            if not conv_id or conv_id in known_ids:
                continue
            if row.query_selector('.ImgMsgUnread') is None:
                continue
            pending.append((row_id, conv_id))

        emails = []
        prev_body = ''
        for row_id, conv_id in pending:
            row = p.query_selector(f'[id="{row_id}"]')
            if row is None:
                log.warning('Row %s vanished before it could be read', row_id)
                continue

            sender = row.query_selector('[id$="__fr"]')
            subject_el = row.query_selector('[id$="__su"] span:first-child')
            date_el = row.query_selector('[id$="__dt"]')
            meta = {
                'from': sender.inner_text() if sender else '',
                'subject': subject_el.inner_text() if subject_el else '(no subject)',
                'date': date_el.inner_text() if date_el else '',
            }

            row.click()
            # The previous message's body stays in the reading pane while the
            # next one loads, so wait for it to change. If it genuinely does
            # not (two identical bodies), carry on with what is there.
            deadline = time.monotonic() + self.cfg['browser_timeout'] / 1000
            body = self._read_body()
            while (not body or body == prev_body) and time.monotonic() < deadline:
                p.wait_for_timeout(250)
                body = self._read_body()
            if not body:
                log.warning('Empty body for %s', conv_id)
            elif body == prev_body:
                log.warning('Reading pane did not change after opening %s', conv_id)
            prev_body = body

            body, inline, attachments = self._collect_media(body)
            emails.append({
                'id': conv_id, 'body': body,
                'inline': inline, 'attachments': attachments, **meta,
            })

        return emails

    def is_logged_in(self) -> bool:
        return PARTAGE_HOST in self._page.url

    def is_alive(self) -> bool:
        """True if the browser is still usable; never raises."""
        try:
            return (
                self._browser is not None
                and self._browser.is_connected()
                and self._page is not None
                and not self._page.is_closed()
            )
        except Exception:
            return False

    def stop(self):
        for closer in (
            lambda: self._context and self._context.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:
                pass
        self._pw = self._browser = self._context = self._page = None
