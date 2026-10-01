# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
