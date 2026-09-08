import re
from playwright.sync_api import sync_playwright
import logging

log = logging.getLogger(__name__)


class PartageSession:
    def __init__(self, cfg):
        self.cfg = cfg
        self._pw = self._browser = self._context = self._page = None

    def start(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self._login()

    def _login(self):
        p = self._page
        p.goto(self.cfg['partage_url'], timeout=self.cfg['browser_timeout'])
        p.fill('#username', self.cfg['username'])
        p.fill('#password', self.cfg['password'])
        p.click('[type=submit]')
        # SSO may show an "Information Release" consent page before redirecting
        p.wait_for_url(re.compile(r'(sso|partage)\.bordeaux-inp\.fr'), timeout=self.cfg['browser_timeout'], wait_until='commit')
        if 'sso.bordeaux-inp.fr' in p.url:
            p.click('button[name="_eventId_proceed"]')
        p.wait_for_function(
            "() => window.location.hostname === 'partage.bordeaux-inp.fr'",
            timeout=self.cfg['browser_timeout'],
        )
        log.info('CAS login successful')

    def get_new_emails(self, known_ids: set) -> list:
        p = self._page
        p.goto(self.cfg['partage_url'], timeout=self.cfg['browser_timeout'])
        p.wait_for_function(
            "() => window.location.hostname === 'partage.bordeaux-inp.fr'",
            timeout=self.cfg['browser_timeout'],
        )
        p.wait_for_selector('[id^="zli__CLV"]', timeout=self.cfg['browser_timeout'])
        rows = p.query_selector_all('[id^="zli__CLV"]')
        emails = []
        for row in rows:
            conv_id = row.get_attribute('id').split('__')[-1]
            is_unread = row.query_selector('.ImgMsgUnread') is not None
            if conv_id and conv_id not in known_ids and is_unread:
                sender = row.query_selector('[id$="__fr"]')
                subject_el = row.query_selector('[id$="__su"] span:first-child')
                date_el = row.query_selector('[id$="__dt"]')
                prev_el = p.query_selector('.MsgBody')
                prev_html = prev_el.inner_html() if prev_el else None
                row.click()
                p.wait_for_selector('.MsgBody', timeout=self.cfg['browser_timeout'])
                # The previous message's body lingers in the reading pane, so a
                # bare wait_for_selector can return it and forward the wrong
                # content. Wait for it to actually change; if it legitimately
                # does not (identical bodies), fall through rather than fail.
                if prev_html is not None:
                    try:
                        p.wait_for_function(
                            "prev => {const e = document.querySelector('.MsgBody');"
                            " return e && e.innerHTML !== prev;}",
                            arg=prev_html,
                            timeout=self.cfg['browser_timeout'],
                        )
                    except Exception:
                        log.warning('Reading pane did not change after opening %s', conv_id)
                body_el = p.query_selector('.MsgBody')
                emails.append({
                    'id':      conv_id,
                    'from':    sender.inner_text() if sender else '',
                    'subject': subject_el.inner_text() if subject_el else '(no subject)',
                    'date':    date_el.inner_text() if date_el else '',
                    'body':    body_el.inner_html() if body_el else '',
                })
        return emails

    def is_logged_in(self) -> bool:
        return 'partage.bordeaux-inp.fr' in self._page.url

    def stop(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
