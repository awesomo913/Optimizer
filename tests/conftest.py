"""Shared pytest fixtures for the Optimizer test suite.

Nothing here touches a real process, the real registry, or the network —
per project policy a process-killer's tests must be fully offline and must
never act on the machine running them.
"""
from __future__ import annotations

import pytest
from Optimizer.database import Database


@pytest.fixture
def db(tmp_path):
    """A real SQLite Database backed by a throwaway file — exercises the
    actual schema/queries without touching the user's real data/ directory."""
    return Database(path=str(tmp_path / "test.db"))


def make_row(
    pid: int = 1234,
    name: str = "notepad.exe",
    exe: str = r"C:\Windows\notepad.exe",
    cpu: float = 1.0,
    mem_mb: float = 50.0,
    status: str = "running",
    is_active: bool = False,
    uses_local_model: bool = False,
    protected: bool = False,
    **extra,
) -> dict:
    """Build a process-row dict shaped like processes.scan()'s output.
    Extra keys (e.g. recommendation/confidence/reason, added after a
    reasoner runs) can be passed through via **extra."""
    row = {
        "pid": pid,
        "name": name,
        "exe": exe,
        "cpu": cpu,
        "mem_mb": mem_mb,
        "status": status,
        "is_active": is_active,
        "uses_local_model": uses_local_model,
        "protected": protected,
    }
    row.update(extra)
    return row
