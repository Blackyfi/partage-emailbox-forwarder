"""Command-line entry point: python -m partage_forwarder [--once] [--dry-run DIR]"""
import argparse
import logging
import signal
import sys
import threading
import time

from . import __version__
from .config import ConfigError, load
from .partage import BadCredentials, PartageClient
from .service import Forwarder
from .store import Store

log = logging.getLogger('partage_forwarder')

MAX_BACKOFF = 3600
# Retrying a rejected password every few minutes is how accounts get locked.
BAD_CREDENTIALS_PAUSE = 6 * 3600


def _setup_logging(level: str):
    logging.basicConfig(
        level=level,
        format='%(asctime)s %(levelname)-7s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def _healthcheck(cfg) -> int:
    """Exit 0 if a poll succeeded recently. Used by the Docker HEALTHCHECK."""
    try:
        last = float(Store(cfg.db_path).get_meta('last_success', 0))
    except Exception as exc:
        print(f'unhealthy: {exc}')
        return 1
    age = time.time() - last
    # Room for a few failed polls (and their backoff) before calling it down.
    if age > max(4 * cfg.poll_interval, 1800):
        print(f'unhealthy: last successful poll {age / 60:.0f} min ago')
        return 1
    print(f'ok: last successful poll {age:.0f}s ago')
    return 0


def _status(cfg) -> int:
    store = Store(cfg.db_path)
    last = float(store.get_meta('last_success', 0))
    print(f'Last successful poll: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last)) if last else "never"}')
    print('Recently forwarded:')
    for when, status, sender, subject in store.recent():
        flag = '' if status == 'forwarded' else f' [{status.upper()}]'
        print(f'  {when} UTC  {sender or "?"} - {subject or "(legacy entry)"}{flag}')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='partage-forwarder',
        description='Forward new mail from a Partage (Zimbra) mailbox to Gmail.')
    parser.add_argument('--once', action='store_true', help='poll once and exit')
    parser.add_argument('--dry-run', metavar='DIR',
                        help='write what would be sent to DIR as .eml files; send and record nothing (implies --once)')
    parser.add_argument('--status', action='store_true', help='show recently forwarded mail and exit')
    parser.add_argument('--healthcheck', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    args = parser.parse_args(argv)

    try:
        cfg = load()
    except ConfigError as exc:
        print(f'Configuration error: {exc}', file=sys.stderr)
        return 2

    if args.healthcheck:
        return _healthcheck(cfg)
    if args.status:
        return _status(cfg)

    _setup_logging(cfg.log_level)
    once = args.once or bool(args.dry_run)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    store = Store(cfg.db_path)
    client = PartageClient(cfg)
    forwarder = Forwarder(cfg, client, store, dry_run_dir=args.dry_run)
    log.info('partage-forwarder %s: %s -> %s, polling "%s" every %ds',
             __version__, cfg.username, cfg.forward_to, cfg.query, cfg.poll_interval)

    failures = 0
    try:
        while not stop.is_set():
            try:
                result = forwarder.run_cycle()
                failures = 0
                if not args.dry_run:
                    store.touch()
                if result.forwarded or result.failed:
                    log.info('Poll done: %d forwarded, %d failed', result.forwarded, result.failed)
                else:
                    log.debug('Poll done: nothing new')
                delay = cfg.poll_interval
            except BadCredentials as exc:
                log.error('%s. Pausing %d hours to avoid locking the account; '
                          'fix .env and restart to retry sooner.', exc, BAD_CREDENTIALS_PAUSE // 3600)
                failures += 1
                delay = BAD_CREDENTIALS_PAUSE
            except Exception as exc:
                failures += 1
                delay = min(cfg.poll_interval * 2 ** (failures - 1), MAX_BACKOFF)
                # The full traceback only helps the first time; after that the
                # same outage would fill the log with identical stacks.
                log.error('Poll failed (%s: %s); retrying in %ds', type(exc).__name__, exc, delay,
                          exc_info=failures == 1)
                client.close()  # start the next attempt from a clean login

            if once:
                return 0 if failures == 0 else 1
            stop.wait(delay)
    finally:
        client.close()
        store.close()
    log.info('Stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
