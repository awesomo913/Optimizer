"""Persistent SQLite log of every scan, recommendation, action, model state,
and error. This is the knowledge base we grow over time to improve decisions."""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    mode TEXT,
    foreground_window TEXT,
    active_windows TEXT,        -- json list of active program names
    process_count INTEGER,
    reasoner TEXT,              -- which engine produced recommendations
    notes TEXT
);

CREATE TABLE IF NOT EXISTS process_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    pid INTEGER,
    name TEXT,
    exe TEXT,
    cpu REAL,
    mem_mb REAL,
    status TEXT,
    is_active INTEGER,          -- tied to an active window
    uses_local_model INTEGER,
    protected INTEGER,
    recommendation TEXT,        -- needed | suggest_close | suspend | kill
    confidence REAL,
    reason TEXT,
    action_taken TEXT,          -- none | suggested | suspended | killed | failed
    FOREIGN KEY (scan_id) REFERENCES scans(id)
);

CREATE TABLE IF NOT EXISTS model_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    provider TEXT,              -- ollama | lmstudio
    model TEXT,
    available INTEGER,          -- server reachable / model listed
    can_spawn INTEGER,          -- could we launch it later if needed
    in_use INTEGER,             -- a running process is connected to it
    note TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    level TEXT,                 -- info | suggestion | warning | error
    category TEXT,              -- scan | model | action | api | system
    message TEXT,
    detail TEXT
);
"""


class Database:
    def __init__(self, path: str | None = None) -> None:
        config._ensure_data_dir()
        self.path = str(path or config.DB_PATH)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- writes -------------------------------------------------------------
    def start_scan(self, mode: str, foreground: str, active: list[str],
                   process_count: int, reasoner: str, notes: str = "") -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO scans (ts, mode, foreground_window, active_windows, "
                "process_count, reasoner, notes) VALUES (?,?,?,?,?,?,?)",
                (time.time(), mode, foreground, json.dumps(active),
                 process_count, reasoner, notes),
            )
            return cur.lastrowid

    def add_process_snapshot(self, scan_id: int, row: dict[str, Any]) -> int:
        """Insert one process snapshot row, returning its new row id so the
        caller can later update `action_taken` once an action is applied."""
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO process_snapshots (scan_id, pid, name, exe, cpu, mem_mb, "
                "status, is_active, uses_local_model, protected, recommendation, "
                "confidence, reason, action_taken) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    scan_id, row.get("pid"), row.get("name"), row.get("exe"),
                    row.get("cpu"), row.get("mem_mb"), row.get("status"),
                    int(bool(row.get("is_active"))),
                    int(bool(row.get("uses_local_model"))),
                    int(bool(row.get("protected"))),
                    row.get("recommendation"), row.get("confidence"),
                    row.get("reason"), row.get("action_taken", "none"),
                ),
            )
            return cur.lastrowid

    def update_action(self, snapshot_id: int, action: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE process_snapshots SET action_taken=? WHERE id=?",
                      (action, snapshot_id))

    def log_model_status(self, provider: str, model: str, available: bool,
                         can_spawn: bool, in_use: bool, note: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO model_status (ts, provider, model, available, can_spawn, "
                "in_use, note) VALUES (?,?,?,?,?,?,?)",
                (time.time(), provider, model, int(available), int(can_spawn),
                 int(in_use), note),
            )

    def log_event(self, level: str, category: str, message: str,
                  detail: Any = None) -> None:
        if detail is not None and not isinstance(detail, str):
            detail = json.dumps(detail, default=str)
        with self._conn() as c:
            c.execute(
                "INSERT INTO events (ts, level, category, message, detail) "
                "VALUES (?,?,?,?,?)",
                (time.time(), level, category, message, detail),
            )

    # ---- reads --------------------------------------------------------------
    def recent_events(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._conn() as c:
            return list(c.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)))

    def stats(self) -> dict[str, int]:
        with self._conn() as c:
            def n(q: str) -> int:
                return c.execute(q).fetchone()[0]
            return {
                "scans": n("SELECT COUNT(*) FROM scans"),
                "snapshots": n("SELECT COUNT(*) FROM process_snapshots"),
                "events": n("SELECT COUNT(*) FROM events"),
                "errors": n("SELECT COUNT(*) FROM events WHERE level='error'"),
                "killed": n("SELECT COUNT(*) FROM process_snapshots "
                           "WHERE action_taken='killed'"),
                "suspended": n("SELECT COUNT(*) FROM process_snapshots "
                              "WHERE action_taken='suspended'"),
            }
