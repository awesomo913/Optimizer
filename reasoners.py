"""Reasoning engines that decide which processes are needed.

Order of preference: DeepSeek API -> local model (Ollama/LM Studio) -> built-in
heuristics. All three return the same shape so the analyzer is engine-agnostic."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config

SYSTEM_PROMPT = (
    "You are a Windows performance optimizer. Given the user's currently active "
    "windows and a list of running processes, decide for EACH process whether it "
    "is needed for the user's current work. Be conservative: when unsure, keep it. "
    "Never recommend closing OS-critical processes or anything connected to a local "
    "model. Respond ONLY with a JSON object: "
    '{"items":[{"pid":int,"recommendation":"needed|suggest_close","confidence":0..1,'
    '"reason":"short"}]}'
)


def _build_user_payload(active_windows: list[str], procs: list[dict]) -> str:
    # Trim to a compact, token-friendly view.
    slim = [
        {"pid": p["pid"], "name": p["name"], "cpu": p["cpu"],
         "mem_mb": p["mem_mb"], "active": p["is_active"],
         "uses_model": p["uses_local_model"], "protected": p["protected"]}
        for p in procs
    ]
    return json.dumps({"active_windows": active_windows, "processes": slim})


def _parse_items(text: str) -> dict[int, dict] | None:
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        data = json.loads(text[start:end])
        out: dict[int, dict] = {}
        for it in data.get("items", []):
            pid = int(it["pid"])
            out[pid] = {
                "recommendation": it.get("recommendation", "needed"),
                "confidence": float(it.get("confidence", 0.5)),
                "reason": str(it.get("reason", ""))[:200],
            }
        return out or None
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


# ---- DeepSeek cloud --------------------------------------------------------
def deepseek_decide(active_windows: list[str], procs: list[dict],
                    api_key: str) -> dict[int, dict] | None:
    body = json.dumps({
        "model": config.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_payload(active_windows, procs)},
        ],
        "temperature": 0.2,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{config.DEEPSEEK_BASE}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return _parse_items(content)


# ---- Local model (Ollama / LM Studio, OpenAI-compatible) -------------------
def local_decide(active_windows: list[str], procs: list[dict],
                 host: str, model: str) -> dict[int, dict] | None:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_payload(active_windows, procs)},
        ],
        "temperature": 0.2,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return _parse_items(content)


# ---- Heuristic fallback ----------------------------------------------------
# Background processes commonly safe to close when not part of active work.
_BLOAT_HINTS = (
    "update", "updater", "crashreport", "crashhandler", "helper", "telemetry",
    "googleupdate", "edgeupdate", "onedrive", "teams", "spotify", "steamwebhelper",
    "discord", "slack", "msedge", "chrome", "skype", "epicgames", "razer",
    "logioptions", "armoury", "icue", "nvcontainer", "adobe", "acrotray",
)


def heuristic_decide(active_windows: list[str], procs: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for p in procs:
        pid = p["pid"]
        if p["protected"]:
            out[pid] = {"recommendation": "needed", "confidence": 1.0,
                        "reason": "OS-critical / protected"}
            continue
        if p["uses_local_model"]:
            out[pid] = {"recommendation": "needed", "confidence": 1.0,
                        "reason": "connected to a local model"}
            continue
        if p["is_active"]:
            out[pid] = {"recommendation": "needed", "confidence": 0.9,
                        "reason": "owns an active window you're using"}
            continue
        name = p["name"].lower()
        looks_bloat = any(h in name for h in _BLOAT_HINTS)
        heavy = p["mem_mb"] > 300 or p["cpu"] > 5
        if looks_bloat and heavy:
            out[pid] = {"recommendation": "suggest_close", "confidence": 0.75,
                        "reason": f"background app, no active window, "
                                  f"{p['mem_mb']:.0f}MB / {p['cpu']:.0f}% CPU"}
        elif looks_bloat:
            out[pid] = {"recommendation": "suggest_close", "confidence": 0.55,
                        "reason": "known background/companion app, not in use"}
        elif heavy and not p["is_active"]:
            out[pid] = {"recommendation": "suggest_close", "confidence": 0.45,
                        "reason": f"idle but heavy ({p['mem_mb']:.0f}MB / "
                                  f"{p['cpu']:.0f}% CPU)"}
        else:
            out[pid] = {"recommendation": "needed", "confidence": 0.5,
                        "reason": "low impact / unknown — kept"}
    return out
