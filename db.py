import contextlib
import sqlite3

from config import DB_PATH


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def init_db():
    with contextlib.closing(get_db()) as con:
        con.execute("PRAGMA journal_mode = WAL;")
        con.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                root_url    TEXT NOT NULL,
                created     TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS annotations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                url         TEXT NOT NULL,
                quote       TEXT NOT NULL,
                comment     TEXT NOT NULL,
                prefix      TEXT DEFAULT '',
                suffix      TEXT DEFAULT '',
                author      TEXT DEFAULT 'anonymous',
                created     TEXT NOT NULL,
                text_start  INTEGER DEFAULT -1,
                text_end    INTEGER DEFAULT -1,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
        """)
        con.commit()


init_db()
