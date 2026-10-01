"""Regression tests for the protected-process safety gate.

This tool can kill/suspend OS processes; `_is_protected` is the single pure
function every safety layer leans on. If it ever returns False for something on
the protected list, the optimizer could blue-screen the machine. These tests
pin that behavior. They are pure (no real processes are touched).
"""
from Optimizer import config
from Optimizer.processes import _is_protected


def test_kernel_and_session_processes_are_protected():
    for name in ("csrss.exe", "wininit.exe", "lsass.exe", "dwm.exe",
                 "explorer.exe", "services.exe", "winlogon.exe"):
        assert _is_protected(name, "") is True, name


def test_security_processes_are_protected():
    for name in ("MsMpEng.exe", "SecurityHealthService.exe", "smartscreen.exe"):
        assert _is_protected(name, "") is True, name


def test_case_insensitive():
    assert _is_protected("LSASS.EXE", "") is True
    assert _is_protected("Explorer.Exe", "") is True


def test_optimizer_own_runtime_is_protected():
    # Must never let the optimizer kill its own interpreter.
    assert _is_protected("python.exe", "") is True
    assert _is_protected("pythonw.exe", "") is True


def test_local_model_runners_protected_by_hint():
    # Matched via MODEL_RUNNER_HINTS substring in name OR exe path.
    assert _is_protected("ollama.exe", "") is True
    assert _is_protected("some_helper.exe",
                         r"C:\Users\me\AppData\Local\Programs\LM Studio\app.exe") is True


def test_ordinary_app_is_not_protected():
    assert _is_protected("notepad.exe", r"C:\Windows\notepad.exe") is False
    assert _is_protected("chrome.exe", r"C:\Program Files\Google\chrome.exe") is False


def test_empty_name_does_not_crash_and_is_not_protected():
    assert _is_protected("", "") is False
    assert _is_protected(None, None) is False  # type: ignore[arg-type]


def test_protected_list_has_the_critical_names():
    # Guard against an accidental edit that drops a must-keep entry.
    for must in ("lsass.exe", "csrss.exe", "explorer.exe", "msmpeng.exe"):
        assert must in config.PROTECTED_NAMES, must
