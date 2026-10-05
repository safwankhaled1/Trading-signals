from __future__ import annotations

import json
import hashlib
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.account = None
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS signals (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
              channel INTEGER, message INTEGER, text TEXT, reply INTEGER, signal TEXT,
              received REAL, PRIMARY KEY(channel,message));
            CREATE TABLE IF NOT EXISTS revisions (
              id INTEGER PRIMARY KEY, channel INTEGER, message INTEGER, text TEXT, time REAL);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY, time REAL, level TEXT, message TEXT, signal TEXT, details TEXT);
            CREATE TABLE IF NOT EXISTS actions (
              id TEXT PRIMARY KEY, signal TEXT, kind TEXT, state TEXT, time REAL, data TEXT);
            CREATE TABLE IF NOT EXISTS deals (
              id TEXT PRIMARY KEY, data TEXT NOT NULL);
        """)
        self.db.commit()
        self._migrate_accounts()

    def _migrate_accounts(self):
        message_columns = {r[1] for r in self.db.execute("PRAGMA table_info(messages)")}
        event_columns = {r[1] for r in self.db.execute("PRAGMA table_info(events)")}
        if "account" in message_columns and "account" in event_columns:
            return
        populated = self.db.execute("SELECT COUNT(*) FROM signals").fetchone()[0] or self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        backup_path = Path(self.path + ".pre-account-isolation.sqlite")
        if populated and not backup_path.exists():
            backup = sqlite3.connect(str(backup_path))
            try:
                self.db.backup(backup)
            finally:
                backup.close()
        accounts = {s["id"]: s.get("account", "") for s in self.signals()}
        with self.db:
            if "account" not in event_columns:
                self.db.execute("ALTER TABLE events ADD COLUMN account TEXT NOT NULL DEFAULT ''")
                current = ""
                for row in self.db.execute("SELECT id,signal,details FROM events ORDER BY id").fetchall():
                    details = json.loads(row["details"])
                    if details.get("account"):
                        current = details["account"]
                    owner = accounts.get(row["signal"], current)
                    self.db.execute("UPDATE events SET account=? WHERE id=?", (owner, row["id"]))
            if "account" not in message_columns:
                self.db.execute("ALTER TABLE messages RENAME TO messages_legacy")
                self.db.execute("CREATE TABLE messages (account TEXT NOT NULL,channel INTEGER,message INTEGER,text TEXT,reply INTEGER,signal TEXT,received REAL,PRIMARY KEY(account,channel,message))")
                for row in self.db.execute("SELECT * FROM messages_legacy").fetchall():
                    self.db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                                    (accounts.get(row["signal"], ""), row["channel"], row["message"], row["text"], row["reply"], row["signal"], row["received"]))
            revision_columns = {r[1] for r in self.db.execute("PRAGMA table_info(revisions)")}
            if "account" not in revision_columns:
                self.db.execute("ALTER TABLE revisions ADD COLUMN account TEXT NOT NULL DEFAULT ''")
            self.db.execute("CREATE INDEX IF NOT EXISTS events_account ON events(account,id)")

    def activate_account(self, account):
        self.account = account
        if not self.get("legacy_account"):
            self.set("legacy_account", self.get("last_account") or account)
        if account != self.get("legacy_account") and self.get("settings") is None:
            known = sorted(self.signals(), key=lambda s: s.get("created", 0))
            if known:
                self.set("settings", known[-1]["config"])
                for s in known:
                    if s.get("channel"):
                        self.set(f"profile:{s['channel']}", s["config"])

    def _key(self, key):
        if self.account and (key in {"settings", "paused", "inbox"} or key.startswith("profile:")):
            token = hashlib.sha256(self.account.encode("utf-8")).hexdigest()[:24]
            return f"account:{token}:{key}"
        return key

    def get(self, key, default=None):
        scoped = self._key(key)
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (scoped,)).fetchone()
        if not row and scoped != key and self.account == self.get("legacy_account"):
            row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (self._key(key), json.dumps(value, ensure_ascii=False)))

    def save_signal(self, signal):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO signals VALUES (?,?)", (signal["id"], json.dumps(signal, ensure_ascii=False)))

    def signals(self):
        query = "SELECT data FROM signals"
        return [json.loads(r[0]) for r in self.db.execute(query + " WHERE json_extract(data,'$.account')=?", (self.account,))] if self.account else [json.loads(r[0]) for r in self.db.execute(query)]

    def message(self, channel, message):
        row = self.db.execute("SELECT * FROM messages WHERE account=? AND channel=? AND message=?", (self.account or "", channel, message)).fetchone()
        return dict(row) if row else None

    def latest_messages(self, channel, limit=20):
        return [dict(row) for row in self.db.execute("SELECT * FROM messages WHERE account=? AND channel=? ORDER BY received DESC,message DESC LIMIT ?", (self.account or "", channel, limit))]

    def save_message(self, channel, message, text, reply, signal, received, edited=False):
        with self.db:
            if edited:
                self.db.execute("INSERT INTO revisions(channel,message,text,time,account) VALUES (?,?,?,?,?)", (channel, message, text, received, self.account or ""))
            self.db.execute("INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?)", (self.account or "", channel, message, text, reply, signal, received))

    def event(self, message, signal="", level="info", **details):
        with self.db:
            self.db.execute("INSERT INTO events(time,level,message,signal,details,account) VALUES (?,?,?,?,?,?)",
                            (time.time(), level, message, signal, json.dumps(details, ensure_ascii=False), self.account or details.get("account", "")))

    def events(self, limit=150):
        if self.account:
            return [dict(r) for r in self.db.execute("SELECT * FROM events WHERE account=? ORDER BY id DESC LIMIT ?", (self.account, limit))]
        return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    def claim(self, action, signal, kind, data):
        try:
            with self.db:
                self.db.execute("INSERT INTO actions VALUES (?,?,?,?,?,?)", (action, signal, kind, "sending", time.time(), json.dumps(data)))
            return True
        except sqlite3.IntegrityError:
            return False

    def action(self, action):
        row = self.db.execute("SELECT * FROM actions WHERE id=?", (action,)).fetchone()
        return dict(row) if row else None

    def finish_action(self, action, state, data):
        with self.db:
            self.db.execute("UPDATE actions SET state=?,data=? WHERE id=?", (state, json.dumps(data), action))

    def unresolved(self):
        if self.account:
            return [dict(r) for r in self.db.execute("SELECT a.* FROM actions a JOIN signals s ON a.signal=s.id WHERE a.state IN ('sending','uncertain') AND json_extract(s.data,'$.account')=?", (self.account,))]
        return [dict(r) for r in self.db.execute("SELECT * FROM actions WHERE state IN ('sending','uncertain')")]

    def save_deals(self, deals):
        with self.db:
            for deal in deals:
                self.db.execute("INSERT OR REPLACE INTO deals VALUES (?,?)", (str(deal["id"]), json.dumps(deal)))

    def deals(self):
        deals = [json.loads(r[0]) for r in self.db.execute("SELECT data FROM deals")]
        if self.account:
            known = {s["id"] for s in self.signals()}
            deals = [d for d in deals if d.get("account") == self.account or (not d.get("account") and (d.get("signal") in known or str(d["id"]).rsplit(":", 1)[0] == self.account))]
        return deals

    def close(self):
        self.db.close()
