"""Orchestrates a full analysis pass: scan -> survey models -> reason -> merge
with safety overrides -> persist. Also applies the chosen optimization action."""
from __future__ import annotations

from . import active_windows, config, local_models, processes, reasoners
from .database import Database


class AnalysisResult:
    def __init__(self) -> None:
        self.scan_id: int = 0
        self.reasoner: str = "heuristic"
        self.foreground: str = ""
        self.active_windows: list[str] = []
        self.model_summary: dict = {}
        self.rows: list[dict] = []          # process rows + recommendation + snapshot_id

    @property
    def closable(self) -> list[dict]:
        return [r for r in self.rows if r["recommendation"] == "suggest_close"]


def _pick_chat_model(models: list[str]) -> str | None:
    """Choose a usable local chat model: skip embedding models (can't chat) and
    cloud-routed models (may need remote auth / be slow). Prefer instruct/chat."""
    usable = [m for m in models
              if "embed" not in m.lower() and ":cloud" not in m.lower()]
    if not usable:
        return None
    preferred = [m for m in usable
                 if any(k in m.lower() for k in
                        ("instruct", "qwen", "dolphin", "llama", "deepseek"))]
    return (preferred or usable)[0]


def _choose_reasoner(active: list[str], rows: list[dict], db: Database,
                     model_summary: dict) -> tuple[dict[int, dict], str]:
    """Try DeepSeek, then a local model, then heuristics. Always returns a map."""
    key = config.get_deepseek_key()
    if key:
        try:
            res = reasoners.deepseek_decide(active, rows, key)
            if res:
                return res, "deepseek"
            db.log_event("warning", "api", "DeepSeek returned unparseable output; "
                         "falling back")
        except Exception as e:  # network/auth/etc. — fall through gracefully
            db.log_event("error", "api", f"DeepSeek call failed: {e}")

    oll = model_summary.get("ollama", {})
    if oll.get("available") and oll.get("models"):
        model = _pick_chat_model(oll["models"])
        if model:
            try:
                res = reasoners.local_decide(active, rows, config.OLLAMA_HOST, model)
                if res:
                    return res, f"ollama:{model}"
            except Exception as e:
                db.log_event("error", "model", f"Ollama reasoning failed: {e}")

    lms = model_summary.get("lmstudio", {})
    if lms.get("available") and lms.get("models"):
        model = _pick_chat_model(lms["models"]) or lms["models"][0]
        try:
            res = reasoners.local_decide(active, rows, config.LMSTUDIO_HOST, model)
            if res:
                return res, f"lmstudio:{model}"
        except Exception as e:
            db.log_event("error", "model", f"LM Studio reasoning failed: {e}")

    return reasoners.heuristic_decide(active, rows), "heuristic"


def _apply_safety_overrides(row: dict, decision: dict) -> dict:
    """No reasoner — cloud or local — is allowed to close protected, active, or
    model-bound processes. This is the final guard."""
    if row["protected"]:
        return {"recommendation": "needed", "confidence": 1.0,
                "reason": "protected (override): " + decision.get("reason", "")}
    if row["uses_local_model"]:
        return {"recommendation": "needed", "confidence": 1.0,
                "reason": "in use by a local model (override)"}
    if row["is_active"]:
        return {"recommendation": "needed", "confidence": 0.95,
                "reason": "active window in use (override)"}
    return decision


def analyze(mode: str, db: Database) -> AnalysisResult:
    result = AnalysisResult()

    fg_pid, fg_title = active_windows.foreground()
    awins = active_windows.active_window_pids()
    result.foreground = fg_title
    result.active_windows = sorted(set(awins.values()))
    active_pids = set(awins) | ({fg_pid} if fg_pid else set())

    result.model_summary = local_models.survey(db)
    model_pids = result.model_summary.get("in_use_pids", set())

    rows = processes.scan(active_pids, model_pids)
    db.log_event("info", "scan",
                 f"Scanned {len(rows)} processes; {len(active_pids)} active, "
                 f"{len(model_pids)} bound to local models")

    decisions, reasoner = _choose_reasoner(result.active_windows, rows, db,
                                            result.model_summary)
    result.reasoner = reasoner

    result.scan_id = db.start_scan(
        mode=mode, foreground=fg_title, active=result.active_windows,
        process_count=len(rows), reasoner=reasoner)

    for row in rows:
        decision = decisions.get(row["pid"], {
            "recommendation": "needed", "confidence": 0.5,
            "reason": "no decision returned — kept"})
        decision = _apply_safety_overrides(row, decision)
        row.update(decision)
        # record snapshot, capture its id for later action updates
        row["snapshot_id"] = db.add_process_snapshot(result.scan_id, row)
        if row["recommendation"] == "suggest_close":
            db.log_event("suggestion", "scan",
                         f"{row['name']} (pid {row['pid']}): {row['reason']}",
                         {"pid": row["pid"], "mem_mb": row["mem_mb"],
                          "cpu": row["cpu"]})

    result.rows = rows
    return result


def apply_action(result: AnalysisResult, pids: list[int], mode: str,
                 db: Database) -> list[tuple[int, bool, str]]:
    """Execute the optimization on the given pids. mode: suspend | kill.
    suggest mode never reaches here (it just displays)."""
    outcomes: list[tuple[int, bool, str]] = []
    by_pid = {r["pid"]: r for r in result.rows}
    for pid in pids:
        row = by_pid.get(pid)
        if row and (row["protected"] or row["is_active"] or row["uses_local_model"]):
            outcomes.append((pid, False, "blocked by safety guard"))
            db.log_event("warning", "action",
                         f"Refused to {mode} protected/active pid {pid}")
            continue
        if mode == "kill":
            ok, msg = processes.kill(pid)
            action = "killed" if ok else "failed"
        elif mode == "suspend":
            ok, msg = processes.suspend(pid)
            action = "suspended" if ok else "failed"
        else:
            ok, msg, action = False, "unknown mode", "none"
        outcomes.append((pid, ok, msg))
        if row and row.get("snapshot_id"):
            db.update_action(row["snapshot_id"], action)
        db.log_event("info" if ok else "error", "action",
                     f"{mode} pid {pid} ({row['name'] if row else '?'}): {msg}")
    return outcomes


def resume_pids(pids: list[int], db: Database) -> list[tuple[int, bool, str]]:
    """Un-suspend processes the optimizer previously suspended. Reversing a
    suspend is always allowed — there is no safety gate to clear here, since
    resuming (unlike suspend/kill) can never leave the system in a worse
    state than before the optimizer touched it."""
    outcomes: list[tuple[int, bool, str]] = []
    for pid in pids:
        ok, msg = processes.resume(pid)
        outcomes.append((pid, ok, msg))
        db.log_event("info" if ok else "error", "action",
                     f"resume pid {pid}: {msg}")
    return outcomes
