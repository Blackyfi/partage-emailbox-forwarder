"""What has been forwarded already, plus a little state, in SQLite."""
import sqlite3
import time
from pathlib import Path

FORWARDED = 'forwarded'  # sent to Gmail
SEEDED = 'seeded'        # already in the mailbox when the forwarder first ran
FAILED = 'failed'        # gave up after too many attempts

SCHEMA = """
CREATE TABLE IF NOT EXISTS forwarded (
    msg_id       TEXT PRIMARY KEY,
    forwarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS failures (
    msg_id       TEXT PRIMARY KEY,
    attempts     INTEGER NOT NULL,
    last_error   TEXT,
    last_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""
# Columns added after the first release; existing databases get them on open.
EXTRA_COLUMNS = {
    'status': "TEXT NOT NULL DEFAULT 'forwarded'",
    'subject': 'TEXT',
    'sender': 'TEXT',
}


class Store:
    def __init__(self, path: str):
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)  # autocommit
        self._db.executescript(SCHEMA)
        have = {row[1] for row in self._db.execute('PRAGMA table_info(forwarded)')}
        for name, decl in EXTRA_COLUMNS.items():
            if name not in have:
                self._db.execute(f'ALTER TABLE forwarded ADD COLUMN {name} {decl}')

    def close(self):
        self._db.close()

    def known_ids(self) -> set:
        return {r[0] for r in self._db.execute('SELECT msg_id FROM forwarded')}

    def mark(self, msg_id: str, status: str = FORWARDED, subject: str = '', sender: str = ''):
        self._db.execute(
            'INSERT OR REPLACE INTO forwarded (msg_id, status, subject, sender) VALUES (?, ?, ?, ?)',
            (msg_id, status, subject, sender))
        self._db.execute('DELETE FROM failures WHERE msg_id = ?', (msg_id,))

    def record_failure(self, msg_id: str, error: str) -> int:
        """Count one more failed attempt; returns the total so far."""
        self._db.execute(
            'INSERT INTO failures (msg_id, attempts, last_error) VALUES (?, 1, ?) '
            'ON CONFLICT(msg_id) DO UPDATE SET attempts = attempts + 1, '
            'last_error = excluded.last_error, last_attempt = CURRENT_TIMESTAMP',
            (msg_id, error[:2000]))
        return self._db.execute(
            'SELECT attempts FROM failures WHERE msg_id = ?', (msg_id,)).fetchone()[0]

    def get_meta(self, key: str, default=None):
        row = self._db.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value):
        self._db.execute('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)', (key, str(value)))

    def touch(self):
        """Record a successful poll, for the container health check."""
        self.set_meta('last_success', time.time())

    def recent(self, limit: int = 20) -> list:
        return self._db.execute(
            "SELECT forwarded_at, status, sender, subject FROM forwarded "
            "WHERE status != 'seeded' ORDER BY forwarded_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
