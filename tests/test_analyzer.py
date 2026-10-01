"""Tests for analyzer.py's safety overrides and the apply-time gate.

This is the second of the three safety layers described in the README:
`_apply_safety_overrides` forces protected/active/model-bound rows to
"needed" no matter what a reasoner said, and `apply_action` refuses to even
call processes.kill/suspend for such rows. psutil/processes are always
mocked — no real process is ever touched by this file.
"""
from __future__ import annotations

from Optimizer import analyzer, processes

from tests.conftest import make_row

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


# ---- apply_action ------------------------------------------------------------

class _FakeResult:
    def __init__(self, rows):
        self.rows = rows


def test_apply_action_never_calls_kill_for_a_protected_row(monkeypatch, db):
    row = make_row(pid=111, protected=True)
    called = []
    monkeypatch.setattr(processes, "kill", lambda pid: called.append(pid) or (True, "x"))

    outcomes = analyzer.apply_action(_FakeResult([row]), [111], "kill", db)

    assert called == [], "a protected pid must never reach processes.kill"
    assert outcomes == [(111, False, "blocked by safety guard")]


def test_apply_action_never_calls_kill_for_an_active_window_row(monkeypatch, db):
    row = make_row(pid=222, is_active=True)
    called = []
    monkeypatch.setattr(processes, "kill", lambda pid: called.append(pid) or (True, "x"))

    outcomes = analyzer.apply_action(_FakeResult([row]), [222], "kill", db)

    assert called == []
    assert outcomes[0][1] is False


def test_apply_action_never_calls_suspend_for_a_model_bound_row(monkeypatch, db):
    row = make_row(pid=333, uses_local_model=True)
    called = []
    monkeypatch.setattr(processes, "suspend", lambda pid: called.append(pid) or (True, "x"))

    outcomes = analyzer.apply_action(_FakeResult([row]), [333], "suspend", db)

    assert called == []
    assert outcomes[0][1] is False


def test_apply_action_calls_kill_for_an_ordinary_closable_row(monkeypatch, db):
    row = make_row(pid=444)
    called = []
    monkeypatch.setattr(processes, "kill", lambda pid: called.append(pid) or (True, "terminated"))

    outcomes = analyzer.apply_action(_FakeResult([row]), [444], "kill", db)

    assert called == [444]
    assert outcomes == [(444, True, "terminated")]


def test_apply_action_records_the_action_against_the_snapshot(monkeypatch, db):
    row = make_row(pid=555)
    row["snapshot_id"] = db.add_process_snapshot(
        db.start_scan("suggest", "", [], 1, "heuristic"), row)
    monkeypatch.setattr(processes, "suspend", lambda pid: (True, "suspended"))

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


# ---- resume_pids ---------------------------------------------------------------

def test_resume_pids_calls_processes_resume_for_each_pid(monkeypatch, db):
    seen = []
    monkeypatch.setattr(processes, "resume",
                        lambda pid: (seen.append(pid), (True, "resumed"))[1])

    outcomes = analyzer.resume_pids([1, 2, 3], db)

    assert seen == [1, 2, 3]
    assert outcomes == [(1, True, "resumed"), (2, True, "resumed"), (3, True, "resumed")]


def test_resume_pids_reports_failures_without_raising(monkeypatch, db):
    monkeypatch.setattr(processes, "resume", lambda pid: (False, "no such process"))

    outcomes = analyzer.resume_pids([42], db)

    assert outcomes == [(42, False, "no such process")]
