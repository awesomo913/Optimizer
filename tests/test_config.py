"""Tests for config.py — key resolution order and config persistence.
Patches config.DATA_DIR/CONFIG_PATH to a tmp_path so nothing touches the
user's real data/config.json (which can hold a real API key)."""
from __future__ import annotations

import logging

from Optimizer import config


def _point_at_tmp(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "CONFIG_PATH", data_dir / "config.json")


def test_load_config_returns_empty_dict_when_no_file(monkeypatch, tmp_path):
    _point_at_tmp(monkeypatch, tmp_path)
    assert config.load_config() == {}


def test_save_and_load_config_roundtrip(monkeypatch, tmp_path):
    _point_at_tmp(monkeypatch, tmp_path)
    config.save_config({"deepseek_api_key": "sk-test-123"})

    assert config.load_config() == {"deepseek_api_key": "sk-test-123"}


def test_load_config_survives_corrupt_json(monkeypatch, tmp_path):
    _point_at_tmp(monkeypatch, tmp_path)
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text("{not valid json", encoding="utf-8")

    assert config.load_config() == {}


def test_get_deepseek_key_prefers_config_file_over_env(monkeypatch, tmp_path):
    _point_at_tmp(monkeypatch, tmp_path)
    config.set_deepseek_key("from-config")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")

    assert config.get_deepseek_key() == "from-config"


def test_get_deepseek_key_falls_back_to_env_var(monkeypatch, tmp_path):
    _point_at_tmp(monkeypatch, tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")

    assert config.get_deepseek_key() == "from-env"


def test_get_deepseek_key_is_none_when_neither_is_set(monkeypatch, tmp_path):
    _point_at_tmp(monkeypatch, tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    assert config.get_deepseek_key() is None


def test_set_deepseek_key_strips_whitespace(monkeypatch, tmp_path):
    _point_at_tmp(monkeypatch, tmp_path)
    config.set_deepseek_key("  sk-abc  \n")

    assert config.get_deepseek_key() == "sk-abc"


def test_set_deepseek_key_empty_string_falls_back_to_env(monkeypatch, tmp_path):
    _point_at_tmp(monkeypatch, tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    config.set_deepseek_key("")

    # an empty saved key must not shadow the env var with an empty string
    assert config.get_deepseek_key() == "from-env"


def test_log_file_path_uses_localappdata_when_set(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\fake\LocalAppData")

    path = config.log_file_path()

    assert str(path) == r"C:\fake\LocalAppData\Optimizer\logs\app.log"


def test_setup_logging_creates_a_file_and_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    # Reset the "already configured" guard so this test is independent of
    # whatever earlier tests/imports may have done to the root logger.
    if hasattr(config.setup_logging, "_done"):
        delattr(config.setup_logging, "_done")
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path1 = config.setup_logging()
        path2 = config.setup_logging()  # should not add a second handler set
        assert path1 == path2
        assert path1.exists()
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
        if hasattr(config.setup_logging, "_done"):
            delattr(config.setup_logging, "_done")
