import json

import pytest

from partage_forwarder import partage
from partage_forwarder.partage import BadCredentials, PartageClient, parse_forms

CAS_PAGE = """
<form id="fm1" action="login" method="post">
  <input id="username" name="username" type="text" value="">
  <input id="password" name="password" type="password" value="">
  <input type="hidden" name="execution" value="e1">
  <input type="hidden" name="_eventId" value="submit">
  <button name="submitBtn" type="submit">LOGIN</button>
</form>"""

CONSENT_PAGE = """
<form action="/idp/profile/SAML2/Redirect/SSO?execution=e1s2" method="post">
  <input type="hidden" name="csrf_token" value="tok">
  <input type="checkbox" name="_shib_idp_consentIds" value="eduPersonPrincipalName" checked>
  <input type="checkbox" name="_shib_idp_consentIds" value="schacHomeOrganization" checked>
  <input type="checkbox" name="marketing" value="yes">
  <input type="radio" name="_shib_idp_consentOptions" value="_shib_idp_doNotRememberConsent">
  <input type="radio" name="_shib_idp_consentOptions" value="_shib_idp_rememberConsent" checked>
  <input type="submit" name="_eventId_AttributeReleaseRejected" value="Reject">
  <input type="submit" name="_eventId_proceed" value="Accept">
</form>"""

SAML_PAGE = """
<body onload="document.forms[0].submit()">
<form action="https://sp.partage.example/Shibboleth.sso/SAML2/POST" method="post">
  <input type="hidden" name="RelayState" value="rs">
  <input type="hidden" name="SAMLResponse" value="PHNhbWw+">
</form></body>"""


def test_consent_form_keeps_repeated_and_checked_fields_only():
    (form,) = parse_forms(CONSENT_PAGE)
    assert form.fields == [
        ('csrf_token', 'tok'),
        ('_shib_idp_consentIds', 'eduPersonPrincipalName'),
        ('_shib_idp_consentIds', 'schacHomeOrganization'),
        ('_shib_idp_consentOptions', '_shib_idp_rememberConsent'),
    ]
    assert partage._pick_button(form) == ('_eventId_proceed', 'Accept')


class FakeResponse:
    def __init__(self, url, text='', status=200, content=None, headers=None):
        self.url, self.text, self.status_code = url, text, status
        self.content = content if content is not None else text.encode()
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise partage.requests.HTTPError(self.status_code)

    def json(self):
        return json.loads(self.text)


class FakeSSO:
    """Just enough of CAS + Shibboleth + Zimbra to exercise the client."""

    def __init__(self, password='s3cret'):
        self.password = password
        self.requests = []
        self.cookies = partage.requests.cookies.RequestsCookieJar()
        self.headers = {}
        self.api_calls = 0
        self.expire_next_api_call = False

    def get(self, url, params=None, timeout=None, allow_redirects=True):
        self.requests.append(('GET', url, params))
        if '/service/home/' in url:
            self.api_calls += 1
            if self.expire_next_api_call:
                self.expire_next_api_call = False
                return FakeResponse(url, status=302)
            if 'query' in params:
                return FakeResponse(url, json.dumps({'m': [
                    {'id': '2', 'cid': '-2', 'd': 2000, 'f': 'u', 'su': 'New',
                     'e': [{'t': 'f', 'p': 'Sarah', 'a': 's@x'}]},
                    {'id': '1', 'cid': '-1', 'd': 1000, 'su': 'Old', 'e': []},
                ]}))
            if params.get('id') == '404':
                return FakeResponse(url, status=404)
            return FakeResponse(url, content=b'From: a@x\r\nSubject: hi\r\n\r\nbody\r\n')
        return FakeResponse('https://cas.example/login?service=x', CAS_PAGE)

    def post(self, url, data=None, timeout=None):
        self.requests.append(('POST', url, data))
        fields = dict(data)
        if url == 'https://cas.example/login':
            if fields['password'] != self.password:
                return FakeResponse(url, CAS_PAGE)
            return FakeResponse('https://sso.example/idp/profile/SAML2/Redirect/SSO?execution=e1s2', CONSENT_PAGE)
        if 'sso.example' in url:
            assert ('_shib_idp_consentIds', 'eduPersonPrincipalName') in data
            assert fields['_eventId_proceed'] == 'Accept'
            return FakeResponse(url, SAML_PAGE)
        if 'Shibboleth.sso' in url:
            self.cookies.set(partage.AUTH_COOKIE, 'token', domain='partage.bordeaux-inp.fr')
            return FakeResponse('https://partage.bordeaux-inp.fr/mail', '<html>zimbra</html>')
        raise AssertionError(f'unexpected POST {url}')

    def close(self):
        pass


@pytest.fixture
def sso(monkeypatch):
    fake = FakeSSO()
    monkeypatch.setattr(PartageClient, '_new_session', lambda self: fake)
    return fake


def test_login_walks_the_sso_chain(cfg, sso):
    PartageClient(cfg).login()
    login_post = next(d for m, u, d in sso.requests if m == 'POST' and 'cas.example' in u)
    assert ('username', 'jdoe') in login_post and ('password', 's3cret') in login_post
    assert ('execution', 'e1') in login_post


def test_rejected_password_is_reported_not_retried(cfg, sso):
    sso.password = 'other'
    with pytest.raises(BadCredentials):
        PartageClient(cfg).login()
    assert sum(1 for m, u, _ in sso.requests if m == 'POST') == 1


def test_search_parses_and_sorts_oldest_first(cfg, sso):
    hits = PartageClient(cfg).search('in:inbox')
    assert [h.id for h in hits] == ['1', '2']
    assert hits[1].unread and not hits[0].unread
    assert hits[1].sender == 'Sarah' and hits[1].received == 2.0


def test_expired_session_logs_in_again(cfg, sso):
    client = PartageClient(cfg)
    client.search('in:inbox')
    sso.expire_next_api_call = True
    assert client.fetch_raw('5').startswith(b'From:')
    logins = sum(1 for m, u, _ in sso.requests if m == 'POST' and 'cas.example' in u)
    assert logins == 2


def test_fetch_raw_missing_message(cfg, sso):
    with pytest.raises(partage.MessageGone):
        PartageClient(cfg).fetch_raw('404')
