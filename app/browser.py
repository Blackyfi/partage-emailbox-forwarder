import re
from playwright.sync_api import sync_playwright
import logging

log = logging.getLogger(__name__)

ROW_SELECTOR = '[id^="zli__CLV"]'
PARTAGE_HOST = 'partage.bordeaux-inp.fr'
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
        p = self._page
        p.goto(self.cfg['partage_url'], timeout=self.cfg['browser_timeout'])
        p.wait_for_function(ON_PARTAGE, timeout=self.cfg['browser_timeout'])
        p.wait_for_selector(ROW_SELECTOR, timeout=self.cfg['browser_timeout'])

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
        prev_body = None
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
            p.wait_for_selector('.MsgBody', timeout=self.cfg['browser_timeout'])
            # The previous message's body stays in the reading pane while the
            # next one loads, so a bare wait can return stale content and
            # forward the wrong body. Wait for it to change; if it genuinely
            # does not (two identical bodies), carry on rather than fail.
            if prev_body is not None:
                try:
                    p.wait_for_function(
                        "prev => {const e = document.querySelector('.MsgBody');"
                        " return e && e.innerHTML !== prev;}",
                        arg=prev_body,
                        timeout=self.cfg['browser_timeout'],
                    )
                except Exception:
                    log.warning('Reading pane did not change after opening %s', conv_id)

            body_el = p.query_selector('.MsgBody')
            body = body_el.inner_html() if body_el else ''
            prev_body = body
            emails.append({'id': conv_id, 'body': body, **meta})

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
