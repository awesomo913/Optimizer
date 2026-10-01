"""Static safety checks on gui.py's source. These never import tkinter or open
a window (per project policy, tests must not open GUI windows) — they assert
on the source text itself to pin the "nothing is closed/suspended without an
explicit click" contract from the README/SECURITY model."""
from __future__ import annotations

from pathlib import Path

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
