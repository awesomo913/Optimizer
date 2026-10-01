"""Orchestrates a full analysis pass: scan -> survey models -> reason -> merge
with safety overrides -> persist. Also applies the chosen optimization action."""
from __future__ import annotations

import logging

from . import active_windows, config, local_models, processes, reasoners
from .database import Database

logger = logging.getLogger(__name__)


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
    # Same definition as active_windows.active_pids(), computed from the
    # fg_pid/awins already fetched above so we don't call the Win32 APIs twice.
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
    suggest mode never reaches here (it just displays).

    A scan can be seconds (or, if the user steps away, much longer) old by
    the time this runs, and pids get reused by the OS. So beyond the
    scan-time `protected`/`is_active`/`uses_local_model` flags on each row,
    this re-derives the safety-relevant facts live, right before acting:

    1. Identity — re-fetch the process by pid and compare its create_time
       against what the scan recorded. A mismatch (or the pid no longer
       existing) means the pid was reused by an unrelated process, or the
       original process already exited; either way, refuse.
    2. Active window / model connection — re-query active_windows and
       local_models *immediately before acting on each pid* (not once for
       the whole batch), so a process that became the foreground window or
       started talking to a local model in the gap between scan and click —
       or even between two pids in the same multi-select batch — is still
       protected.

    processes.kill/suspend re-check the protected *name* and identity again
    themselves as a third, independent layer — this function's live
    active/model re-check is what they can't do on their own (they have no
    notion of "window" or "model connection").
    """
    outcomes: list[tuple[int, bool, str]] = []
    by_pid = {r["pid"]: r for r in result.rows}

    for pid in pids:
        row = by_pid.get(pid)
        if row is None:
            outcomes.append((pid, False, "unknown pid (not part of this scan)"))
            continue
        if row["protected"] or row["is_active"] or row["uses_local_model"]:
            outcomes.append((pid, False, "blocked by safety guard"))
            db.log_event("warning", "action",
                         f"Refused to {mode} protected/active/model-bound pid {pid}")
            continue

        # Re-derived fresh for THIS pid, right before acting on it — not
        # once for the whole batch — so a long multi-pid batch can't rely on
        # a world-state snapshot that's gone stale by the time it reaches the
        # last few pids.
        if pid in active_windows.active_pids():
            outcomes.append((pid, False, "now has an active window — refused"))
            db.log_event("warning", "action",
                         f"Refused to {mode} pid {pid}: became active since scan")
            continue
        if pid in local_models.pids_using_models():
            outcomes.append((pid, False, "now connected to a local model — refused"))
            db.log_event("warning", "action",
                         f"Refused to {mode} pid {pid}: became model-bound since scan")
            continue

        create_time = row.get("create_time")
        if mode == "kill":
            ok, msg = processes.kill(pid, create_time)
            action = "killed" if ok else "failed"
        elif mode == "suspend":
            ok, msg = processes.suspend(pid, create_time)
            action = "suspended" if ok else "failed"
        else:
            ok, msg, action = False, "unknown mode", "none"
        outcomes.append((pid, ok, msg))
        if row.get("snapshot_id"):
            db.update_action(row["snapshot_id"], action)
        db.log_event("info" if ok else "error", "action",
                     f"{mode} pid {pid} ({row['name']}): {msg}")
    return outcomes


def load_suspended(db: Database) -> dict[int, dict]:
    """Reload the persisted suspended-processes list (so "Resume Suspended"
    still works after the app restarts). Each entry is re-verified against
    the live process table — same pid AND the same create_time — because the
    gap between "we suspended this" and "the app restarted" can be arbitrarily
    long; the process may have exited, or its pid may have been reused by
    something else entirely. Entries that no longer check out are dropped
    (and removed from the DB) rather than risking a resume() on the wrong
    process later. Returns {pid: {"name": str, "create_time": float}}."""
    live: dict[int, dict] = {}
    for entry in db.list_suspended():
        pid, name, create_time = entry["pid"], entry["name"], entry["create_time"]
        if processes.is_same_process(pid, create_time):
            live[pid] = {"name": name, "create_time": create_time}
            continue
        try:
            db.remove_suspended(pid)
        except Exception:
            # A DB hiccup here must not abort startup (this runs from
            # gui.OptimizerApp.__init__) or skip validating the rest of the
            # persisted list — log and keep going; the stale entry will
            # simply be re-validated (and retried) next startup.
            logger.exception("Failed to remove stale suspended entry for pid %s", pid)
        db.log_event(
            "info", "action",
            f"Dropped stale suspended-process entry for pid {pid} ({name}): "
            "no longer the same process")
    return live


def resume_pids(entries: list[dict], db: Database) -> list[tuple[int, bool, str]]:
    """Un-suspend processes the optimizer previously suspended.

    `entries` are {"pid": int, "create_time": float, "name": str} — the
    create_time recorded when we suspended it (loaded from the persisted
    suspended-processes list, which survives a restart). Reversing a suspend
    has no protected/active/model gate — there is nothing it could do that
    makes the system worse than before the optimizer touched it — but
    identity is still checked: if the pid has been reused since we suspended
    it, "resuming" would act on a process we never touched, not the one we
    suspended."""
    outcomes: list[tuple[int, bool, str]] = []
    for entry in entries:
        pid = entry["pid"]
        ok, msg = processes.resume(pid, entry.get("create_time"))
        outcomes.append((pid, ok, msg))
        db.log_event("info" if ok else "error", "action", f"resume pid {pid}: {msg}")
    return outcomes
