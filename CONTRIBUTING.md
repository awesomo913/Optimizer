# Contributing to Local Device Optimizer

Thanks for considering a contribution. This is a small, free, open-source tool that closes and suspends real Windows processes — issues and PRs of any size are welcome, but please read the "Code style" safety notes below before touching anything in `processes.py` or `analyzer.py`.

## Dev setup

Requires Python 3.11.

```bash
git clone https://github.com/awesomo913/Optimizer.git
cd Optimizer
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt
.venv\Scripts\python -m Optimizer
```

For running the full check suite and building the exe, also install the dev dependencies:

```bash
uv pip install --python .venv -r requirements-dev.txt
```

## Running tests and lint

```bash
pytest
ruff check .
```

Please run both before opening a PR. Tests never open the Tkinter GUI window, never touch a real process, and never make a real network call — `psutil`, `urllib`, and the Win32 window APIs are all mocked (see `tests/conftest.py` and the fakes in `tests/test_processes.py`).

## Architecture

- `active_windows.py` — foreground + visible windows → PIDs, via raw `ctypes` (no extra dependency).
- `processes.py` — enumerate processes; the safe `kill`/`suspend`/`resume` primitives, each re-checking the protected list immediately before acting.
- `local_models.py` — discover running Ollama / LM Studio servers and which programs are talking to them.
- `reasoners.py` — DeepSeek → local model → built-in heuristics, all returning one shape so `analyzer.py` is engine-agnostic.
- `analyzer.py` — orchestrates scan → survey → reason → safety overrides → persist, and applies the chosen action.
- `assistant.py` — the chat panel; same DeepSeek → local → offline fallback chain.
- `database.py` — SQLite log of every scan, recommendation, action, and error.
- `config.py` — paths, the hard protected-process safety list, and the file logger.
- `gui.py` — the Tkinter UI (all heavy work runs on worker threads, never the Tk main thread).

### Key implementation details worth knowing before you change things

- **Three independent safety layers, not one.** `analyzer._apply_safety_overrides` forces protected/active/model-bound rows to "needed" regardless of what a reasoner said; `analyzer.apply_action` refuses to call `processes.kill`/`suspend` for such rows at all; and `processes.kill`/`suspend` re-check the protected list again immediately before the actual syscall. A change to `config.PROTECTED_NAMES` or `processes._is_protected` must keep all three tests passing (`tests/test_safety.py`, `tests/test_analyzer.py`, `tests/test_processes.py`) — don't weaken this to make a feature easier.
- **No auto-apply path.** `gui.py` defaults to `mode="suggest"`, and `on_optimize()` always shows a confirmation dialog naming the actual processes before calling `analyzer.apply_action`. If you add a new way to trigger an action, it must go through the same confirmation + safety-gate path — see `tests/test_gui_safety.py`.
- **DeepSeek is opt-in.** `config.get_deepseek_key()` returns `None` unless the user explicitly set a key (UI or `DEEPSEEK_API_KEY`); `analyzer._choose_reasoner` and `assistant.chat` only attempt a DeepSeek call when a key is present. Don't add a code path that calls DeepSeek without checking this first.
- `conftest.py` at the repo root puts this directory's **parent** on `sys.path` so `from Optimizer import config` works in tests, while the package's own internal modules keep using relative imports (`from . import config`). This is also why `build.py` generates a tiny absolute-import launcher rather than building `__main__.py` directly — see the comment at the top of `build.py`.

## Code style

- Small, focused functions over large ones; prefer early returns over deep nesting.
- No silent `except:` blocks — catch specific exceptions, log or surface them, never swallow.
- Anything that touches the GUI (tkinter) must run on the main thread; background threads hand results back via `self.root.after(0, ...)`, not by touching widgets directly.
- Use `logging` (module-level `logger = logging.getLogger(__name__)`), not `print()`, in library code — the GUI build has no console, so `config.setup_logging()` routes everything to a file as well as stderr.

## Good first issues

Looking for a place to start? These are scoped well for a first PR:

- Windows Credential Manager (`keyring`) as an alternative to the plaintext `data/config.json` key storage — see `SECURITY.md`.
- A "why is this protected?" tooltip on protected rows in the process table.
- Per-process history (was this process suggested-close last time too?) surfaced in the assistant's answers.
- macOS/Linux equivalents of `active_windows.py` (currently Win32-only via `ctypes`).

Check open issues first in case someone's already working on one — comment to claim it.

## Pull request checklist

- [ ] `pytest` passes
- [ ] `ruff check .` passes with no new warnings
- [ ] No new silent exception handling
- [ ] No change weakens the three safety gates (protected / active-window / model-bound processes stay un-touchable) without an explicit, separate discussion
- [ ] GUI changes only touch tkinter from the main thread
- [ ] Updated `CHANGELOG.md` under `[Unreleased]` if the change is user-facing
- [ ] Description explains *why*, not just *what*
