"""Tests for local_models.py. All network calls go through _http_json, which
we monkeypatch — these tests never make a real HTTP request, and
psutil.net_connections is monkeypatched too so no real connection table is read."""
from __future__ import annotations

from collections import namedtuple

import psutil
from Optimizer import local_models

Addr = namedtuple("addr", ["ip", "port"])
Conn = namedtuple("conn", ["laddr", "raddr", "pid"])


def test_ollama_models_returns_names(monkeypatch):
    monkeypatch.setattr(local_models, "_http_json",
                        lambda url, timeout=2.0: {"models": [{"name": "qwen2.5:7b"},
                                                             {"name": "llama3:8b"}]})
    assert local_models.ollama_models() == ["qwen2.5:7b", "llama3:8b"]


def test_ollama_models_empty_when_server_unreachable(monkeypatch):
    monkeypatch.setattr(local_models, "_http_json", lambda url, timeout=2.0: None)
    assert local_models.ollama_models() == []


def test_lmstudio_models_returns_ids(monkeypatch):
    monkeypatch.setattr(local_models, "_http_json",
                        lambda url, timeout=2.0: {"data": [{"id": "mistral-7b"}]})
    assert local_models.lmstudio_models() == ["mistral-7b"]


def test_pids_using_models_matches_remote_port(monkeypatch):
    conns = [Conn(laddr=Addr("127.0.0.1", 51000), raddr=Addr("127.0.0.1", 11434), pid=777)]
    monkeypatch.setattr(psutil, "net_connections", lambda kind="inet": conns)

    assert local_models.pids_using_models() == {777}


def test_pids_using_models_matches_local_port(monkeypatch):
    conns = [Conn(laddr=Addr("127.0.0.1", 1234), raddr=None, pid=888)]
    monkeypatch.setattr(psutil, "net_connections", lambda kind="inet": conns)

    assert local_models.pids_using_models() == {888}


def test_pids_using_models_ignores_unrelated_ports(monkeypatch):
    conns = [Conn(laddr=Addr("127.0.0.1", 443), raddr=Addr("1.2.3.4", 443), pid=999)]
    monkeypatch.setattr(psutil, "net_connections", lambda kind="inet": conns)

    assert local_models.pids_using_models() == set()


def test_pids_using_models_handles_access_denied(monkeypatch, caplog):
    def raiser(kind="inet"):
        raise psutil.AccessDenied()
    monkeypatch.setattr(psutil, "net_connections", raiser)

    with caplog.at_level("WARNING", logger="Optimizer.local_models"):
        result = local_models.pids_using_models()

    assert result == set()
    # A degraded safety-relevant scan must not fail silently — the model-
    # connection protection gate depends on this set being accurate.
    assert any("degraded" in r.message for r in caplog.records)


def test_survey_builds_a_summary_without_crashing(monkeypatch, db):
    monkeypatch.setattr(local_models, "ollama_models", lambda: ["qwen2.5:7b"])
    monkeypatch.setattr(local_models, "lmstudio_models", lambda: [])
    monkeypatch.setattr(local_models, "ollama_can_spawn", lambda: (True, "/usr/bin/ollama"))
    monkeypatch.setattr(local_models, "lmstudio_can_spawn", lambda: (False, "not found"))
    monkeypatch.setattr(local_models, "pids_using_models", lambda: {123})
    monkeypatch.setattr(local_models, "_http_json", lambda url, timeout=2.0: {"models": []})

    summary = local_models.survey(db)

    assert summary["ollama"]["available"] is True
    assert summary["ollama"]["models"] == ["qwen2.5:7b"]
    assert summary["lmstudio"]["available"] is False
    assert summary["in_use_pids"] == {123}
