"""Discover local LLM stacks (Ollama + LM Studio), figure out which running
programs are actively connected to them, and whether models can be spawned
later on demand. Everything found is logged for future tuning."""
from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

import psutil

from . import config


def _http_json(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def ollama_models() -> list[str]:
    data = _http_json(f"{config.OLLAMA_HOST}/api/tags")
    if not data:
        return []
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def lmstudio_models() -> list[str]:
    data = _http_json(f"{config.LMSTUDIO_HOST}/v1/models")
    if not data:
        return []
    return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


def ollama_can_spawn() -> tuple[bool, str]:
    exe = shutil.which("ollama")
    if exe:
        return True, exe
    # default Windows install location
    guess = Path.home() / "AppData/Local/Programs/Ollama/ollama.exe"
    if guess.exists():
        return True, str(guess)
    return False, "ollama executable not found on PATH or default location"


def lmstudio_can_spawn() -> tuple[bool, str]:
    exe = shutil.which("lms")
    if exe:
        return True, exe
    guess = Path.home() / "AppData/Local/LM-Studio/lms.exe"
    if guess.exists():
        return True, str(guess)
    return False, "LM Studio CLI (lms) not found; cannot auto-spawn"


def pids_using_models() -> set[int]:
    """PIDs with an active network connection to a known local-model port.
    These are programs currently relying on a local model -> never disturb."""
    using: set[int] = set()
    try:
        for conn in psutil.net_connections(kind="inet"):
            port = None
            if conn.raddr and conn.raddr.port in config.MODEL_PORTS:
                port = conn.raddr.port
            elif conn.laddr and conn.laddr.port in config.MODEL_PORTS:
                port = conn.laddr.port
            if port and conn.pid:
                using.add(conn.pid)
    except (psutil.AccessDenied, OSError):
        pass
    return using


def survey(db) -> dict:
    """Probe both providers, record state to the database, return a summary."""
    in_use_pids = pids_using_models()

    oll = ollama_models()
    oll_spawn, oll_note = ollama_can_spawn()
    oll_available = bool(oll) or _http_json(f"{config.OLLAMA_HOST}/api/tags") is not None
    if not oll and not oll_available and not oll_spawn:
        db.log_event("warning", "model",
                     "Ollama not running and cannot be spawned later", oll_note)
    for m in (oll or ["<none>"]):
        db.log_model_status("ollama", m, oll_available, oll_spawn,
                            bool(in_use_pids), oll_note if not oll_spawn else "")

    lms = lmstudio_models()
    lms_spawn, lms_note = lmstudio_can_spawn()
    lms_available = bool(lms)
    if not lms_available and not lms_spawn:
        db.log_event("info", "model",
                     "LM Studio not running and no CLI to spawn it", lms_note)
    for m in (lms or ["<none>"]):
        db.log_model_status("lmstudio", m, lms_available, lms_spawn,
                            bool(in_use_pids), lms_note if not lms_spawn else "")

    summary = {
        "ollama": {"models": oll, "available": oll_available,
                   "can_spawn": oll_spawn, "note": oll_note},
        "lmstudio": {"models": lms, "available": lms_available,
                     "can_spawn": lms_spawn, "note": lms_note},
        "in_use_pids": in_use_pids,
    }
    return summary
