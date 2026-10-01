"""Tests for database.py against a throwaway SQLite file (never the user's
real data/optimizer.db)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

from tests.conftest import make_row


@contextmanager
def _broken_conn(db):
    """Make db._conn() raise, simulating a locked/corrupt DB file, for
    exactly the duration of the `with` block."""
    def raiser():
        raise sqlite3.OperationalError("database is locked")
    original = db._conn
    db._conn = raiser
    try:
        yield
    finally:
        db._conn = original


def test_start_scan_returns_an_id_and_persists_fields(db):
    scan_id = db.start_scan(mode="suggest", foreground="Notepad",
                            active=["Notepad", "Chrome"], process_count=42,
                            reasoner="heuristic")

    assert isinstance(scan_id, int) and scan_id > 0
    with db._conn() as c:
        row = c.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    assert row["mode"] == "suggest"
    assert row["foreground_window"] == "Notepad"
    assert json.loads(row["active_windows"]) == ["Notepad", "Chrome"]
    assert row["process_count"] == 42


def test_add_process_snapshot_returns_its_row_id(db):
    scan_id = db.start_scan("suggest", "", [], 0, "heuristic")
    row = make_row(pid=1, name="chrome.exe", recommendation="suggest_close",
                   confidence=0.8, reason="bloat")

    snap_id = db.add_process_snapshot(scan_id, row)

    assert isinstance(snap_id, int) and snap_id > 0
    with db._conn() as c:
        saved = c.execute(
            "SELECT * FROM process_snapshots WHERE id=?", (snap_id,)).fetchone()
    assert saved["pid"] == 1
    assert saved["name"] == "chrome.exe"
    assert saved["recommendation"] == "suggest_close"
    assert saved["action_taken"] == "none"


def test_update_action_changes_only_the_targeted_row(db):
    scan_id = db.start_scan("kill", "", [], 0, "heuristic")
    id_a = db.add_process_snapshot(scan_id, make_row(pid=1))
    id_b = db.add_process_snapshot(scan_id, make_row(pid=2))

    db.update_action(id_a, "killed")

    with db._conn() as c:
        a = c.execute("SELECT action_taken FROM process_snapshots WHERE id=?",
                      (id_a,)).fetchone()[0]
        b = c.execute("SELECT action_taken FROM process_snapshots WHERE id=?",
                      (id_b,)).fetchone()[0]
    assert a == "killed"
    assert b == "none"


def test_log_model_status_roundtrip(db):
    db.log_model_status("ollama", "qwen2.5:7b", available=True, can_spawn=True,
                        in_use=False, note="")

    with db._conn() as c:
        row = c.execute("SELECT * FROM model_status").fetchone()
    assert row["provider"] == "ollama"
    assert row["available"] == 1
    assert row["in_use"] == 0


def test_log_event_serializes_non_string_detail(db):
    db.log_event("warning", "api", "DeepSeek failed", {"pid": 123, "code": 429})

    events = db.recent_events(limit=5)
    assert len(events) == 1
    detail = json.loads(events[0]["detail"])
    assert detail == {"pid": 123, "code": 429}


def test_log_event_accepts_plain_string_detail(db):
    db.log_event("info", "scan", "ok", "already a string")

    events = db.recent_events(limit=5)
    assert events[0]["detail"] == "already a string"


def test_recent_events_orders_newest_first(db):
    db.log_event("info", "scan", "first")
    db.log_event("info", "scan", "second")

    events = db.recent_events(limit=5)

    assert events[0]["message"] == "second"
    assert events[1]["message"] == "first"


def test_record_suspended_then_list_suspended_roundtrip(db):
    db.record_suspended(pid=111, name="chrome.exe", create_time=1700000000.0)
    db.record_suspended(pid=222, name="discord.exe", create_time=1700000050.0)

    entries = db.list_suspended()

    assert {e["pid"] for e in entries} == {111, 222}
    chrome = next(e for e in entries if e["pid"] == 111)
    assert chrome["name"] == "chrome.exe"
    assert chrome["create_time"] == 1700000000.0


def test_record_suspended_overwrites_the_same_pid(db):
    db.record_suspended(pid=111, name="chrome.exe", create_time=1.0)
    db.record_suspended(pid=111, name="chrome.exe", create_time=2.0)

    entries = db.list_suspended()

    assert len(entries) == 1
    assert entries[0]["create_time"] == 2.0


def test_remove_suspended_drops_only_that_pid(db):
    db.record_suspended(pid=111, name="a.exe", create_time=1.0)
    db.record_suspended(pid=222, name="b.exe", create_time=2.0)

    db.remove_suspended(111)

    entries = db.list_suspended()
    assert [e["pid"] for e in entries] == [222]


def test_list_suspended_empty_by_default(db):
    assert db.list_suspended() == []


def test_stats_counts_scans_snapshots_and_actions(db):
    scan_id = db.start_scan("kill", "", [], 0, "heuristic")
    id_a = db.add_process_snapshot(scan_id, make_row(pid=1))
    id_b = db.add_process_snapshot(scan_id, make_row(pid=2))
    db.update_action(id_a, "killed")
    db.update_action(id_b, "suspended")
    db.log_event("error", "scan", "boom")

    stats = db.stats()

    assert stats["scans"] == 1
    assert stats["snapshots"] == 2
    assert stats["killed"] == 1
    assert stats["suspended"] == 1
    assert stats["errors"] == 1


# ---- DB failures must never propagate (repo-wide DB error convention) -------
# A locked file, a full disk, or schema drift must degrade gracefully (log a
# warning, return a safe fallback) rather than crash whatever scan/action/
# startup path is in progress. Every write/read method is checked here.

def test_start_scan_returns_none_on_db_failure_without_raising(db, caplog):
    with _broken_conn(db), caplog.at_level("WARNING"):
        result = db.start_scan("suggest", "", [], 0, "heuristic")
    assert result is None
    assert any("start_scan" in r.message for r in caplog.records)


def test_add_process_snapshot_returns_none_on_db_failure(db, caplog):
    with _broken_conn(db), caplog.at_level("WARNING"):
        result = db.add_process_snapshot(1, make_row(pid=1))
    assert result is None
    assert any("add_process_snapshot" in r.message for r in caplog.records)


def test_update_action_does_not_raise_on_db_failure(db, caplog):
    with _broken_conn(db), caplog.at_level("WARNING"):
        db.update_action(1, "killed")  # must not raise
    assert any("update_action" in r.message for r in caplog.records)


def test_log_model_status_does_not_raise_on_db_failure(db, caplog):
    with _broken_conn(db), caplog.at_level("WARNING"):
        db.log_model_status("ollama", "x", True, True, False)
    assert any("log_model_status" in r.message for r in caplog.records)


def test_log_event_does_not_raise_on_db_failure(db, caplog):
    with _broken_conn(db), caplog.at_level("WARNING"):
        db.log_event("info", "scan", "hello")  # must not raise
    assert any("log_event" in r.message for r in caplog.records)


def test_recent_events_returns_empty_list_on_db_failure(db, caplog):
    with _broken_conn(db), caplog.at_level("WARNING"):
        result = db.recent_events()
    assert result == []
    assert any("recent_events" in r.message for r in caplog.records)


def test_stats_returns_empty_dict_on_db_failure(db, caplog):
    with _broken_conn(db), caplog.at_level("WARNING"):
        result = db.stats()
    assert result == {}
    assert any("stats" in r.message for r in caplog.records)


def test_list_suspended_returns_empty_list_on_db_failure(db, caplog):
    with _broken_conn(db), caplog.at_level("WARNING"):
        result = db.list_suspended()
    assert result == []
    assert any("list_suspended" in r.message for r in caplog.records)


