# Security Policy

## Supported versions

Only the latest released version of Local Device Optimizer is supported with security fixes.

## Reporting a vulnerability

Please report security issues using **[GitHub's private vulnerability reporting](https://github.com/awesomo913/Optimizer/security/advisories/new)** (Security tab → "Report a vulnerability") rather than a public issue. This lets the report be triaged before details are public.

If private reporting isn't available for you, open a regular GitHub issue with as much detail as you're comfortable sharing publicly, and note that it's a security concern in the title.

Please include:

- A description of the issue and its potential impact
- Steps to reproduce, if possible
- The Optimizer version and Windows version you're running

## Scope notes — read this before relying on the app

This app closes and suspends real processes on your machine. The safety model is deliberately layered — see the README's "Staying safe" section for the three independent gates — but you should still understand the real tradeoffs:

- **Nothing is closed or suspended automatically.** Every action requires an explicit checkbox + "OPTIMIZE NOW" click + a confirmation dialog naming the processes involved. Default mode is "Suggest only" — it never touches anything.
- **Protected, active-window, and model-connected processes cannot be closed or suspended**, regardless of what you check or what mode you pick — the gate is enforced three times (once at recommendation time, once when you click Optimize, and again immediately before the kill/suspend syscall), and is covered by automated tests (`tests/test_safety.py`, `tests/test_analyzer.py`, `tests/test_processes.py`).
- **Suspending is reversible; closing is not.** Use "Resume Suspended" to bring back anything you suspended. A closed process is gone — any unsaved work in it is gone too. The UI always offers Suspend before Auto-close for this reason.
- **The DeepSeek cloud reasoner is strictly opt-in and off by default.** With no API key configured, Optimizer never makes a network call to analyze your system — it uses a local model (Ollama/LM Studio) if one is running, or offline built-in heuristics. If you add a DeepSeek key, your running process names **and your window titles** (which can contain document names, page titles, or other content) are sent to DeepSeek's API with every scan and chat message. This is stated again in the "Set DeepSeek Key…" dialog itself.
- **The DeepSeek key is stored in plaintext** in `data/config.json` (gitignored — it never gets committed or synced anywhere by this app). Anything with read access to your Windows user profile can read it. Prefer the `DEEPSEEK_API_KEY` environment variable if you want to avoid writing it to disk at all. There is no encryption-at-rest for this file.
- **No telemetry.** Optimizer does not phone home, does not report usage anywhere, and the only outbound network calls it ever makes are: (a) probing `localhost` for Ollama/LM Studio, and (b) the DeepSeek API, only if you've set a key.
- **The release `.exe` is unsigned.** Reports about SmartScreen or antivirus false positives are welcome context but are not themselves security vulnerabilities — see the README FAQ for the current recommended workaround (verify checksums, or build from source).
