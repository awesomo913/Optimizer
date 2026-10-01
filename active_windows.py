"""Detect what the user is actually working in: the foreground window and all
visible top-level application windows, mapped to their owning PIDs.

Pure ctypes -> no extra dependency (lightest footprint)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

_EnumWindowsProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _get_window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _get_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def foreground() -> tuple[int, str]:
    """Return (pid, title) of the window the user is focused on right now."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, ""
    return _get_window_pid(hwnd), _get_title(hwnd)


def active_window_pids() -> dict[int, str]:
    """All visible, titled, top-level windows -> {pid: title}.

    Represents the set of programs the user has open and is working with."""
    result: dict[int, str] = {}

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _get_title(hwnd)
        if not title:
            return True
        # skip zero-size / off-screen helper windows
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if (rect.right - rect.left) <= 0 or (rect.bottom - rect.top) <= 0:
            return True
        pid = _get_window_pid(hwnd)
        if pid:
            # keep the longest/most descriptive title per pid
            if pid not in result or len(title) > len(result[pid]):
                result[pid] = title
        return True

    user32.EnumWindows(_EnumWindowsProc(callback), 0)
    return result


def active_pids() -> set[int]:
    """All PIDs that currently own a visible window, plus the current
    foreground pid. This is the single definition of "has an active window" —
    used both at scan time and again, live, immediately before any
    suspend/kill so a process that *became* active after the scan is still
    protected."""
    fg_pid, _fg_title = foreground()
    return set(active_window_pids()) | ({fg_pid} if fg_pid else set())
