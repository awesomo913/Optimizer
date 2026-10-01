"""Tests for assistant.py. No real HTTP call is ever made — assistant._post
is monkeypatched for every engine in the fallback chain."""
from __future__ import annotations

from Optimizer import assistant, config

from tests.conftest import make_row


class _FakeResult:
    def __init__(self, rows):
        self.reasoner = "heuristic"
        self.foreground = "Notepad"
        self.active_windows = ["Notepad"]
        self.model_summary = {"ollama": {"available": False, "models": []},
                              "lmstudio": {"available": False, "models": []}}
        self.rows = rows


def test_build_context_handles_no_scan_yet():
    assert assistant.build_context(None) == "No scan has been run yet."


def test_build_context_lists_closable_processes():
    rows = [make_row(pid=1, name="discord.exe", recommendation="suggest_close",
                     reason="idle background app")]
    ctx = assistant.build_context(_FakeResult(rows))

    assert "discord.exe" in ctx
    assert "suggest_close" in ctx
    assert "Suggested to close (1):" in ctx


def test_build_context_flags_protected_active_and_model(monkeypatch):
    rows = [make_row(pid=1, name="lsass.exe", protected=True, recommendation="needed",
                    reason="protected")]
    ctx = assistant.build_context(_FakeResult(rows))
    assert "[protected]" in ctx


def test_chat_tries_deepseek_first_when_key_is_set(monkeypatch, db):
    monkeypatch.setattr(config, "get_deepseek_key", lambda: "sk-test")
    calls = []

    def fake_post(url, body, headers, timeout):
        calls.append(url)
        return "deepseek reply"
    monkeypatch.setattr(assistant, "_post", fake_post)

    reply, engine = assistant.chat([{"role": "user", "content": "hi"}], "ctx", {}, db)

    assert reply == "deepseek reply"
    assert engine == "deepseek"
    assert config.DEEPSEEK_BASE in calls[0]


def test_chat_falls_back_to_ollama_when_deepseek_fails(monkeypatch, db):
    monkeypatch.setattr(config, "get_deepseek_key", lambda: "sk-test")

    def fake_post(url, body, headers, timeout):
        if config.DEEPSEEK_BASE in url:
            raise RuntimeError("network down")
        return "ollama reply"
    monkeypatch.setattr(assistant, "_post", fake_post)

    model_summary = {"ollama": {"available": True, "models": ["qwen2.5:7b-instruct"]}}
    reply, engine = assistant.chat([{"role": "user", "content": "hi"}], "ctx",
                                   model_summary, db)

    assert reply == "ollama reply"
    assert engine.startswith("ollama:")


def test_chat_offline_when_no_key_and_no_local_model(monkeypatch, db):
    monkeypatch.setattr(config, "get_deepseek_key", lambda: None)

    reply, engine = assistant.chat([{"role": "user", "content": "hi"}], "ctx", {}, db)

    assert engine == "offline"
    assert "DeepSeek" in reply


def test_chat_without_key_never_calls_deepseek(monkeypatch, db):
    """Privacy guard: with no key configured, the assistant must not even
    attempt a DeepSeek call (the opt-in contract)."""
    monkeypatch.setattr(config, "get_deepseek_key", lambda: None)
    calls = []
    monkeypatch.setattr(assistant, "_post",
                        lambda url, body, headers, timeout: calls.append(url))

    assistant.chat([{"role": "user", "content": "hi"}], "ctx", {}, db)

    assert not any(config.DEEPSEEK_BASE in u for u in calls)
