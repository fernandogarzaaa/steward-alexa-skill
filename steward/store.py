"""SQLite-backed local storage for Steward: calendar events, reminders, notes, prefs.

Everything lives in one SQLite file so the MCP server stays dependency-free
beyond the standard library. The default location is ~/.steward/steward.db;
override with the STEWARD_DB environment variable (tests use a temp file).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS prefs (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def default_db_path() -> str:
    return os.environ.get(
        "STEWARD_DB", str(Path.home() / ".steward" / "steward.db")
    )


class Store:
    """Thread-safe wrapper around the Steward SQLite database."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_db_path()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def _q(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            self._conn.commit()
            return rows

    def _exec(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid

    # Calendar
    def calendar_add(self, title: str, date: str, start_time: str = "",
                     notes: str = "") -> dict:
        eid = self._exec(
            "INSERT INTO events (title, date, start_time, notes) VALUES (?,?,?,?)",
            (title, date, start_time, notes),
        )
        return {"id": eid, "title": title, "date": date,
                "start_time": start_time, "notes": notes}

    def calendar_list(self, date: str = "") -> list[dict]:
        if date:
            return self._q("SELECT * FROM events WHERE date = ? ORDER BY start_time",
                           (date,))
        return self._q("SELECT * FROM events ORDER BY date, start_time")

    def calendar_delete(self, event_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM events WHERE id = ?",
                                     (event_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # Reminders
    def reminder_add(self, text: str, due: str = "") -> dict:
        rid = self._exec("INSERT INTO reminders (text, due) VALUES (?,?)",
                         (text, due))
        return {"id": rid, "text": text, "due": due, "done": 0}

    def reminder_list(self, include_done: bool = False) -> list[dict]:
        if include_done:
            return self._q("SELECT * FROM reminders ORDER BY id")
        return self._q("SELECT * FROM reminders WHERE done = 0 ORDER BY id")

    def reminder_done(self, reminder_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reminders SET done = 1 WHERE id = ?", (reminder_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # Notes
    def note_save(self, title: str, body: str, tags: str = "") -> dict:
        nid = self._exec(
            "INSERT INTO notes (title, body, tags) VALUES (?,?,?)",
            (title, body, tags),
        )
        return {"id": nid, "title": title, "tags": tags}

    def note_search(self, query: str) -> list[dict]:
        like = "%" + query + "%"
        return self._q(
            "SELECT id, title, tags, substr(body, 1, 280) AS snippet FROM notes"
            " WHERE title LIKE ? OR body LIKE ? OR tags LIKE ? ORDER BY id DESC",
            (like, like, like),
        )

    def note_get(self, note_id: int) -> dict | None:
        rows = self._q("SELECT * FROM notes WHERE id = ?", (note_id,))
        return rows[0] if rows else None

    # Prefs (cross-session memory)
    def prefs_set(self, key: str, value: str) -> dict:
        self._exec(
            "INSERT INTO prefs (key, value, updated_at) VALUES (?,?,datetime('now'))"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
            " updated_at=datetime('now')",
            (key, value),
        )
        return {"key": key, "value": value}

    def prefs_get(self, key: str) -> dict:
        rows = self._q("SELECT key, value FROM prefs WHERE key = ?", (key,))
        if rows:
            return rows[0]
        return {"key": key, "value": ""}

    def prefs_all(self) -> list[dict]:
        return self._q("SELECT key, value FROM prefs ORDER BY key")

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# Namespaces come from an HTTP header, so they also end up in a file name:
# accept only short url-safe tokens (what secrets.token_urlsafe produces).
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def valid_namespace(ns: str | None) -> bool:
    return bool(ns) and bool(_NAMESPACE_RE.match(ns))


class StorePool:
    """One Store per namespace, for the hosted multi-visitor demo.

    The default namespace (None or "") is the classic single-user store at
    ``base_path``, so local use is unchanged. Any valid namespace gets its
    own SQLite file next to it: ``<base>.ns-<namespace>.db``. ``reset()``
    drops every namespaced store (files included) and leaves the default
    store alone; the hosted demo calls it on a timer.
    """

    def __init__(self, base_path: str | None = None) -> None:
        self.base_path = base_path or default_db_path()
        self._lock = threading.Lock()
        self._default: Store | None = None
        self._stores: dict[str, Store] = {}

    def _ns_path(self, ns: str) -> str:
        if self.base_path == ":memory:":
            return ":memory:"
        base = Path(self.base_path)
        return str(base.with_name(f"{base.stem}.ns-{ns}{base.suffix or '.db'}"))

    def get(self, ns: str | None = None) -> Store:
        with self._lock:
            if not ns:
                if self._default is None:
                    self._default = Store(self.base_path)
                return self._default
            if not valid_namespace(ns):
                raise ValueError("invalid store namespace")
            store = self._stores.get(ns)
            if store is None:
                store = self._stores[ns] = Store(self._ns_path(ns))
            return store

    def namespaces(self) -> list[str]:
        with self._lock:
            return sorted(self._stores)

    def reset(self) -> int:
        """Close and delete every namespaced store. Returns how many went."""
        with self._lock:
            old, self._stores = self._stores, {}
        for store in old.values():
            store.close()
        removed = 0
        if self.base_path != ":memory:":
            base = Path(self.base_path)
            pattern = f"{base.stem}.ns-*{base.suffix or '.db'}"
            for f in base.parent.glob(pattern):
                for extra in (f, Path(str(f) + "-journal"),
                              Path(str(f) + "-wal"), Path(str(f) + "-shm")):
                    try:
                        extra.unlink()
                    except FileNotFoundError:
                        pass
                removed += 1
        return max(removed, len(old))
