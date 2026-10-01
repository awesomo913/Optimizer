"""Enumerate every running process, flag the ones tied to active work or local
models, and provide safe kill/suspend primitives that respect the protected
list.

PID reuse matters here: Windows (and every OS) recycles process ids. A pid
captured during a scan can belong to a completely different process by the
time the user clicks OPTIMIZE a few seconds later — the old process could
have exited and a new, unrelated one could have been assigned the same pid.
`create_time()` (the process's start timestamp, from psutil) is what makes a
pid safe to re-identify: two processes essentially never share both the same
pid *and* the same create_time at the same moment. Every action-time function
here takes an `expected_create_time` and refuses to act if it doesn't match
the live process, or if the pid is gone entirely.
"""
from __future__ import annotations

import os
import time

import psutil

from . import config

# This process's own pid/parent-pid — never act on either, regardless of name
# or exe. Captured once at import time; a process's own pid/ppid don't change
# during its lifetime, so this is safe to cache.
_SELF_PID = os.getpid()
_SELF_PARENT_PID = os.getppid()


def _is_self_or_parent(pid: int | None) -> bool:
    return pid is not None and pid in (_SELF_PID, _SELF_PARENT_PID)


def _is_protected(name: str, exe: str, pid: int | None = None) -> bool:
    if _is_self_or_parent(pid):
        return True
    n = (name or "").lower()
    if n in config.PROTECTED_NAMES:
        return True
    blob = f"{n} {(exe or '').lower()}"
    return any(h in blob for h in config.MODEL_RUNNER_HINTS)


def scan(active_pids: set[int], model_pids: set[int],
         cpu_interval: float = 0.4) -> list[dict]:
    """Snapshot all processes. `active_pids` = PIDs owning visible windows;
    `model_pids` = PIDs connected to a local model server."""
    procs = list(psutil.process_iter(["pid", "name", "exe", "create_time"]))

    # Prime cpu_percent (first call always reads 0).
    for p in procs:
        try:
            p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(cpu_interval)

    ncpu = psutil.cpu_count() or 1
    rows: list[dict] = []
    for p in procs:
        try:
            with p.oneshot():
                name = p.info.get("name") or f"pid{p.pid}"
                exe = p.info.get("exe") or ""
                create_time = p.info.get("create_time")
                if create_time is None:
                    create_time = p.create_time()
                cpu = p.cpu_percent(None) / ncpu  # normalize to 0-100 of total
                mem_mb = p.memory_info().rss / (1024 * 1024)
                status = p.status()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        rows.append({
            "pid": p.pid,
            "name": name,
            "exe": exe,
            "create_time": create_time,
            "cpu": round(cpu, 1),
            "mem_mb": round(mem_mb, 1),
            "status": status,
            "is_active": p.pid in active_pids,
            "uses_local_model": p.pid in model_pids,
            "protected": _is_protected(name, exe, p.pid),
        })
    return rows


_GONE = "gone"
_REUSED = "reused"


def _resolve_live(pid: int, expected_create_time: float | None
                  ) -> tuple[psutil.Process | None, str | None]:
    """Re-fetch the process by pid and verify its identity against the
    create_time recorded at scan time. Returns (process, None) if it's
    genuinely the same process, or (None, _GONE | _REUSED) otherwise."""
    try:
        p = psutil.Process(pid)
        if not p.is_running():
            return None, _GONE
    except psutil.NoSuchProcess:
        return None, _GONE

    if expected_create_time is not None:
        try:
            live_create_time = p.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None, _GONE
        # Compare with a small tolerance — psutil's float timestamp can have
        # sub-millisecond rounding differences between two reads of the same
        # process depending on platform; a reused pid's create_time differs
        # by whole seconds at minimum (processes don't start in the same tick
        # another one exited).
        if abs(live_create_time - expected_create_time) > 1.0:
            return None, _REUSED

    return p, None


def is_same_process(pid: int, expected_create_time: float | None) -> bool:
    """Read-only identity check: is `pid` still running the same process it
    was when `expected_create_time` was recorded? Used to validate persisted
    state (e.g. the suspended-processes list, reloaded at startup) without
    taking any action."""
    p, _reason = _resolve_live(pid, expected_create_time)
    return p is not None


def kill(pid: int, expected_create_time: float | None = None) -> tuple[bool, str]:
    p, reason = _resolve_live(pid, expected_create_time)
    if p is None:
        if reason == _GONE:
            return True, "already gone"  # nothing left to kill -> success
        return False, "pid was reused by a different process since scan"
    try:
        if _is_protected(p.name(), p.exe() if p.is_running() else "", pid):
            return False, "protected process refused"
        p.terminate()
        try:
            p.wait(timeout=3)
        except psutil.TimeoutExpired:
            p.kill()
        return True, "terminated"
    except psutil.NoSuchProcess:
        return True, "already gone"
    except (psutil.AccessDenied, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def suspend(pid: int, expected_create_time: float | None = None) -> tuple[bool, str]:
    p, reason = _resolve_live(pid, expected_create_time)
    if p is None:
        if reason == _GONE:
            return False, "no such process"
        return False, "pid was reused by a different process since scan"
    try:
        if _is_protected(p.name(), p.exe() if p.is_running() else "", pid):
            return False, "protected process refused"
        p.suspend()
        return True, "suspended"
    except psutil.NoSuchProcess:
        return False, "no such process"
    except (psutil.AccessDenied, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def resume(pid: int, expected_create_time: float | None = None) -> tuple[bool, str]:
    """Un-suspend a process. Unlike kill/suspend, there's no protected-name
    gate here (resuming can't make the system worse off) — but identity is
    still verified: if the pid has been reused since we suspended it, resuming
    whatever now holds that pid would affect a process we never touched."""
    p, reason = _resolve_live(pid, expected_create_time)
    if p is None:
        if reason == _GONE:
            return False, "no such process"
        return False, "pid was reused by a different process since scan"
    try:
        p.resume()
        return True, "resumed"
    except psutil.NoSuchProcess:
        return False, "no such process"
    except (psutil.AccessDenied, OSError) as e:
        return False, f"{type(e).__name__}: {e}"
