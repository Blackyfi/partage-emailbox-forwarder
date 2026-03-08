import time
import logging

from config import load
from browser import PartageSession
from db import get_known_ids, mark_forwarded
from forwarder import forward


def run():
    cfg = load()
    logging.basicConfig(
        level=cfg['log_level'],
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    log = logging.getLogger(__name__)

    session = PartageSession(cfg)
    session.start()

    while True:
        try:
            if not session.is_logged_in():
                log.warning('Session expired – re-logging in')
                session._login()

            known = get_known_ids(cfg['db_path'])
            emails = session.get_new_emails(known)
            for email in emails:
                forward(email, cfg)
                mark_forwarded(cfg['db_path'], email['id'])
                log.info(f"Forwarded: {email['subject']}")

            if not emails:
                log.debug('No new emails')

        except Exception as e:
            log.error(f'Cycle error: {e}', exc_info=True)

        time.sleep(cfg['poll_interval'])


if __name__ == '__main__':
    run()
