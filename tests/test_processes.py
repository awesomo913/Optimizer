"""Tests for processes.kill / suspend / resume — the action-time safety gate.

These never touch a real process: psutil.Process is replaced with a fake
that records what was called, so we can prove the protected-process check
(and, now, the pid-identity check) happens BEFORE terminate()/suspend() is
ever invoked.
"""
from __future__ import annotations

import os

import psutil
import pytest
from Optimizer import processes

REAL_CREATE_TIME = 1_700_000_000.0


class FakeProcess:
    def __init__(self, name="notepad.exe", exe=r"C:\Windows\notepad.exe",
                running=True, raise_on_init=None, create_time=REAL_CREATE_TIME):
        if raise_on_init:
            raise raise_on_init
        self._name = name
        self._exe = exe
        self._running = running
        self._create_time = create_time
        self.terminated = False
        self.killed = False
        self.suspended = False
        self.resumed = False
        self.waited = False

    def name(self):
        return self._name

    def exe(self):
        return self._exe

    def is_running(self):
        return self._running

    def create_time(self):
        return self._create_time

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True

    def suspend(self):
        self.suspended = True

    def resume(self):
        self.resumed = True


def test_kill_refuses_protected_process(monkeypatch):
    fake = FakeProcess(name="lsass.exe")
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.kill(999)

    assert ok is False
    assert "protected" in msg
    assert fake.terminated is False, "a protected process must never be terminated"


def test_suspend_refuses_protected_process(monkeypatch):
    fake = FakeProcess(name="MsMpEng.exe")
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.suspend(999)

    assert ok is False
    assert "protected" in msg
    assert fake.suspended is False


def test_kill_refuses_model_runner_by_exe_hint(monkeypatch):
    fake = FakeProcess(name="app.exe",
                       exe=r"C:\Users\me\AppData\Local\Programs\LM Studio\app.exe")
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.kill(999)

    assert ok is False
    assert fake.terminated is False


def test_kill_ordinary_process_terminates(monkeypatch):
    fake = FakeProcess(name="notepad.exe")
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.kill(999)

    assert ok is True
    assert fake.terminated is True
    assert msg == "terminated"


def test_suspend_ordinary_process_suspends(monkeypatch):
    fake = FakeProcess(name="chrome.exe")
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.suspend(999)

    assert ok is True
    assert fake.suspended is True


def test_kill_handles_already_gone(monkeypatch):
    def raiser(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, "Process", raiser)

    ok, msg = processes.kill(999)

    assert ok is True
    assert "already gone" in msg


def test_suspend_handles_already_gone(monkeypatch):
    def raiser(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, "Process", raiser)

    ok, msg = processes.suspend(999)

    assert ok is False
    assert "no such process" in msg


def test_kill_handles_access_denied(monkeypatch):
    fake = FakeProcess(name="notepad.exe")
    fake.terminate = lambda: (_ for _ in ()).throw(psutil.AccessDenied(1234))
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.kill(999)

    assert ok is False
    assert "AccessDenied" in msg


def test_resume_calls_resume_on_the_process(monkeypatch):
    fake = FakeProcess(name="chrome.exe")
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.resume(999)

    assert ok is True
    assert fake.resumed is True
    assert msg == "resumed"


def test_resume_handles_no_such_process(monkeypatch):
    def raiser(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, "Process", raiser)

    ok, msg = processes.resume(999)

    assert ok is False
    assert "no such process" in msg


def test_resume_is_never_blocked_by_the_protected_list(monkeypatch):
    """Resuming is always safe to allow — there is no protected-process gate
    on resume (unlike kill/suspend), since un-suspending something the
    optimizer itself suspended cannot make the system worse off."""
    fake = FakeProcess(name="lsass.exe")  # even a protected name...
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.resume(999)

    assert ok is True
    assert fake.resumed is True


@pytest.mark.parametrize("fn", [processes.kill, processes.suspend, processes.resume])
def test_pid_argument_is_passed_through(monkeypatch, fn):
    seen = {}

    def fake_process(pid):
        seen["pid"] = pid
        return FakeProcess()
    monkeypatch.setattr(psutil, "Process", fake_process)

    fn(4321)

    assert seen["pid"] == 4321


# ---- PID reuse protection ---------------------------------------------------
# A pid captured at scan time can belong to a totally different process by
# the time an action runs (the OS recycles pids). create_time is the identity
# check that catches this.

def test_kill_refuses_a_reused_pid(monkeypatch):
    """The live process has a different create_time than what was recorded at
    scan time -> it's not the same process anymore. Must refuse, not kill."""
    fake = FakeProcess(name="notepad.exe", create_time=REAL_CREATE_TIME + 500)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.kill(999, expected_create_time=REAL_CREATE_TIME)

    assert ok is False
    assert "reused" in msg
    assert fake.terminated is False


def test_suspend_refuses_a_reused_pid(monkeypatch):
    fake = FakeProcess(name="chrome.exe", create_time=REAL_CREATE_TIME + 500)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.suspend(999, expected_create_time=REAL_CREATE_TIME)

    assert ok is False
    assert "reused" in msg
    assert fake.suspended is False


def test_resume_refuses_a_reused_pid(monkeypatch):
    fake = FakeProcess(name="chrome.exe", create_time=REAL_CREATE_TIME + 500)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.resume(999, expected_create_time=REAL_CREATE_TIME)

    assert ok is False
    assert "reused" in msg
    assert fake.resumed is False


def test_kill_allows_a_matching_create_time(monkeypatch):
    fake = FakeProcess(name="notepad.exe", create_time=REAL_CREATE_TIME)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.kill(999, expected_create_time=REAL_CREATE_TIME)

    assert ok is True
    assert fake.terminated is True


def test_kill_tolerates_tiny_float_rounding_in_create_time(monkeypatch):
    """create_time is a float re-read from the OS; sub-millisecond jitter
    between two reads of the SAME process must not be mistaken for reuse."""
    fake = FakeProcess(name="notepad.exe", create_time=REAL_CREATE_TIME + 0.0003)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.kill(999, expected_create_time=REAL_CREATE_TIME)

    assert ok is True


def test_is_same_process_true_for_a_matching_pid(monkeypatch):
    fake = FakeProcess(create_time=REAL_CREATE_TIME)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    assert processes.is_same_process(999, REAL_CREATE_TIME) is True


def test_is_same_process_false_for_a_reused_pid(monkeypatch):
    fake = FakeProcess(create_time=REAL_CREATE_TIME + 500)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    assert processes.is_same_process(999, REAL_CREATE_TIME) is False


def test_is_same_process_false_when_gone(monkeypatch):
    def raiser(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, "Process", raiser)

    assert processes.is_same_process(999, REAL_CREATE_TIME) is False


# ---- self-protection ---------------------------------------------------------
# The optimizer must never act on its own process or its parent, regardless
# of what name/exe psutil reports for that pid.

def test_kill_refuses_own_pid_even_with_an_ordinary_looking_name(monkeypatch):
    fake = FakeProcess(name="totally_normal_app.exe")
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.kill(os.getpid())

    assert ok is False
    assert "protected" in msg
    assert fake.terminated is False


def test_suspend_refuses_parent_pid(monkeypatch):
    fake = FakeProcess(name="totally_normal_app.exe")
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    ok, msg = processes.suspend(os.getppid())

    assert ok is False
    assert "protected" in msg
    assert fake.suspended is False


def test_is_protected_flags_frozen_exe_name():
    assert processes._is_protected("OptimizerGUI.exe", "") is True


def test_is_protected_flags_self_pid_regardless_of_name():
    assert processes._is_protected("anything.exe", "", pid=os.getpid()) is True


def test_is_protected_flags_parent_pid_regardless_of_name():
    assert processes._is_protected("anything.exe", "", pid=os.getppid()) is True


def test_is_protected_does_not_flag_an_unrelated_pid():
    unrelated_pid = os.getpid() + 1  # not guaranteed to exist; identity-only check
    assert processes._is_protected("notepad.exe", r"C:\Windows\notepad.exe",
                                   pid=unrelated_pid) is False
