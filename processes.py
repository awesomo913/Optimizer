"""Enumerate every running process, flag the ones tied to active work or local
models, and provide safe kill/suspend primitives that respect the protected list."""
from __future__ import annotations

import time

import psutil

from . import config


def _is_protected(name: str, exe: str) -> bool:
    n = (name or "").lower()
    if n in config.PROTECTED_NAMES:
        return True
    blob = f"{n} {(exe or '').lower()}"
    return any(h in blob for h in config.MODEL_RUNNER_HINTS)


def scan(active_pids: set[int], model_pids: set[int],
         cpu_interval: float = 0.4) -> list[dict]:
    """Snapshot all processes. `active_pids` = PIDs owning visible windows;
    `model_pids` = PIDs connected to a local model server."""
    procs = list(psutil.process_iter(["pid", "name", "exe"]))

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
                cpu = p.cpu_percent(None) / ncpu  # normalize to 0-100 of total
                mem_mb = p.memory_info().rss / (1024 * 1024)
                status = p.status()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        rows.append({
            "pid": p.pid,
            "name": name,
            "exe": exe,
            "cpu": round(cpu, 1),
            "mem_mb": round(mem_mb, 1),
            "status": status,
            "is_active": p.pid in active_pids,
            "uses_local_model": p.pid in model_pids,
            "protected": _is_protected(name, exe),
        })
    return rows


def kill(pid: int) -> tuple[bool, str]:
    try:
        p = psutil.Process(pid)
        if _is_protected(p.name(), p.exe() if p.is_running() else ""):
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


def suspend(pid: int) -> tuple[bool, str]:
    try:
        p = psutil.Process(pid)
        if _is_protected(p.name(), p.exe() if p.is_running() else ""):
            return False, "protected process refused"
        p.suspend()
        return True, "suspended"
    except psutil.NoSuchProcess:
        return False, "no such process"
    except (psutil.AccessDenied, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def resume(pid: int) -> tuple[bool, str]:
    try:
        psutil.Process(pid).resume()
        return True, "resumed"
    except psutil.NoSuchProcess:
        return False, "no such process"
    except (psutil.AccessDenied, OSError) as e:
        return False, f"{type(e).__name__}: {e}"
