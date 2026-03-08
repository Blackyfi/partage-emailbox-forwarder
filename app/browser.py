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
        p.goto(self.cfg['cas_url'], timeout=self.cfg['browser_timeout'])
        p.fill('#username', self.cfg['username'])
        p.fill('#password', self.cfg['password'])
        p.click('[type=submit]')
        p.wait_for_url('*partage.bordeaux-inp.fr*', timeout=self.cfg['browser_timeout'])
        log.info('CAS login successful')

    def get_new_emails(self, known_ids: set) -> list:
        p = self._page
        p.goto(self.cfg['partage_url'], timeout=self.cfg['browser_timeout'])
        # Wait for inbox list
        # NOTE: CSS selectors are placeholders — inspect the live Partage/Zimbra DOM
        # to confirm the correct selectors before deploying.
        p.wait_for_selector('.zl__ri__r', timeout=self.cfg['browser_timeout'])
        rows = p.query_selector_all('.zl__ri__r')
        emails = []
        for row in rows:
            msg_id = row.get_attribute('id')
            if msg_id and msg_id not in known_ids:
                row.click()
                p.wait_for_selector('.msg', timeout=self.cfg['browser_timeout'])
                emails.append({
                    'id':      msg_id,
                    'from':    p.inner_text('.msg .From') or '',
                    'subject': p.inner_text('.msg .Subject') or '(no subject)',
                    'date':    p.inner_text('.msg .Date') or '',
                    'body':    p.inner_html('.msg .Body') or '',
                })
        return emails

    def is_logged_in(self) -> bool:
        return 'cas.bordeaux-inp.fr' not in self._page.url

    def stop(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
