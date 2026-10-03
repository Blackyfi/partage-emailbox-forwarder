import smtplib
import time
from email import policy
from email.parser import BytesParser

import pytest

from partage_forwarder import service
from partage_forwarder.partage import MessageGone, Summary
from partage_forwarder.service import Forwarder, Transient
from partage_forwarder.store import Store


def raw(subject):
    return f'From: Sarah <s@x.fr>\r\nSubject: {subject}\r\n\r\nbody of {subject}\r\n'.encode()


class FakeClient:
    def __init__(self, summaries):
        self.summaries = summaries
        self.broken = {}
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return list(self.summaries)

    def fetch_raw(self, msg_id):
        if msg_id in self.broken:
            raise self.broken[msg_id]
        return raw(f'subject {msg_id}')


class FakeMailer:
    sent = []
    fail_connect = None
    fail_send = {}

    def __init__(self, cfg):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def connect(self):
        if FakeMailer.fail_connect:
            raise FakeMailer.fail_connect

    def send(self, msg):
        for needle, exc in FakeMailer.fail_send.items():
            if needle in str(msg['Subject']):
                raise exc
        FakeMailer.sent.append(msg)


@pytest.fixture(autouse=True)
def fake_mailer(monkeypatch):
    FakeMailer.sent, FakeMailer.fail_connect, FakeMailer.fail_send = [], None, {}
    monkeypatch.setattr(service, 'Mailer', FakeMailer)


def summary(i, unread=True, age=60, cid=None):
    return Summary(id=str(i), conversation_id=cid or f'-{i}', received=time.time() - age,
                   unread=unread, subject=f'subject {i}', sender='Sarah')


def subjects():
    return [str(m['Subject']) for m in FakeMailer.sent]


def test_first_run_forwards_only_recent_unread_mail(cfg):
    store = Store(':memory:')
    store.mark('-3')  # forwarded by the old browser-based version
    client = FakeClient([
        summary(1, unread=False),           # already read
        summary(2, age=30 * 24 * 3600),     # unread but a month old
        summary(3),                         # legacy-forwarded
        summary(4),                         # genuinely new
    ])
    Forwarder(cfg, client, store).run_cycle()
    assert subjects() == ['[Partage] subject 4']

    # Later, anything new is forwarded, read or not.
    client.summaries.append(summary(5, unread=False))
    FakeMailer.sent.clear()
    Forwarder(cfg, client, store).run_cycle()
    assert subjects() == ['[Partage] subject 5']


def test_nothing_is_forwarded_twice(cfg):
    store = Store(':memory:')
    client = FakeClient([summary(1)])
    fwd = Forwarder(cfg, client, store)
    assert fwd.run_cycle().forwarded == 1
    assert fwd.run_cycle().forwarded == 0
    assert len(FakeMailer.sent) == 1


def test_broken_message_is_retried_then_given_up_with_a_notice(cfg):
    store = Store(':memory:')
    client = FakeClient([summary(1), summary(2)])
    client.broken['1'] = ValueError('unparseable')
    fwd = Forwarder(cfg, client, store)
    for attempt in range(cfg.max_attempts):
        result = fwd.run_cycle()
        assert result.failed == 1
    # Message 2 went through on the first cycle despite message 1.
    assert subjects()[0] == '[Partage] subject 2'
    assert subjects()[-1] == '[Partage] Could not forward: subject 1'
    assert fwd.run_cycle().failed == 0  # given up: no further attempts


def test_gmail_login_failure_is_not_held_against_messages(cfg):
    store = Store(':memory:')
    FakeMailer.fail_connect = smtplib.SMTPAuthenticationError(535, b'bad app password')
    fwd = Forwarder(cfg, FakeClient([summary(1)]), store)
    for _ in range(cfg.max_attempts + 2):
        with pytest.raises(Transient):
            fwd.run_cycle()
    FakeMailer.fail_connect = None
    assert fwd.run_cycle().forwarded == 1


def test_temporary_smtp_error_stops_the_batch(cfg):
    store = Store(':memory:')
    FakeMailer.fail_send = {'subject 1': smtplib.SMTPDataError(451, b'try later')}
    fwd = Forwarder(cfg, FakeClient([summary(1), summary(2)]), store)
    with pytest.raises(Transient):
        fwd.run_cycle()
    assert subjects() == []
    FakeMailer.fail_send = {}
    assert fwd.run_cycle().forwarded == 2


def test_vanished_message_is_skipped(cfg):
    store = Store(':memory:')
    client = FakeClient([summary(1), summary(2)])
    client.broken['1'] = MessageGone('1')
    assert Forwarder(cfg, client, store).run_cycle().forwarded == 1


def test_dry_run_writes_files_and_records_nothing(cfg, tmp_path):
    store = Store(':memory:')
    fwd = Forwarder(cfg, FakeClient([summary(1)]), store, dry_run_dir=str(tmp_path))
    assert fwd.run_cycle().forwarded == 1
    msg = BytesParser(policy=policy.default).parsebytes((tmp_path / '1.eml').read_bytes())
    assert 'body of subject 1' in msg.get_body(('plain',)).get_content()
    assert 'body of subject 1' in msg.get_body(('html',)).get_content()
    assert FakeMailer.sent == []
    assert store.known_ids() == set() and store.get_meta('seeded_at') is None


def test_search_window_covers_downtime(cfg):
    store = Store(':memory:')
    client = FakeClient([])
    fwd = Forwarder(cfg, client, store)
    fwd.run_cycle()
    store.set_meta('last_success', time.time() - 20 * 86400)
    fwd.run_cycle()
    store.set_meta('last_success', time.time() - 365 * 86400)
    fwd.run_cycle()
    assert client.queries == [
        '(in:inbox) after:-7day', '(in:inbox) after:-27day', '(in:inbox) after:-60day']
