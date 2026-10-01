"""Static safety checks on gui.py's source, plus a few behavioral checks on
pure-logic methods that don't touch any tkinter widget. These never import
tkinter or open a window (per project policy, tests must not open GUI
windows): the static checks assert on the source text itself, and the
behavioral ones construct an OptimizerApp instance via `object.__new__`
(skipping __init__, so no Tk() root/widgets are ever created) and set only
the plain-Python attributes the method under test actually touches."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from Optimizer import gui

GUI_SRC = (Path(__file__).resolve().parent.parent / "gui.py").read_text(encoding="utf-8")


def test_default_mode_is_suggest_only_not_auto_apply():
    assert 'self.mode = tk.StringVar(value="suggest")' in GUI_SRC


def test_optimize_always_confirms_before_suspend_or_kill():
    assert "messagebox.askyesno(" in GUI_SRC


def test_suggest_mode_never_calls_apply_action():
    """In suggest mode, on_optimize must return before touching analyzer.apply_action."""
    start = GUI_SRC.index("def on_optimize(")
    end = GUI_SRC.index("\n    def ", start + 1)
    body = GUI_SRC[start:end]
    suggest_branch_start = body.index('mode == "suggest"')
    suggest_branch = body[suggest_branch_start:body.index("return", suggest_branch_start)]
    assert "apply_action" not in suggest_branch


def test_kill_confirmation_warns_about_unsaved_work():
    assert "unsaved work" in GUI_SRC.lower()


def test_mode_ordering_prefers_suspend_before_autoclose():
    """UI ordering requirement: Suggest, then Suspend (reversible), then
    Auto-close (destructive) — never the destructive option first."""
    order = [v for v, _label in [
        ("suggest", "Suggest only"), ("suspend", "Suspend (freeze)"),
        ("kill", "Auto-close")]]
    idx = {name: GUI_SRC.index(f'("{name}",') for name in order}
    assert idx["suggest"] < idx["suspend"] < idx["kill"]


def test_resume_feature_exists():
    assert "on_resume_all" in GUI_SRC
    assert "resume_pids" in GUI_SRC


def test_deepseek_key_dialog_states_privacy_tradeoff():
    assert "on_set_key" in GUI_SRC
    start = GUI_SRC.index("def on_set_key(")
    end = GUI_SRC.index("\n    def ", start + 1)
    body = GUI_SRC[start:end].lower()
    assert "opt-in" in body or "optional" in body or "only" in body
    assert "deepseek" in body


def test_suspended_processes_are_persisted_not_just_in_memory():
    """Coordinator safety-review item 2: Resume Suspended must survive a
    restart, so suspend/resume must go through the DB, not just a dict."""
    assert "db.record_suspended(" in GUI_SRC
    assert "db.remove_suspended(" in GUI_SRC
    assert "analyzer.load_suspended(" in GUI_SRC
    assert "_load_persisted_suspended" in GUI_SRC


def test_startup_loads_persisted_suspended_state():
    start = GUI_SRC.index("def __init__(self, root")
    end = GUI_SRC.index("\n    def ", start + 1)
    body = GUI_SRC[start:end]
    assert "_load_persisted_suspended()" in body


def test_optimize_done_db_writes_are_wrapped_in_try_except():
    """Coordinator finding 1: db.record_suspended/remove_suspended in
    _optimize_done must be wrapped so a DB hiccup can't abort the results
    loop (and leave the dialog/resume-button state stale)."""
    start = GUI_SRC.index("def _optimize_done(")
    end = GUI_SRC.index("\n    def ", start + 1)
    body = GUI_SRC[start:end]
    record_idx = body.index("self.db.record_suspended(")
    assert "try:" in body[max(0, record_idx - 120):record_idx]
    assert "except Exception" in body[record_idx:record_idx + 400]


def test_poll_resume_drops_permanently_stale_entries_but_keeps_retryable_ones():
    """Coordinator finding 4: a resume failure for a gone/reused pid must drop
    the entry (same rule as load_suspended on startup); a retryable failure
    (e.g. AccessDenied) must stay in self._suspended for the next click."""
    start = GUI_SRC.index("def _poll_resume(")
    end = GUI_SRC.index("\n    def ", start + 1)
    body = GUI_SRC[start:end]
    assert "processes.is_permanently_stale(" in body


# ---- behavioral checks on pure-logic methods (no Tk root ever created) ------

def _bare_app(db) -> gui.OptimizerApp:
    """An OptimizerApp with __init__ skipped — no tk.Tk(), no widgets, no
    window. Only set the plain attributes the method under test needs."""
    app = object.__new__(gui.OptimizerApp)
    app.db = db
    return app


def test_safe_remove_suspended_swallows_a_db_failure_and_logs(db, caplog):
    def raiser(pid):
        raise sqlite3.OperationalError("database is locked")
    db.remove_suspended = raiser
    app = _bare_app(db)

    with caplog.at_level(logging.ERROR, logger="Optimizer.gui"):
        app._safe_remove_suspended(123, "chrome.exe")  # must not raise

    assert any("123" in r.message or "chrome.exe" in r.message for r in caplog.records)


def test_safe_remove_suspended_calls_through_on_success(db):
    db.record_suspended(pid=123, name="chrome.exe", create_time=1.0)
    app = _bare_app(db)

    app._safe_remove_suspended(123, "chrome.exe")

    assert db.list_suspended() == []
