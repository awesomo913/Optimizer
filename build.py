"""Build OptimizerGUI.exe — a standalone Windows executable for the GUI.

Builds with whatever interpreter runs this script. Point it at a dedicated
clean venv so the bundle doesn't drag unrelated packages from a shared
environment:

    .venv\\Scripts\\python.exe build.py

Output: dist\\OptimizerGUI.exe only (no copies elsewhere — CI picks it up
from dist\\ for releases; a local install is the user's own choice).

Note on naming: this repo's package is itself called "Optimizer" (this
directory *is* the package — see conftest.py), and its modules use relative
imports (`from . import config`). PyInstaller can't analyze a script that
does a bare relative import, so this build generates a tiny absolute-import
launcher in a temp directory and points PyInstaller's module search path
(--paths) at this directory's parent so `from Optimizer.gui import run`
resolves. That lookup only matters at *analysis* time; the frozen exe
doesn't depend on the build machine's folder layout at all.

Note on scope: only a GUI entry point exists in this repository
(gui.OptimizerApp / gui.run). No separate "daemon" source (background
service, scheduler, tray app, etc.) exists here, so this script builds the
GUI only.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)  # the directory containing the "Optimizer" package
APP_NAME = "OptimizerGUI"

_LAUNCHER_SOURCE = "from Optimizer.gui import run\n\nif __name__ == '__main__':\n    run()\n"


def build_exe() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        launcher_path = os.path.join(tmp_dir, "optimizer_gui_launcher.py")
        with open(launcher_path, "w", encoding="utf-8") as f:
            f.write(_LAUNCHER_SOURCE)

        args = [
            sys.executable, "-m", "PyInstaller",
            "--clean", "--noconfirm",
            "--onefile", "--windowed",
            f"--name={APP_NAME}",
            f"--paths={PARENT}",
            launcher_path,
        ]
        print("[build] Running PyInstaller (this may take 1-2 minutes)...")
        # Timeout so a hung PyInstaller fails loudly instead of blocking
        # forever; 20 minutes comfortably covers slow machines / cold caches.
        subprocess.check_call(args, cwd=HERE, timeout=1200)
        print("[build] PyInstaller complete.")


def main() -> None:
    build_exe()

    exe_path = os.path.join(HERE, "dist", f"{APP_NAME}.exe")
    if not os.path.isfile(exe_path):
        raise SystemExit(f"[build] FAILED: expected {exe_path} but it doesn't exist")
    size_mb = round(os.path.getsize(exe_path) / (1024 * 1024), 1)
    print(f"[build] OK -> {exe_path}  ({size_mb} MB)")


if __name__ == "__main__":
    main()
