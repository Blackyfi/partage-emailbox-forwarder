import sqlite3

from partage_forwarder.store import FAILED, Store


def test_legacy_database_is_migrated(tmp_path):
    path = tmp_path / 'emails.db'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE forwarded (msg_id TEXT PRIMARY KEY, forwarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    db.execute("INSERT INTO forwarded (msg_id) VALUES ('-12464')")
    db.commit()
    db.close()

    store = Store(str(path))
    assert store.known_ids() == {'-12464'}
    store.mark('msg:1', subject='Hello', sender='Sarah')
    assert store.recent()[0][2:] == ('Sarah', 'Hello')


def test_failures_count_up_and_clear_on_success():
    store = Store(':memory:')
    assert store.record_failure('msg:1', 'boom') == 1
    assert store.record_failure('msg:1', 'boom') == 2
    store.mark('msg:1', FAILED)
    assert store.record_failure('msg:1', 'boom') == 1
