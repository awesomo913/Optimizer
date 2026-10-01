# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **PID reuse protection.** Operating systems recycle process ids, so a pid captured at scan time could belong to an unrelated process by the time the user clicked OPTIMIZE. Every process row now carries `create_time`; `analyzer.apply_action` and `processes.kill`/`suspend`/`resume` re-fetch the live process and refuse if its `create_time` doesn't match, or it no longer exists.
- **Live re-derivation at apply time.** `apply_action` now re-queries `active_windows`/`local_models` *live*, right before acting, instead of trusting only the scan-time `is_active`/`uses_local_model` flags — a process that became the foreground window or started talking to a local model after the scan is still refused.
- **Suspended-process tracking is persisted**, not just kept in an in-memory dict in the GUI. It's written to the database on every successful suspend/resume/kill and reloaded on startup (`analyzer.load_suspended()`), with each entry re-verified against the live process table and dropped if it no longer matches — so **Resume Suspended** keeps working across a restart.
- **Explicit self-protection.** The optimizer can never act on its own process or its parent (`os.getpid()`/`os.getppid()`, checked by pid regardless of reported name), and the frozen build's exe name (`OptimizerGUI.exe`) was added to `config.PROTECTED_NAMES`.
- **`processes.kill`/`suspend`/`resume` now require `expected_create_time`** — it was previously an optional argument defaulting to `None`, which meant a caller could forget to pass it and silently lose PID-reuse protection. There is no default now; every real caller always has a recorded `create_time`.
- **Live active-window/model-connection re-checks now run per pid, not once per batch.** A multi-select Optimize action re-queries `active_windows`/`local_models` immediately before acting on each pid, so a process that becomes active partway through a long batch is still caught — not just one that was already active when the batch started.
- **`processes.kill` distinguishes a clean terminate from a forced kill.** If a process doesn't exit within the wait timeout and the hard-kill fallback is used, the result is now `"force-killed after timeout"` instead of being reported identically to a clean `"terminated"`.
- **Resuming a permanently-stale suspended entry (gone, or pid reused) now drops it**, the same rule `load_suspended()` already applied on startup — it no longer sits in the list forever being retried. A retryable failure (e.g. `AccessDenied`) is left in place so the next "Resume Suspended" click tries again.
- **DB write failures can no longer abort an in-progress action loop or crash startup.** `database.py`'s write/read methods (`start_scan`, `add_process_snapshot`, `update_action`, `log_model_status`, `log_event`, `recent_events`, `stats`, `list_suspended`) now catch broadly, log a warning, and return a safe fallback instead of raising; `gui._optimize_done`'s `record_suspended`/`remove_suspended` calls and `analyzer.load_suspended`'s cleanup of a stale entry are separately wrapped so one failed DB write doesn't stop the rest of a results/startup loop from completing.

## [1.0.0] - 2026-09-30

### Added

- AI-assisted process optimizer: scans running processes, your active windows, and connected local AI models, then recommends `needed` or `suggest_close` for each process with a one-line reason.
- Three-engine reasoner chain, all opt-in beyond the offline floor: DeepSeek API (cloud, requires a key you provide) → local model via Ollama/LM Studio (if running) → built-in heuristics (always available, fully offline).
- Three independent safety gates so protected, active-window, and model-connected processes can never be closed or suspended: a recommendation-time override, an apply-time refusal, and an action-time re-check immediately before the kill/suspend syscall.
- Suspend (reversible) and Auto-close (destructive) modes, plus a **Resume Suspended** button to bring back anything the app suspended.
- Confirmation dialog before every suspend/close action, naming the specific processes involved and warning about unsaved work for closes.
- Chat assistant panel that answers questions against the live scan ("why is X suggested?", "what's safe to close?").
- Every scan, recommendation, action, and error logged to a local SQLite database (`data/optimizer.db`) for future tuning.
- File logging (`%LOCALAPPDATA%\Optimizer\logs\app.log`) so the windowed `.exe` (which has no console) still records errors.
- Portable single-file `OptimizerGUI.exe` distributed via GitHub Releases, built by GitHub Actions on tag with an attached `SHA256SUMS.txt`.

### Changed

- DeepSeek key dialog now states the privacy tradeoff directly next to the key field: leaving it blank keeps everything local; setting a key sends process names and window titles to DeepSeek's API.
- `database.add_process_snapshot` now returns the new row's id directly instead of `analyzer.py` reaching into the database's private connection helper to do the same insert a second time.

### Fixed

- A scan-failure handler captured the `except ... as e` exception variable inside a `lambda` scheduled via `root.after()` — Python deletes that variable when the `except` block exits, so a failed scan would raise a `NameError` in the error-reporting path itself instead of showing the real error. The message is now captured as a plain string before scheduling the callback.
