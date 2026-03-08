import sqlite3


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS forwarded ("
        "  msg_id TEXT PRIMARY KEY,"
        "  forwarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.commit()
    return conn


def get_known_ids(db_path: str) -> set:
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT msg_id FROM forwarded").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def mark_forwarded(db_path: str, msg_id: str):
    conn = _connect(db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO forwarded (msg_id) VALUES (?)", (msg_id,))
        conn.commit()
    finally:
        conn.close()
