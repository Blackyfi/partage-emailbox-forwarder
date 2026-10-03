"""One poll of the mailbox: find new messages, forward them, remember them."""
import logging
import smtplib
import time
from dataclasses import dataclass
from pathlib import Path

from .mailer import Mailer, build_failure_notice, build_message
from .partage import MessageGone, PartageClient, Summary
from .rawmail import parse
from .store import FAILED, FORWARDED, SEEDED, Store

log = logging.getLogger(__name__)

# On the very first run, unread mail younger than this is forwarded; anything
# older (or already read) is taken as handled, so a fresh install does not
# flood the destination with a backlog.
BACKLOG_SECONDS = 7 * 24 * 3600
# The REST search ignores 'limit' and returns the whole folder (megabytes for
# a few thousand mails), so the query is narrowed to recent days instead,
# widened as needed to cover however long the forwarder was down.
MIN_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 60


class Transient(Exception):
    """A failure of the connection rather than of one message: stop the batch
    and retry next cycle."""


@dataclass
class CycleResult:
    forwarded: int = 0
    failed: int = 0


def key(summary: Summary) -> str:
    return f'msg:{summary.id}'


def _is_transient(exc: Exception) -> bool:
    # Gmail answers a rejected message with a 5xx and a rate limit or hiccup
    # with a 4xx. Only the former is a property of the message itself.
    if isinstance(exc, (smtplib.SMTPDataError, smtplib.SMTPSenderRefused)):
        return exc.smtp_code < 500
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return False
    # Network trouble, SMTP login failures, HTTP 5xx from Partage... all of
    # these are OSError subclasses (requests and smtplib errors included).
    return isinstance(exc, OSError)


class Forwarder:
    def __init__(self, cfg, client: PartageClient, store: Store, dry_run_dir: str | None = None):
        self.cfg = cfg
        self.client = client
        self.store = store
        self.dry_run_dir = Path(dry_run_dir) if dry_run_dir else None
        self._dry_known = set()
        self._seeded = False

    def _known(self) -> set:
        return self.store.known_ids() | self._dry_known

    def _mark(self, summary: Summary, status: str, subject: str = '', sender: str = ''):
        if self.dry_run_dir:
            self._dry_known.add(key(summary))
        else:
            self.store.mark(key(summary), status, subject or summary.subject, sender or summary.sender)

    def _seed(self, summaries: list):
        """First run: decide what counts as already handled.

        Messages the previous (browser-based) version forwarded are recorded
        under their conversation id, '-<message id>' for single-message
        conversations, so those are recognised as well.
        """
        known = self._known()
        now = time.time()
        to_forward = 0
        for s in summaries:
            legacy = s.conversation_id in known or f'-{s.id}' in known
            fresh = s.unread and now - s.received < BACKLOG_SECONDS
            if legacy or not fresh:
                self._mark(s, SEEDED)
            else:
                to_forward += 1
        if not self.dry_run_dir:
            self.store.set_meta('seeded_at', now)
        log.info('First run: %d message(s) already in the mailbox were marked as seen; '
                 '%d recent unread one(s) will be forwarded',
                 len(summaries) - to_forward, to_forward)

    def _query(self) -> str:
        last = float(self.store.get_meta('last_success', 0)) or time.time()
        days = int((time.time() - last) // 86400) + MIN_WINDOW_DAYS
        return f'({self.cfg.query}) after:-{min(days, MAX_WINDOW_DAYS)}day'

    def run_cycle(self) -> CycleResult:
        summaries = self.client.search(self._query())
        if not self._seeded and self.store.get_meta('seeded_at') is None:
            self._seed(summaries)
        self._seeded = True

        known = self._known()
        pending = [s for s in summaries if key(s) not in known]
        result = CycleResult()
        if not pending:
            return result

        with Mailer(self.cfg) as mailer:
            if not self.dry_run_dir:
                # Connect up front: a wrong app password or an unreachable
                # Gmail is nobody's fault but the setup's, and must not be
                # counted against (and eventually drop) the first message.
                try:
                    mailer.connect()
                except Exception as exc:
                    raise Transient(f'Cannot connect to Gmail: {type(exc).__name__}: {exc}') from exc
            for summary in pending:
                try:
                    self._forward_one(summary, mailer)
                    result.forwarded += 1
                except MessageGone:
                    log.info('Message %s disappeared before it could be fetched; skipping', summary.id)
                except Exception as exc:
                    # Counted even when it looks like a network blip, so one
                    # message that reliably breaks the connection cannot
                    # hold up everything behind it forever.
                    result.failed += 1
                    self._record_failure(summary, exc, mailer)
                    if _is_transient(exc):
                        raise Transient(f'{type(exc).__name__}: {exc}') from exc
        return result

    def _forward_one(self, summary: Summary, mailer: Mailer):
        mail = parse(self.client.fetch_raw(summary.id), key(summary))
        msg = build_message(mail, self.cfg)
        if self.dry_run_dir:
            self.dry_run_dir.mkdir(parents=True, exist_ok=True)
            path = self.dry_run_dir / f'{summary.id}.eml'
            path.write_bytes(bytes(msg))
            log.info('[dry run] Would forward "%s" from %s -> %s', mail.subject, mail.sender, path)
        else:
            mailer.send(msg)
            log.info('Forwarded "%s" from %s', mail.subject, mail.sender)
        self._mark(summary, FORWARDED, mail.subject, mail.sender)

    def _record_failure(self, summary: Summary, exc: Exception, mailer: Mailer):
        label = f'message {summary.id} ("{summary.subject}" from {summary.sender})'
        if self.dry_run_dir:
            log.error('Could not forward %s', label, exc_info=exc)
            return
        attempts = self.store.record_failure(key(summary), f'{type(exc).__name__}: {exc}')
        if attempts >= self.cfg.max_attempts:
            self.store.mark(key(summary), FAILED, summary.subject, summary.sender)
            log.error('Giving up on %s after %d attempts', label, attempts, exc_info=exc)
            # Rather than lose the mail silently, at least say it exists.
            try:
                mailer.send(build_failure_notice(summary, exc, self.cfg))
            except Exception:
                log.warning('Could not send the failure notice either', exc_info=True)
        else:
            log.warning('Could not forward %s (attempt %d/%d, will retry)',
                        label, attempts, self.cfg.max_attempts, exc_info=exc)
