"""Configuration, paths, and the hard protected-process safety list."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "optimizer.db"
CONFIG_PATH = DATA_DIR / "config.json"

# Local model server endpoints we know how to talk to.
OLLAMA_HOST = "http://127.0.0.1:11434"
LMSTUDIO_HOST = "http://127.0.0.1:1234"
MODEL_PORTS = {11434, 1234}  # ports that indicate a process is talking to a local model

DEEPSEEK_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

# Processes that must NEVER be killed or suspended. Killing these can blue-screen,
# log you out, or hard-freeze Windows. This list is enforced regardless of mode.
PROTECTED_NAMES = {
    "system", "system idle process", "registry", "memory compression",
    "memcompression",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "lsaiso.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe",
    "explorer.exe", "ntoskrnl.exe", "taskhostw.exe", "sihost.exe",
    "ctfmon.exe", "spoolsv.exe", "audiodg.exe", "conhost.exe", "dllhost.exe",
    "runtimebroker.exe", "searchindexer.exe", "wudfhost.exe", "wininit",
    # Security / antivirus — disabling these weakens the machine. Never touch.
    "msmpeng.exe", "nissrv.exe", "mpdefendercoreservice.exe", "mssense.exe",
    "securityhealthservice.exe", "securityhealthsystray.exe", "smartscreen.exe",
    "python.exe",  # don't let the optimizer kill itself / its own runtime
    "pythonw.exe",
    "optimizergui.exe",  # the frozen PyInstaller build of this app (build.py)
}

# Substrings that mark a process as part of our own model stack — never touch.
MODEL_RUNNER_HINTS = ("ollama", "lm studio", "lmstudio", "lms.exe")


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    _ensure_data_dir()
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(cfg: dict) -> None:
    _ensure_data_dir()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def get_deepseek_key() -> str | None:
    """Key resolution order: config.json -> DEEPSEEK_API_KEY env var."""
    cfg = load_config()
    key = cfg.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY")
    return key.strip() if key else None


def set_deepseek_key(key: str) -> None:
    cfg = load_config()
    cfg["deepseek_api_key"] = key.strip()
    save_config(cfg)


def log_file_path() -> Path:
    """Where the app writes its log (Windows: %LOCALAPPDATA%/Optimizer/logs)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state")
    return Path(base) / "Optimizer" / "logs" / "app.log"


def setup_logging() -> Path | None:
    """Log to a file (always) and to stderr when one exists.

    A windowed exe (built with PyInstaller's --windowed flag) has no stderr,
    so without the file handler every log line would be silently dropped.
    Returns the log path, or None if it could not be created (logging then
    falls back to stderr only, if stderr exists)."""
    if getattr(setup_logging, "_done", False):
        return log_file_path()
    setup_logging._done = True  # type: ignore[attr-defined]

    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)

    path = log_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        logging.getLogger(__name__).warning("Could not open log file %s: %s", path, exc)
        return None
    handler.setFormatter(fmt)
    root.addHandler(handler)
    return path
