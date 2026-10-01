"""Tests for active_windows.py's orchestration logic (foreground / enumeration
decisions). The low-level ctypes primitives (_get_window_pid, _get_title) are
monkeypatched rather than driven through real Win32 calls — this keeps the
tests fast, deterministic, and independent of whatever windows happen to be
open on the machine running CI."""
from __future__ import annotations

from types import SimpleNamespace

from Optimizer import active_windows


def test_foreground_returns_zero_pid_and_empty_title_when_no_window(monkeypatch):
    fake = SimpleNamespace(GetForegroundWindow=lambda: 0)
    monkeypatch.setattr(active_windows, "user32", fake)

    assert active_windows.foreground() == (0, "")


def test_foreground_returns_pid_and_title_for_a_real_window(monkeypatch):
    fake = SimpleNamespace(GetForegroundWindow=lambda: 42)
    monkeypatch.setattr(active_windows, "user32", fake)
    monkeypatch.setattr(active_windows, "_get_window_pid", lambda hwnd: 1234)
    monkeypatch.setattr(active_windows, "_get_title", lambda hwnd: "Notepad")

    assert active_windows.foreground() == (1234, "Notepad")


def test_active_window_pids_skips_invisible_windows(monkeypatch):
    fake = SimpleNamespace(
        IsWindowVisible=lambda hwnd: False,
        EnumWindows=lambda proc, lparam: proc(99, 0),
    )
    monkeypatch.setattr(active_windows, "user32", fake)

    result = active_windows.active_window_pids()

    assert result == {}


def test_active_window_pids_skips_untitled_windows(monkeypatch):
    fake = SimpleNamespace(
        IsWindowVisible=lambda hwnd: True,
        EnumWindows=lambda proc, lparam: proc(99, 0),
    )
    monkeypatch.setattr(active_windows, "user32", fake)
    monkeypatch.setattr(active_windows, "_get_title", lambda hwnd: "")

    result = active_windows.active_window_pids()

    assert result == {}


def test_active_window_pids_skips_zero_sized_windows(monkeypatch):
    class FakeRect:
        def __init__(self):
            self.left = self.right = self.top = self.bottom = 0

    def fake_get_window_rect(hwnd, rect_ref):
        pass  # rect stays all-zero -> zero width/height -> skipped

    fake = SimpleNamespace(
        IsWindowVisible=lambda hwnd: True,
        EnumWindows=lambda proc, lparam: proc(99, 0),
        GetWindowRect=fake_get_window_rect,
    )
    monkeypatch.setattr(active_windows, "user32", fake)
    monkeypatch.setattr(active_windows, "_get_title", lambda hwnd: "Hidden Helper")

    result = active_windows.active_window_pids()

    assert result == {}


def test_active_window_pids_keeps_the_longest_title_per_pid(monkeypatch):
    windows = [(1, "a.txt - Notepad"), (2, "short")]

    def enum_windows(proc, lparam):
        for hwnd, _title in windows:
            proc(hwnd, 0)

    titles = {1: "a.txt - Notepad", 2: "short"}
    pids = {1: 55, 2: 55}  # both hwnds belong to the same pid

    fake = SimpleNamespace(
        IsWindowVisible=lambda hwnd: True,
        EnumWindows=enum_windows,
        GetWindowRect=lambda hwnd, rect_ref: None,
    )
    monkeypatch.setattr(active_windows, "user32", fake)
    monkeypatch.setattr(active_windows, "_get_title", lambda hwnd: titles[hwnd])
    monkeypatch.setattr(active_windows, "_get_window_pid", lambda hwnd: pids[hwnd])

    # GetWindowRect is a no-op above, leaving rect fields at 0 which the real
    # code treats as zero-size -> skip. Patch wintypes.RECT via a stand-in
    # that reports a nonzero size instead.
    from ctypes import wintypes
    real_rect = wintypes.RECT

    class NonZeroRect(real_rect):
        def __init__(self):
            super().__init__()
            self.right = 100
            self.bottom = 100

    monkeypatch.setattr(wintypes, "RECT", NonZeroRect)
    try:
        result = active_windows.active_window_pids()
    finally:
        monkeypatch.setattr(wintypes, "RECT", real_rect)

    assert result == {55: "a.txt - Notepad"}
