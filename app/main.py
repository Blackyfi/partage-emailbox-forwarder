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
    try:
        session.start()
    except Exception:
        # Don't die on a bad first attempt (CAS down, no network at boot); the
        # loop below notices the dead session and rebuilds it.
        log.error('Initial session start failed', exc_info=True)

    while True:
        try:
            if not session.is_logged_in():
                log.warning('Session expired - re-logging in')
                session._login()

            known = get_known_ids(cfg['db_path'])
            emails = session.get_new_emails(known)
            for email in emails:
                # Opening the message in Partage already marked it read, so a
                # failure here means it will not look new next cycle. Keep going
                # through the rest of the batch and make the loss loud.
                try:
                    forward(email, cfg)
                    mark_forwarded(cfg['db_path'], email['id'])
                    log.info(f"Forwarded: {email['subject']}")
                except Exception:
                    log.error(
                        f"NOT FORWARDED (already marked read in Partage, will not "
                        f"retry): id={email['id']} subject={email['subject']!r}",
                        exc_info=True,
                    )

            # Logged at INFO so a healthy-but-idle poller is distinguishable
            # from one that has quietly stopped polling altogether.
            log.info(f'Cycle complete: {len(emails)} new')

        except Exception as e:
            log.error(f'Cycle error: {e}', exc_info=True)
            # A crashed or OOM-killed browser fails every future cycle
            # identically, so retrying the same dead page would stall the
            # forwarder forever. Rebuild the session instead.
            if not session.is_alive():
                log.warning('Browser session is dead - restarting it')
                try:
                    session.stop()
                    session.start()
                    log.info('Browser session restarted')
                except Exception:
                    log.error('Session restart failed; will retry', exc_info=True)

        time.sleep(cfg['poll_interval'])


if __name__ == '__main__':
    run()
