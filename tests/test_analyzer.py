"""Tests for analyzer.py's safety overrides and the apply-time gate.

This is the second of the three safety layers described in the README:
`_apply_safety_overrides` forces protected/active/model-bound rows to
"needed" no matter what a reasoner said, and `apply_action` refuses to even
call processes.kill/suspend for such rows — re-deriving the active-window and
model-connection facts LIVE (not just trusting the scan-time flags) and
verifying each pid's identity (create_time) before acting. psutil/processes/
active_windows/local_models are always mocked — no real process, window, or
network call is ever touched by this file.
"""
from __future__ import annotations

import sqlite3

import pytest
from Optimizer import analyzer, processes

from tests.conftest import make_row


@pytest.fixture(autouse=True)
def _no_live_system_queries(monkeypatch):
    """apply_action re-derives active-window/model-connection state live,
    right before acting. Default both to "nothing live" so every test that
    doesn't care about this re-derivation isn't secretly depending on
    whatever windows/models happen to be real on the test machine; tests that
    DO care override one of these explicitly."""
    monkeypatch.setattr(analyzer.active_windows, "active_pids", lambda: set())
    monkeypatch.setattr(analyzer.local_models, "pids_using_models", lambda: set())


# ---- _apply_safety_overrides ------------------------------------------------

def test_protected_row_is_forced_to_needed_even_if_reasoner_said_close():
    row = make_row(protected=True)
    decision = {"recommendation": "suggest_close", "confidence": 0.9, "reason": "bloat"}

    out = analyzer._apply_safety_overrides(row, decision)

    assert out["recommendation"] == "needed"
    assert out["confidence"] == 1.0


def test_active_window_row_is_forced_to_needed():
    row = make_row(is_active=True)
    decision = {"recommendation": "suggest_close", "confidence": 0.8, "reason": "idle"}

    out = analyzer._apply_safety_overrides(row, decision)

    assert out["recommendation"] == "needed"


def test_model_bound_row_is_forced_to_needed():
    row = make_row(uses_local_model=True)
    decision = {"recommendation": "suggest_close", "confidence": 0.8, "reason": "idle"}

    out = analyzer._apply_safety_overrides(row, decision)

    assert out["recommendation"] == "needed"


def test_ordinary_row_keeps_the_reasoners_decision():
    row = make_row()
    decision = {"recommendation": "suggest_close", "confidence": 0.6, "reason": "bloat"}

    out = analyzer._apply_safety_overrides(row, decision)

    assert out == decision


# ---- apply_action: scan-time flags --------------------------------------------

class _FakeResult:
    def __init__(self, rows):
        self.rows = rows


def test_apply_action_never_calls_kill_for_a_protected_row(monkeypatch, db):
    row = make_row(pid=111, protected=True)
    called = []
    monkeypatch.setattr(processes, "kill",
                        lambda pid, ct=None: called.append(pid) or (True, "x"))

    outcomes = analyzer.apply_action(_FakeResult([row]), [111], "kill", db)

    assert called == [], "a protected pid must never reach processes.kill"
    assert outcomes == [(111, False, "blocked by safety guard")]


def test_apply_action_never_calls_kill_for_a_scan_time_active_row(monkeypatch, db):
    row = make_row(pid=222, is_active=True)
    called = []
    monkeypatch.setattr(processes, "kill", lambda pid, ct=None: called.append(pid))

    outcomes = analyzer.apply_action(_FakeResult([row]), [222], "kill", db)

    assert called == []
    assert outcomes[0][1] is False


def test_apply_action_never_calls_suspend_for_a_scan_time_model_bound_row(monkeypatch, db):
    row = make_row(pid=333, uses_local_model=True)
    called = []
    monkeypatch.setattr(processes, "suspend", lambda pid, ct=None: called.append(pid))

    outcomes = analyzer.apply_action(_FakeResult([row]), [333], "suspend", db)

    assert called == []
    assert outcomes[0][1] is False


def test_apply_action_calls_kill_for_an_ordinary_closable_row(monkeypatch, db):
    row = make_row(pid=444)
    called = []
    monkeypatch.setattr(processes, "kill",
                        lambda pid, ct=None: called.append((pid, ct)) or (True, "terminated"))

    outcomes = analyzer.apply_action(_FakeResult([row]), [444], "kill", db)

    assert called == [(444, row["create_time"])]
    assert outcomes == [(444, True, "terminated")]


def test_apply_action_passes_the_scan_time_create_time_to_processes_suspend(monkeypatch, db):
    row = make_row(pid=777, create_time=12345.6)
    seen = {}
    monkeypatch.setattr(
        processes, "suspend",
        lambda pid, ct=None: (seen.update(pid=pid, create_time=ct), (True, "suspended"))[1])

    analyzer.apply_action(_FakeResult([row]), [777], "suspend", db)

    assert seen == {"pid": 777, "create_time": 12345.6}


def test_apply_action_records_the_action_against_the_snapshot(monkeypatch, db):
    row = make_row(pid=555)
    row["snapshot_id"] = db.add_process_snapshot(
        db.start_scan("suggest", "", [], 1, "heuristic"), row)
    monkeypatch.setattr(processes, "suspend", lambda pid, ct=None: (True, "suspended"))

    analyzer.apply_action(_FakeResult([row]), [555], "suspend", db)

    with db._conn() as c:
        action = c.execute(
            "SELECT action_taken FROM process_snapshots WHERE id=?",
            (row["snapshot_id"],)).fetchone()[0]
    assert action == "suspended"


def test_apply_action_unknown_mode_fails_closed(monkeypatch, db):
    row = make_row(pid=666)
    outcomes = analyzer.apply_action(_FakeResult([row]), [666], "bogus", db)

    assert outcomes == [(666, False, "unknown mode")]


def test_apply_action_refuses_an_unknown_pid_without_touching_processes(monkeypatch, db):
    called = []
    monkeypatch.setattr(processes, "kill", lambda pid, ct=None: called.append(pid))

    outcomes = analyzer.apply_action(_FakeResult([]), [999], "kill", db)

    assert called == []
    assert outcomes == [(999, False, "unknown pid (not part of this scan)")]


# ---- apply_action: live re-derivation (the coordinator's headline gaps) ------
# A scan can be stale by the time the user clicks OPTIMIZE. apply_action must
# re-check the live world, not just the flags captured at scan time.

def test_apply_action_refuses_a_process_that_became_foreground_since_scan(monkeypatch, db):
    """Row says is_active=False (it wasn't active when scanned), but the user
    has since switched to it -> must refuse, live, even though nothing about
    the row itself changed."""
    row = make_row(pid=888, is_active=False)
    called = []
    monkeypatch.setattr(processes, "kill", lambda pid, ct=None: called.append(pid))
    monkeypatch.setattr(analyzer.active_windows, "active_pids", lambda: {888})

    outcomes = analyzer.apply_action(_FakeResult([row]), [888], "kill", db)

    assert called == [], "must never reach processes.kill once live-active"
    assert outcomes == [(888, False, "now has an active window — refused")]


def test_apply_action_refuses_a_process_that_connected_to_a_model_since_scan(monkeypatch, db):
    """Row says uses_local_model=False at scan time, but it has since opened a
    connection to Ollama/LM Studio -> must refuse, live."""
    row = make_row(pid=999, uses_local_model=False)
    called = []
    monkeypatch.setattr(processes, "suspend", lambda pid, ct=None: called.append(pid))
    monkeypatch.setattr(analyzer.local_models, "pids_using_models", lambda: {999})

    outcomes = analyzer.apply_action(_FakeResult([row]), [999], "suspend", db)

    assert called == []
    assert outcomes == [(999, False, "now connected to a local model — refused")]


def test_apply_action_proceeds_when_live_state_is_still_clear(monkeypatch, db):
    """Sanity check: the live re-derivation isn't just refusing everything —
    an ordinary row with nothing live-active/model-bound still goes through."""
    row = make_row(pid=1010)
    monkeypatch.setattr(processes, "kill", lambda pid, ct=None: (True, "terminated"))
    monkeypatch.setattr(analyzer.active_windows, "active_pids", lambda: {1, 2, 3})
    monkeypatch.setattr(analyzer.local_models, "pids_using_models", lambda: {4, 5, 6})

    outcomes = analyzer.apply_action(_FakeResult([row]), [1010], "kill", db)

    assert outcomes == [(1010, True, "terminated")]


def test_apply_action_rederives_live_state_per_pid_not_once_per_batch(monkeypatch, db):
    """Coordinator finding: re-query active_windows/local_models right before
    EACH pid's action, not once for the whole batch. Simulate a process that
    becomes active in the gap between the first and second pid in the same
    multi-select batch — the second pid must still be refused."""
    row_a = make_row(pid=1, name="a.exe")
    row_b = make_row(pid=2, name="b.exe")
    active_calls = []

    def fake_active_pids():
        active_calls.append(len(active_calls))
        # Pid 2 "becomes" active only starting on the SECOND call -- if
        # apply_action only queried this once up front, it would never see
        # pid 2 as active and would (incorrectly) kill it.
        return {2} if len(active_calls) >= 2 else set()

    monkeypatch.setattr(analyzer.active_windows, "active_pids", fake_active_pids)
    killed = []
    monkeypatch.setattr(
        processes, "kill",
        lambda pid, ct=None: killed.append(pid) or (True, "terminated"))

    outcomes = analyzer.apply_action(_FakeResult([row_a, row_b]), [1, 2], "kill", db)

    assert len(active_calls) >= 2, "active_pids() must be queried at least once per pid"
    assert killed == [1], "pid 2 must never reach processes.kill once live-active"
    assert outcomes == [
        (1, True, "terminated"),
        (2, False, "now has an active window — refused"),
    ]


def test_apply_action_rederives_model_connection_per_pid(monkeypatch, db):
    row_a = make_row(pid=10, name="a.exe")
    row_b = make_row(pid=20, name="b.exe")
    model_calls = []

    def fake_model_pids():
        model_calls.append(len(model_calls))
        return {20} if len(model_calls) >= 2 else set()

    monkeypatch.setattr(analyzer.local_models, "pids_using_models", fake_model_pids)
    suspended = []
    monkeypatch.setattr(processes, "suspend",
                        lambda pid, ct=None: suspended.append(pid) or (True, "suspended"))

    outcomes = analyzer.apply_action(_FakeResult([row_a, row_b]), [10, 20], "suspend", db)

    assert len(model_calls) >= 2
    assert suspended == [10]
    assert outcomes[1] == (20, False, "now connected to a local model — refused")


# ---- resume_pids ---------------------------------------------------------------

def test_resume_pids_calls_processes_resume_with_pid_and_create_time(monkeypatch, db):
    seen = []
    monkeypatch.setattr(
        processes, "resume",
        lambda pid, ct=None: (seen.append((pid, ct)), (True, "resumed"))[1])

    entries = [{"pid": 1, "create_time": 10.0, "name": "a.exe"},
              {"pid": 2, "create_time": 20.0, "name": "b.exe"}]
    outcomes = analyzer.resume_pids(entries, db)

    assert seen == [(1, 10.0), (2, 20.0)]
    assert outcomes == [(1, True, "resumed"), (2, True, "resumed")]


def test_resume_pids_reports_failures_without_raising(monkeypatch, db):
    monkeypatch.setattr(processes, "resume", lambda pid, ct=None: (False, "no such process"))

    outcomes = analyzer.resume_pids([{"pid": 42, "create_time": 1.0, "name": "x.exe"}], db)

    assert outcomes == [(42, False, "no such process")]


# ---- load_suspended: persistence across a restart ---------------------------
# Coordinator's item 2: suspended-process tracking must survive the app being
# closed and reopened, with stale entries (pid gone, or reused by something
# else) dropped rather than trusted blindly.

def test_load_suspended_returns_entries_that_still_match(monkeypatch, db):
    db.record_suspended(pid=111, name="chrome.exe", create_time=500.0)
    monkeypatch.setattr(processes, "is_same_process", lambda pid, ct: True)

    live = analyzer.load_suspended(db)

    assert live == {111: {"name": "chrome.exe", "create_time": 500.0}}
    # still in the DB — nothing was dropped
    assert [e["pid"] for e in db.list_suspended()] == [111]


def test_load_suspended_drops_entries_whose_identity_no_longer_matches(monkeypatch, db):
    """Simulates a restart where the previously-suspended pid now belongs to
    a different process (or is gone) — must not be resumable, and must be
    cleared from the persisted list so it doesn't linger forever."""
    db.record_suspended(pid=111, name="chrome.exe", create_time=500.0)
    monkeypatch.setattr(processes, "is_same_process", lambda pid, ct: False)

    live = analyzer.load_suspended(db)

    assert live == {}
    assert db.list_suspended() == [], "stale entry must be removed from the DB too"


def test_load_suspended_handles_a_mix_of_valid_and_stale_entries(monkeypatch, db):
    db.record_suspended(pid=1, name="still-suspended.exe", create_time=1.0)
    db.record_suspended(pid=2, name="reused-pid.exe", create_time=2.0)

    monkeypatch.setattr(processes, "is_same_process", lambda pid, ct: pid == 1)

    live = analyzer.load_suspended(db)

    assert live == {1: {"name": "still-suspended.exe", "create_time": 1.0}}
    assert [e["pid"] for e in db.list_suspended()] == [1]


def test_load_suspended_survives_a_db_failure_while_dropping_a_stale_entry(monkeypatch, db):
    """load_suspended runs from gui.OptimizerApp.__init__ (startup). If
    db.remove_suspended() throws while clearing a stale entry, that must not
    crash app startup or stop the rest of the list from being validated."""
    db.record_suspended(pid=1, name="stale.exe", create_time=1.0)
    db.record_suspended(pid=2, name="still-good.exe", create_time=2.0)
    monkeypatch.setattr(processes, "is_same_process", lambda pid, ct: pid == 2)

    def raising_remove(pid):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db, "remove_suspended", raising_remove)

    live = analyzer.load_suspended(db)  # must not raise

    assert live == {2: {"name": "still-good.exe", "create_time": 2.0}}


def test_resume_pids_refuses_a_reused_pid(monkeypatch, db):
    """End-to-end through the analyzer layer: processes.resume is the one that
    actually does the create_time comparison, so this pins that resume_pids
    passes the recorded create_time through rather than dropping it."""
    monkeypatch.setattr(
        processes, "resume",
        lambda pid, ct=None: (False, "pid was reused by a different process since scan"))

    outcomes = analyzer.resume_pids(
        [{"pid": 7, "create_time": 999.0, "name": "old.exe"}], db)

    assert outcomes == [(7, False, "pid was reused by a different process since scan")]
