"""Conversational assistant — lets the user talk through what's running, why
something is suggested for closing, and what they actually want to do.

Same spirit as the Quasar chat panel: a system prompt + a live context block
describing the current scan, backed by DeepSeek with a local-model fallback."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config

SYSTEM = (
    "You are the built-in assistant inside a Windows process-optimizer app. "
    "You can see a live snapshot of the user's current scan: their active "
    "windows, running processes, memory/CPU use, which processes are protected, "
    "active, or bound to a local AI model, and which the optimizer suggested "
    "closing and why. "
    "Help the user understand what's running and decide what to close. Be concise "
    "and practical — short paragraphs or bullets. When asked about a specific "
    "process, say what it likely is, whether it's safe to close, and the tradeoff. "
    "NEVER advise closing anything marked protected, active, or model-bound; those "
    "are blocked by the app for safety anyway. If you're unsure what a process is, "
    "say so rather than guessing."
)


def build_context(result) -> str:
    """Compact, token-aware snapshot of the current AnalysisResult for the model."""
    if result is None:
        return "No scan has been run yet."

    oll = result.model_summary.get("ollama", {})
    lms = result.model_summary.get("lmstudio", {})
    rows = sorted(result.rows, key=lambda r: -r["mem_mb"])

    def fmt(r):
        flags = []
        if r["protected"]:
            flags.append("protected")
        if r["is_active"]:
            flags.append("active")
        if r["uses_local_model"]:
            flags.append("model")
        return (f"{r['name']} pid={r['pid']} cpu={r['cpu']:.0f}% "
                f"mem={r['mem_mb']:.0f}MB {r['recommendation']}"
                f"{' [' + ','.join(flags) + ']' if flags else ''}"
                f" — {r['reason']}")

    closable = [r for r in rows if r["recommendation"] == "suggest_close"]
    top = rows[:40]

    lines = [
        f"Reasoning engine used: {result.reasoner}",
        f"Foreground window: {result.foreground or '(unknown)'}",
        f"Active programs: {', '.join(result.active_windows) or '(none)'}",
        f"Ollama: {'on' if oll.get('available') else 'off'}, "
        f"models={oll.get('models', [])}, can_spawn={oll.get('can_spawn')}",
        f"LM Studio: {'on' if lms.get('available') else 'off'}, "
        f"can_spawn={lms.get('can_spawn')}",
        f"Total processes: {len(rows)}",
        "",
        f"Suggested to close ({len(closable)}):",
        *[f"  - {fmt(r)}" for r in closable],
        "",
        "Top processes by memory:",
        *[f"  - {fmt(r)}" for r in top],
    ]
    return "\n".join(lines)


def _post(url: str, body: dict, headers: dict, timeout: float) -> str:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp["choices"][0]["message"]["content"].strip()


def _pick_chat_model(models: list[str]) -> str | None:
    usable = [m for m in models
              if "embed" not in m.lower() and ":cloud" not in m.lower()]
    if not usable:
        return None
    preferred = [m for m in usable if any(
        k in m.lower() for k in ("instruct", "qwen", "dolphin", "llama", "deepseek"))]
    return (preferred or usable)[0]


def chat(history: list[dict], context: str, model_summary: dict, db) -> tuple[str, str]:
    """history: list of {'role','content'} (user/assistant turns).
    Returns (reply_text, engine_label). Tries DeepSeek -> local -> offline note."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "system", "content": "CURRENT SYSTEM SNAPSHOT:\n" + context},
        *history,
    ]

    key = config.get_deepseek_key()
    if key:
        try:
            reply = _post(
                f"{config.DEEPSEEK_BASE}/chat/completions",
                {"model": config.DEEPSEEK_MODEL, "messages": messages,
                 "temperature": 0.4, "stream": False},
                {"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"}, timeout=90)
            return reply, "deepseek"
        except Exception as e:
            db.log_event("error", "api", f"Assistant DeepSeek call failed: {e}")

    oll = (model_summary or {}).get("ollama", {})
    if oll.get("available") and oll.get("models"):
        model = _pick_chat_model(oll["models"])
        if model:
            try:
                reply = _post(
                    f"{config.OLLAMA_HOST}/v1/chat/completions",
                    {"model": model, "messages": messages,
                     "temperature": 0.4, "stream": False},
                    {"Content-Type": "application/json"}, timeout=180)
                return reply, f"ollama:{model}"
            except Exception as e:
                db.log_event("error", "model", f"Assistant Ollama call failed: {e}")

    lms = (model_summary or {}).get("lmstudio", {})
    if lms.get("available") and lms.get("models"):
        model = _pick_chat_model(lms["models"]) or lms["models"][0]
        try:
            reply = _post(
                f"{config.LMSTUDIO_HOST}/v1/chat/completions",
                {"model": model, "messages": messages,
                 "temperature": 0.4, "stream": False},
                {"Content-Type": "application/json"}, timeout=180)
            return reply, f"lmstudio:{model}"
        except Exception as e:
            db.log_event("error", "model", f"Assistant LM Studio call failed: {e}")

    return ("I can't reach DeepSeek or a local model right now, so I can't chat. "
            "Check your DeepSeek key (Set DeepSeek Key…) or that Ollama is running. "
            "The scan results and suggestions in the table are still valid."), "offline"
