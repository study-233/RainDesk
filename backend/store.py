import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

import win32crypt

PORT = 17846
DATA = Path(os.environ["ProgramData"]) / "RainDesk"


def pack(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, root=DATA):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "classroom.sqlite", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=4000;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS vault (key TEXT PRIMARY KEY, value BLOB);
            CREATE TABLE IF NOT EXISTS jobs (
                owner TEXT, lesson TEXT, problem TEXT, created REAL, payload TEXT, deadline REAL,
                state TEXT, answer TEXT, reason TEXT DEFAULT '', ms INTEGER, detail TEXT DEFAULT '',
                PRIMARY KEY(owner,lesson,problem));
            CREATE TABLE IF NOT EXISTS decks (
                owner TEXT, lesson TEXT, id TEXT, title TEXT, slides TEXT, updated REAL,
                PRIMARY KEY(owner,lesson,id));
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at REAL, level TEXT, message TEXT, notify INTEGER);
        """)
        if "event_key" not in {row[1] for row in self.db.execute("PRAGMA table_info(events)")}:
            self.db.execute("ALTER TABLE events ADD COLUMN event_key TEXT")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS once_per_event ON events(event_key)")
        self.db.execute("UPDATE jobs SET state='uncertain',detail='服务恢复，请核对课堂回执' WHERE state='sending'")
        self.db.execute("UPDATE jobs SET state='queued' WHERE state='thinking'")
        tokenfile = root / "control.token"
        if not tokenfile.exists():
            tmp = tokenfile.with_suffix(".tmp")
            tmp.write_text(secrets.token_urlsafe(48), encoding="ascii")
            tmp.replace(tokenfile)
        self.token = tokenfile.read_text(encoding="ascii").strip()

    def get(self, key, default=None, secret=False):
        table = "vault" if secret else "settings"
        row = self.db.execute(f"SELECT value FROM {table} WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        value = win32crypt.CryptUnprotectData(row[0], None, None, None, 1)[1] if secret else row[0]
        return json.loads(value)

    def put(self, key, value, secret=False):
        if self.get(key,None,secret) == value:
            return
        table = "vault" if secret else "settings"
        data = pack(value)
        if secret:
            data = win32crypt.CryptProtectData(data.encode(), "RainDesk", None, None, None, 1)
        self.db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?)", (key, data))

    def log(self, message, level="info", notify=False, event_key=None):
        self.db.execute("INSERT OR IGNORE INTO events(at,level,message,notify,event_key) VALUES (?,?,?,?,?)",
                        (time.time(), level, message, notify, event_key))
        self.db.execute("DELETE FROM events WHERE id <= (SELECT max(id)-2000 FROM events)")

    def rows(self, sql, args=()):
        return [dict(row) for row in self.db.execute(sql, args)]

    def job(self, key):
        return self.db.execute("SELECT * FROM jobs WHERE owner=? AND lesson=? AND problem=?", key).fetchone()

    def state(self, key, status, detail=""):
        self.db.execute("UPDATE jobs SET state=?,detail=? WHERE owner=? AND lesson=? AND problem=?", (status, detail, *key))
