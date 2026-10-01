"""Persistent SQLite log of every scan, recommendation, action, model state,
and error. This is the knowledge base we grow over time to improve decisions."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import config

logger = logging.getLogger(__name__)

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

-- Processes the app has suspended and not yet resumed. Persisted (not just
-- in-memory) so "Resume Suspended" still works after the app is closed and
-- reopened. create_time is what lets us tell a still-suspended process apart
-- from a different process that was later assigned the same pid.
CREATE TABLE IF NOT EXISTS suspended_processes (
    pid INTEGER PRIMARY KEY,
    name TEXT,
    create_time REAL,
    ts REAL NOT NULL
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
    # Per project convention, a DB hiccup (locked file, disk full, schema
    # drift) must never propagate out of Database and crash the GUI or abort
    # a scan/action in progress — the database is a log we grow over time,
    # not something the app's core safety behavior depends on. Every write
    # method below catches broadly, logs a warning, and returns a safe
    # fallback (None) instead of raising.
    def start_scan(self, mode: str, foreground: str, active: list[str],
                   process_count: int, reasoner: str, notes: str = "") -> int | None:
        try:
            with self._conn() as c:
                cur = c.execute(
                    "INSERT INTO scans (ts, mode, foreground_window, active_windows, "
                    "process_count, reasoner, notes) VALUES (?,?,?,?,?,?,?)",
                    (time.time(), mode, foreground, json.dumps(active),
                     process_count, reasoner, notes),
                )
                return cur.lastrowid
        except Exception as exc:
            logger.warning("start_scan failed: %s", exc)
            return None

    def add_process_snapshot(self, scan_id: int | None, row: dict[str, Any]
                             ) -> int | None:
        """Insert one process snapshot row, returning its new row id so the
        caller can later update `action_taken` once an action is applied.
        Returns None (and logs) if the write fails."""
        try:
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
        except Exception as exc:
            logger.warning("add_process_snapshot failed for pid %s: %s",
                           row.get("pid"), exc)
            return None

    def update_action(self, snapshot_id: int, action: str) -> None:
        try:
            with self._conn() as c:
                c.execute("UPDATE process_snapshots SET action_taken=? WHERE id=?",
                          (action, snapshot_id))
        except Exception as exc:
            logger.warning("update_action failed for snapshot %s: %s", snapshot_id, exc)

    # ---- suspended-process persistence ("Resume Suspended" across restarts) --
    def record_suspended(self, pid: int, name: str, create_time: float) -> None:
        """Remember that we suspended this process, so Resume Suspended can
        find it again even after the app restarts."""
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO suspended_processes "
                "(pid, name, create_time, ts) VALUES (?,?,?,?)",
                (pid, name, create_time, time.time()))

    def remove_suspended(self, pid: int) -> None:
        """Forget a pid — call this once it's been resumed, killed, or found
        stale (pid/create_time no longer match a real process) on reload."""
        with self._conn() as c:
            c.execute("DELETE FROM suspended_processes WHERE pid=?", (pid,))

    def list_suspended(self) -> list[dict[str, Any]]:
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT pid, name, create_time, ts FROM suspended_processes "
                    "ORDER BY ts").fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("list_suspended failed: %s", exc)
            return []

    def log_model_status(self, provider: str, model: str, available: bool,
                         can_spawn: bool, in_use: bool, note: str = "") -> None:
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO model_status (ts, provider, model, available, can_spawn, "
                    "in_use, note) VALUES (?,?,?,?,?,?,?)",
                    (time.time(), provider, model, int(available), int(can_spawn),
                     int(in_use), note),
                )
        except Exception as exc:
            logger.warning("log_model_status failed for %s/%s: %s", provider, model, exc)

    def log_event(self, level: str, category: str, message: str,
                  detail: Any = None) -> None:
        if detail is not None and not isinstance(detail, str):
            detail = json.dumps(detail, default=str)
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO events (ts, level, category, message, detail) "
                    "VALUES (?,?,?,?,?)",
                    (time.time(), level, category, message, detail),
                )
        except Exception as exc:
            # Logged via the stdlib logger, not db.log_event — we're already
            # inside the method that failed, re-entering it would loop.
            logger.warning("log_event failed (%s/%s %r): %s", level, category, message, exc)

    # ---- reads --------------------------------------------------------------
    def recent_events(self, limit: int = 100) -> list[sqlite3.Row]:
        try:
            with self._conn() as c:
                return list(c.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)))
        except Exception as exc:
            logger.warning("recent_events failed: %s", exc)
            return []

    def stats(self) -> dict[str, int]:
        try:
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
        except Exception as exc:
            logger.warning("stats failed: %s", exc)
            return {}
