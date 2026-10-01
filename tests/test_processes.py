"""Tests for processes.kill / suspend / resume — the action-time safety gate.

These never touch a real process: psutil.Process is replaced with a fake
that records what was called, so we can prove the protected-process check
happens BEFORE terminate()/suspend() is ever invoked.
"""
from __future__ import annotations

import psutil
import pytest
from Optimizer import processes


class FakeProcess:
    def __init__(self, name="notepad.exe", exe=r"C:\Windows\notepad.exe",
                running=True, raise_on_init=None):
        if raise_on_init:
            raise raise_on_init
        self._name = name
        self._exe = exe
        self._running = running
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
