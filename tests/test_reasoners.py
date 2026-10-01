"""Tests for reasoners.py — the heuristic fallback (always available, no
network) and the JSON parsing shared by the cloud/local engines."""
from __future__ import annotations

from Optimizer.reasoners import _parse_items, heuristic_decide

from tests.conftest import make_row


def test_heuristic_keeps_protected_processes():
    rows = [make_row(pid=1, protected=True, name="lsass.exe")]
    out = heuristic_decide([], rows)
    assert out[1]["recommendation"] == "needed"
    assert out[1]["confidence"] == 1.0


def test_heuristic_keeps_model_bound_processes():
    rows = [make_row(pid=1, uses_local_model=True, name="ollama.exe")]
    out = heuristic_decide([], rows)
    assert out[1]["recommendation"] == "needed"


def test_heuristic_keeps_active_window_processes():
    rows = [make_row(pid=1, is_active=True, name="code.exe")]
    out = heuristic_decide([], rows)
    assert out[1]["recommendation"] == "needed"
    assert out[1]["confidence"] == 0.9


def test_heuristic_flags_heavy_known_bloat_as_suggest_close():
    rows = [make_row(pid=1, name="discord.exe", mem_mb=400, cpu=8)]
    out = heuristic_decide([], rows)
    assert out[1]["recommendation"] == "suggest_close"
    assert out[1]["confidence"] == 0.75


def test_heuristic_flags_light_known_bloat_with_lower_confidence():
    rows = [make_row(pid=1, name="onedrive.exe", mem_mb=10, cpu=0.1)]
    out = heuristic_decide([], rows)
    assert out[1]["recommendation"] == "suggest_close"
    assert out[1]["confidence"] == 0.55


def test_heuristic_flags_idle_heavy_unknown_process_with_low_confidence():
    rows = [make_row(pid=1, name="some_unknown_app.exe", mem_mb=500, cpu=10)]
    out = heuristic_decide([], rows)
    assert out[1]["recommendation"] == "suggest_close"
    assert out[1]["confidence"] == 0.45


def test_heuristic_keeps_low_impact_unknown_process():
    rows = [make_row(pid=1, name="some_tiny_tool.exe", mem_mb=5, cpu=0.1)]
    out = heuristic_decide([], rows)
    assert out[1]["recommendation"] == "needed"
    assert out[1]["confidence"] == 0.5


def test_heuristic_never_recommends_closing_a_safety_relevant_row():
    """Cross-check: no matter how 'heavy' a protected/active/model row looks,
    the heuristic must never suggest closing it."""
    rows = [
        make_row(pid=1, protected=True, mem_mb=9999, cpu=99, name="svchost.exe"),
        make_row(pid=2, is_active=True, mem_mb=9999, cpu=99, name="chrome.exe"),
        make_row(pid=3, uses_local_model=True, mem_mb=9999, cpu=99, name="ollama.exe"),
    ]
    out = heuristic_decide([], rows)
    assert all(d["recommendation"] == "needed" for d in out.values())


# ---- _parse_items ------------------------------------------------------------

def test_parse_items_extracts_valid_json_embedded_in_prose():
    text = ('Sure, here you go:\n'
           '{"items":[{"pid":10,"recommendation":"needed","confidence":0.9,'
           '"reason":"active"}]}\nHope that helps!')
    out = _parse_items(text)
    assert out == {10: {"recommendation": "needed", "confidence": 0.9, "reason": "active"}}


def test_parse_items_returns_none_on_garbage():
    assert _parse_items("not json at all") is None


def test_parse_items_returns_none_on_empty_items_list():
    assert _parse_items('{"items":[]}') is None


def test_parse_items_defaults_missing_fields():
    out = _parse_items('{"items":[{"pid":5}]}')
    assert out[5] == {"recommendation": "needed", "confidence": 0.5, "reason": ""}


def test_parse_items_truncates_long_reason():
    long_reason = "x" * 500
    out = _parse_items(f'{{"items":[{{"pid":1,"reason":"{long_reason}"}}]}}')
    assert len(out[1]["reason"]) == 200
