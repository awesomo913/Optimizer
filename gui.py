"""Lightweight Tkinter GUI. Native widgets only -> minimal system footprint.

Left: process table + controls. Right: a conversational assistant you can ask
about anything in the scan ("why is X suggested?", "what's safe to close?").
Heavy work (scanning, model calls) runs on worker threads so the UI never freezes.
"""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
import traceback
from tkinter import messagebox, ttk

from . import analyzer, assistant, config
from .database import Database

logger = logging.getLogger(__name__)


def _log_tk_callback_error(exc, val, tb) -> None:
    """Tkinter swallows exceptions raised inside widget callbacks by default
    (it just prints to stderr, which doesn't exist for a --windowed exe).
    Route them into the same log file as everything else instead."""
    logger.error("Unhandled error in Tk callback:\n%s",
                 "".join(traceback.format_exception(exc, val, tb)))


class OptimizerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.report_callback_exception = _log_tk_callback_error
        self.db = Database()
        self.result: analyzer.AnalysisResult | None = None
        self.mode = tk.StringVar(value="suggest")
        self._busy = False
        self._chat_busy = False
        self._chat_history: list[dict] = []     # {'role','content'}
        self._chat_q: queue.Queue = queue.Queue()
        # pid -> {"name": str, "create_time": float}, for "Resume Suspended".
        # Persisted via self.db (suspended_processes table) so it survives a
        # restart — see _load_persisted_suspended().
        self._suspended: dict[int, dict] = {}
        self._resume_q: queue.Queue = queue.Queue()

        root.title("Local Device Optimizer")
        root.geometry("1280x740")
        root.minsize(1040, 600)

        self._build_top_bar()
        self._build_main_split()
        self._build_status_bar()
        self._refresh_key_state()
        self._load_persisted_suspended()

    # ---- layout -------------------------------------------------------------
    def _build_top_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(fill="x")

        ttk.Label(bar, text="Mode:").pack(side="left")
        for val, label in (("suggest", "Suggest only"),
                           ("suspend", "Suspend (freeze)"),
                           ("kill", "Auto-close")):
            ttk.Radiobutton(bar, text=label, value=val,
                            variable=self.mode).pack(side="left", padx=4)

        self.scan_btn = ttk.Button(bar, text="Scan", command=self.on_scan)
        self.scan_btn.pack(side="left", padx=(16, 4))
        self.optimize_btn = ttk.Button(bar, text="  OPTIMIZE NOW  ",
                                       command=self.on_optimize)
        self.optimize_btn.pack(side="left", padx=4)
        ttk.Button(bar, text="Ask AI about checked",
                   command=self.ask_about_checked).pack(side="left", padx=4)
        self.resume_btn = ttk.Button(bar, text="Resume Suspended (0)",
                                     command=self.on_resume_all, state="disabled")
        self.resume_btn.pack(side="left", padx=4)

        ttk.Button(bar, text="Set DeepSeek Key…",
                   command=self.on_set_key).pack(side="right", padx=4)
        self.key_lbl = ttk.Label(bar, text="")
        self.key_lbl.pack(side="right", padx=8)

    def _build_main_split(self) -> None:
        pane = ttk.PanedWindow(self.root, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=4)
        self._build_table(pane)
        self._build_chat(pane)

    def _build_table(self, parent) -> None:
        frame = ttk.Frame(parent)
        parent.add(frame, weight=3)

        cols = ("sel", "pid", "name", "cpu", "mem", "flags", "rec", "reason")
        headers = {"sel": "✓", "pid": "PID", "name": "Process", "cpu": "CPU%",
                   "mem": "Mem MB", "flags": "Flags", "rec": "Verdict",
                   "reason": "Reason"}
        widths = {"sel": 30, "pid": 56, "name": 170, "cpu": 50, "mem": 64,
                  "flags": 100, "rec": 100, "reason": 300}
        self.tree = ttk.Treeview(frame, columns=cols, show="headings",
                                 selectmode="none")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c],
                             anchor="center" if c in ("sel", "pid", "cpu", "mem")
                             else "w")
        self.tree.tag_configure("close", background="#ffecec")
        self.tree.tag_configure("needed", background="#f3fff3")
        self.tree.tag_configure("protected", foreground="#888")

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._toggle_row)
        self.tree.bind("<Double-1>", self._row_double_click)
        self._checked: set[str] = set()

    def _build_chat(self, parent) -> None:
        frame = ttk.Frame(parent)
        parent.add(frame, weight=2)
        ttk.Label(frame, text="Assistant — ask about anything running",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 2))

        wrap = ttk.Frame(frame)
        wrap.pack(fill="both", expand=True)
        self.chat = tk.Text(wrap, wrap="word", state="disabled", height=10,
                            font=("Segoe UI", 10), background="#101418",
                            foreground="#e6e6e6", relief="flat", padx=8, pady=8)
        csb = ttk.Scrollbar(wrap, orient="vertical", command=self.chat.yview)
        self.chat.configure(yscrollcommand=csb.set)
        self.chat.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self.chat.tag_configure("you", foreground="#6fb3ff",
                                font=("Segoe UI", 10, "bold"))
        self.chat.tag_configure("ai", foreground="#e6e6e6")
        self.chat.tag_configure("system", foreground="#c9b76b")
        self.chat.tag_configure("error", foreground="#ff8a8a")
        self.chat.tag_configure("label", foreground="#7a8a99",
                                font=("Segoe UI", 8, "bold"))

        inrow = ttk.Frame(frame)
        inrow.pack(fill="x", pady=(4, 0))
        self.chat_entry = tk.Text(inrow, height=3, wrap="word",
                                  font=("Segoe UI", 10))
        self.chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.chat_entry.bind("<Control-Return>", lambda _e: (self.send_chat(), "break"))
        self.send_btn = ttk.Button(inrow, text="Send\n(Ctrl+Enter)",
                                   command=self.send_chat, width=12)
        self.send_btn.pack(side="left", fill="y")

        self._chat_add("system",
                       "Run a Scan, then ask me things like “why is Discord "
                       "suggested?”, “what's safe to close while I'm coding?”, "
                       "or double-click any red row to ask about it.")

    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(fill="x")
        self.status = tk.StringVar(value="Ready. Click Scan to analyze your system.")
        ttk.Label(bar, textvariable=self.status).pack(side="left")
        self.summary = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.summary).pack(side="right")

    # ---- chat rendering -----------------------------------------------------
    def _chat_add(self, role: str, text: str, engine: str | None = None) -> None:
        labels = {"you": "YOU", "ai": "ASSISTANT", "system": "SYSTEM",
                  "error": "ERROR"}
        self.chat.configure(state="normal")
        lbl = labels.get(role, role.upper())
        if engine:
            lbl += f"  ({engine})"
        self.chat.insert("end", f"{lbl}\n", "label")
        self.chat.insert("end", text.strip() + "\n\n", role)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    # ---- helpers ------------------------------------------------------------
    def _refresh_key_state(self) -> None:
        if config.get_deepseek_key():
            self.key_lbl.config(text="DeepSeek: ✓ key set", foreground="green")
        else:
            self.key_lbl.config(text="DeepSeek: not set (using local/heuristic)",
                                foreground="#a60")

    def _toggle_row(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        item = self.tree.identify_row(event.y)
        if not item or "selectable" not in self.tree.item(item, "tags"):
            return
        if item in self._checked:
            self._checked.discard(item)
            mark = "☐"
        else:
            self._checked.add(item)
            mark = "☑"
        vals = list(self.tree.item(item, "values"))
        vals[0] = mark
        self.tree.item(item, values=vals)

    def _row_double_click(self, event) -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        vals = self.tree.item(item, "values")
        if not vals:
            return
        name, pid, verdict = vals[2], vals[1], vals[6]
        q = (f"What is the process \"{name}\" (pid {pid})? The optimizer's verdict "
             f"is \"{verdict}\". Is it safe to close while I'm doing my current "
             f"work, and what would I lose if I do?")
        self._submit_chat(q)

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.scan_btn.config(state=state)
        self.optimize_btn.config(state=state)
        if msg:
            self.status.set(msg)

    # ---- key dialog ---------------------------------------------------------
    def on_set_key(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("DeepSeek API Key")
        win.geometry("460x220")
        win.transient(self.root)
        ttk.Label(win, text="Enter your DeepSeek API key (stored locally):",
                  padding=10).pack(anchor="w")
        entry = ttk.Entry(win, width=56, show="•")
        entry.pack(padx=10, fill="x")
        entry.insert(0, config.get_deepseek_key() or "")

        ttk.Label(
            win, wraplength=430, justify="left", foreground="#a60",
            text=("Privacy: DeepSeek is strictly opt-in. Leave this blank and "
                  "the optimizer only ever uses a local model (Ollama/LM Studio) "
                  "or built-in offline heuristics — nothing leaves your machine. "
                  "If you set a key here, the names of your running processes "
                  "and your window titles (which can contain document or page "
                  "names) are sent to DeepSeek's cloud API with every scan and "
                  "chat message. The key itself is stored in plaintext in "
                  "data/config.json (gitignored, never uploaded)."),
        ).pack(padx=10, pady=(8, 0), anchor="w")

        def save():
            config.set_deepseek_key(entry.get())
            self._refresh_key_state()
            win.destroy()

        def clear():
            config.set_deepseek_key("")
            self._refresh_key_state()
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(pady=10)
        ttk.Button(btns, text="Save", command=save).pack(side="left", padx=4)
        ttk.Button(btns, text="Clear / go offline-only",
                   command=clear).pack(side="left", padx=4)
        entry.focus_set()

    # ---- scan ---------------------------------------------------------------
    def on_scan(self) -> None:
        if self._busy:
            return
        self._set_busy(True, "Scanning processes, windows, and local models…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        try:
            result = analyzer.analyze(self.mode.get(), self.db)
        except Exception as e:
            # `except ... as e` implicitly `del`s e when this block exits, so
            # it must not be captured by a lambda that outlives the block
            # (Tk's `after` runs it later, on the main thread) — capture the
            # message as a plain string instead.
            msg = str(e)
            logger.exception("Analysis crashed")
            self.db.log_event("error", "scan", f"Analysis crashed: {msg}")
            self.root.after(0, lambda: self._scan_failed(msg))
            return
        self.root.after(0, lambda: self._scan_done(result))

    def _scan_failed(self, err: str) -> None:
        self._set_busy(False, f"Scan failed: {err}")
        messagebox.showerror("Scan failed", err)

    def _scan_done(self, result: analyzer.AnalysisResult) -> None:
        prev_closable = len(self.result.closable) if self.result else None
        self.result = result
        self._checked.clear()
        self.tree.delete(*self.tree.get_children())

        rows = sorted(result.rows, key=lambda r: (
            r["recommendation"] != "suggest_close", -r["mem_mb"]))
        reclaim = 0.0
        for r in rows:
            close = r["recommendation"] == "suggest_close"
            flags = []
            if r["protected"]:
                flags.append("PROTECTED")
            if r["is_active"]:
                flags.append("ACTIVE")
            if r["uses_local_model"]:
                flags.append("MODEL")
            mark = "☐" if close else "—"
            tags = ["close", "selectable"] if close else (
                ["protected"] if r["protected"] else ["needed"])
            iid = self.tree.insert(
                "", "end", tags=tags,
                values=(mark, r["pid"], r["name"], f"{r['cpu']:.0f}",
                        f"{r['mem_mb']:.0f}", " ".join(flags),
                        r["recommendation"], r["reason"]))
            if close:
                reclaim += r["mem_mb"]
                if r.get("confidence", 0) >= 0.7:
                    self._checked.add(iid)
                    vals = list(self.tree.item(iid, "values"))
                    vals[0] = "☑"
                    self.tree.item(iid, values=vals)

        n_close = len(result.closable)
        oll = result.model_summary.get("ollama", {})
        lms = result.model_summary.get("lmstudio", {})
        self.summary.set(
            f"engine: {result.reasoner}  |  "
            f"ollama: {len(oll.get('models', []))} models"
            f"{'' if oll.get('available') else ' (offline)'}  |  "
            f"lmstudio: {'on' if lms.get('available') else 'off'}")

        delta = ""
        if prev_closable is not None:
            diff = prev_closable - n_close
            if diff > 0:
                delta = f"  (rescan: {diff} fewer closable than before)"
        self.status.set(
            f"Active: {result.foreground or '—'}  •  {n_close} closable, "
            f"~{reclaim:.0f} MB reclaimable.{delta} "
            + ("Review & OPTIMIZE NOW, or ask the assistant."
               if n_close else "System looks lean."))
        self._set_busy(False)

    # ---- optimize -----------------------------------------------------------
    def on_optimize(self) -> None:
        if self._busy:
            return
        if not self.result:
            messagebox.showinfo("Optimize", "Run a Scan first.")
            return
        pids: list[int] = []
        for iid in self._checked:
            vals = self.tree.item(iid, "values")
            if vals and vals[0] == "☑":
                pids.append(int(vals[1]))
        if not pids:
            messagebox.showinfo("Optimize", "Nothing selected to optimize.")
            return

        mode = self.mode.get()
        if mode == "suggest":
            names = [r["name"] for r in self.result.rows if r["pid"] in pids]
            self._chat_add("system",
                           "Suggest-only mode — nothing was changed. Candidates: "
                           + ", ".join(sorted(set(names))) +
                           ". Switch to Suspend or Auto-close to act, or ask me "
                           "about any of these.")
            messagebox.showinfo(
                "Suggest-only mode",
                f"{len(pids)} processes are flagged (red rows). Nothing was "
                "changed. Switch to 'Suspend' or 'Auto-close' and click OPTIMIZE "
                "NOW to act.")
            return

        names = sorted({r["name"] for r in self.result.rows if r["pid"] in pids})
        shown = ", ".join(names[:12]) + (f", +{len(names) - 12} more" if len(names) > 12 else "")
        if mode == "suspend":
            verb = "suspend (freeze)"
            risk_note = ("Suspended processes stop using CPU immediately but keep "
                        "their memory; use \"Resume Suspended\" any time to bring "
                        "them back exactly where they left off.")
        else:
            verb = "close"
            risk_note = ("This closes the process outright. Any unsaved work in "
                        "that program may be lost. Prefer Suspend if you're not sure.")
        if not messagebox.askyesno(
                "Confirm optimize",
                f"About to {verb} {len(pids)} process(es):\n\n{shown}\n\n{risk_note}\n\n"
                "Protected, active (has a visible window), and model-bound "
                "processes are automatically skipped regardless of what's checked. "
                "Continue?"):
            return
        self._set_busy(True, f"Applying ({mode})…")
        threading.Thread(target=self._optimize_worker, args=(pids, mode),
                         daemon=True).start()

    def _optimize_worker(self, pids: list[int], mode: str) -> None:
        try:
            outcomes = analyzer.apply_action(self.result, pids, mode, self.db)
        except Exception as e:
            msg = str(e)
            logger.exception("apply_action crashed")
            self.db.log_event("error", "action", f"apply_action crashed: {msg}")
            self.root.after(0, lambda: self._scan_failed(f"Optimize failed: {msg}"))
            return
        self.root.after(0, lambda: self._optimize_done(outcomes, mode))

    def _optimize_done(self, outcomes, mode: str) -> None:
        by_pid = {r["pid"]: r for r in self.result.rows}
        done, failed, reclaimed = [], [], 0.0
        for pid, ok, msg in outcomes:
            row = by_pid.get(pid, {})
            name = row.get("name", str(pid))
            if ok:
                done.append(name)
                reclaimed += row.get("mem_mb", 0)
                if mode == "suspend":
                    create_time = row.get("create_time")
                    self._suspended[pid] = {"name": name, "create_time": create_time}
                    self.db.record_suspended(pid, name, create_time)
                elif mode == "kill":
                    self._suspended.pop(pid, None)
                    self.db.remove_suspended(pid)
            else:
                failed.append(f"{name} ({msg})")
        self._refresh_resume_button()

        verb = "Suspended" if mode == "suspend" else "Closed"
        lines = [f"{verb} {len(done)} processes, freeing ~{reclaimed:.0f} MB."]
        if done:
            lines.append("  • " + ", ".join(sorted(set(done))))
        if failed:
            lines.append(f"Skipped/failed {len(failed)} (safety guard or access):")
            lines.append("  • " + "; ".join(failed))
        lines.append("Re-scanning to show the new state…")
        results_text = "\n".join(lines)

        self._chat_add("system", results_text)
        messagebox.showinfo("Optimization results", results_text)
        self.status.set(f"{verb} {len(done)}, freed ~{reclaimed:.0f} MB, "
                        f"{len(failed)} skipped. Logged to DB. Re-scanning…")
        self.root.after(200, self.on_scan)  # rescan -> table shows new state

    # ---- resume ---------------------------------------------------------------
    def _load_persisted_suspended(self) -> None:
        """Reload the suspended-processes list from the DB on startup so
        "Resume Suspended" still works after a restart. See
        analyzer.load_suspended() for the identity-verification logic."""
        self._suspended = analyzer.load_suspended(self.db)
        self._refresh_resume_button()

    def _refresh_resume_button(self) -> None:
        n = len(self._suspended)
        self.resume_btn.config(text=f"Resume Suspended ({n})",
                               state="normal" if n else "disabled")

    def on_resume_all(self) -> None:
        if self._busy or not self._suspended:
            return
        entries = [{"pid": pid, "name": info["name"], "create_time": info["create_time"]}
                  for pid, info in self._suspended.items()]
        self._set_busy(True, f"Resuming {len(entries)} suspended process(es)…")
        threading.Thread(target=self._resume_worker, args=(entries,),
                         daemon=True).start()
        self.root.after(80, self._poll_resume)

    def _resume_worker(self, entries: list[dict]) -> None:
        try:
            outcomes = analyzer.resume_pids(entries, self.db)
        except Exception as e:
            msg = str(e)
            logger.exception("resume_pids crashed")
            self.db.log_event("error", "action", f"resume_pids crashed: {msg}")
            self.root.after(0, lambda: self._scan_failed(f"Resume failed: {msg}"))
            # Unblock _poll_resume (it's waiting on this queue) with an empty
            # result rather than leaving it to poll forever.
            outcomes = []
        self._resume_q.put(outcomes)

    def _poll_resume(self) -> None:
        try:
            outcomes = self._resume_q.get_nowait()
        except queue.Empty:
            self.root.after(80, self._poll_resume)
            return
        resumed, failed = [], []
        for pid, ok, msg in outcomes:
            info = self._suspended.get(pid, {})
            name = info.get("name", str(pid))
            if ok:
                self._suspended.pop(pid, None)
                self.db.remove_suspended(pid)
                resumed.append(name)
            else:
                failed.append(f"{name} ({msg})")
        self._refresh_resume_button()
        lines = [f"Resumed {len(resumed)} process(es)."]
        if resumed:
            lines.append("  • " + ", ".join(sorted(set(resumed))))
        if failed:
            lines.append(f"Failed to resume {len(failed)}:")
            lines.append("  • " + "; ".join(failed))
        text = "\n".join(lines)
        self._chat_add("system", text)
        self._set_busy(False, text.splitlines()[0])

    # ---- assistant chat -----------------------------------------------------
    def ask_about_checked(self) -> None:
        if not self.result:
            messagebox.showinfo("Assistant", "Run a Scan first.")
            return
        names = []
        for iid in self._checked:
            vals = self.tree.item(iid, "values")
            if vals and vals[0] == "☑":
                names.append(f"{vals[2]} (pid {vals[1]})")
        if not names:
            messagebox.showinfo("Assistant",
                                "No checked rows. Check some red rows first.")
            return
        q = ("For each of these checked processes, tell me what it is and whether "
             "it's safe to close right now given my active work: "
             + "; ".join(names))
        self._submit_chat(q)

    def send_chat(self) -> None:
        text = self.chat_entry.get("1.0", "end").strip()
        if not text:
            return
        self.chat_entry.delete("1.0", "end")
        self._submit_chat(text)

    def _submit_chat(self, text: str) -> None:
        if self._chat_busy:
            self._chat_add("system", "Hold on — still answering the last question.")
            return
        self._chat_add("you", text)
        self._chat_history.append({"role": "user", "content": text})
        self._chat_busy = True
        self.send_btn.config(state="disabled")
        self._chat_add("system", "thinking…")
        context = assistant.build_context(self.result)
        model_summary = self.result.model_summary if self.result else {}
        threading.Thread(target=self._chat_worker,
                         args=(list(self._chat_history), context, model_summary),
                         daemon=True).start()
        self.root.after(80, self._poll_chat)

    def _chat_worker(self, history, context, model_summary) -> None:
        try:
            reply, engine = assistant.chat(history, context, model_summary, self.db)
        except Exception as e:
            reply, engine = f"Assistant error: {e}", "error"
        self._chat_q.put((reply, engine))

    def _poll_chat(self) -> None:
        try:
            reply, engine = self._chat_q.get_nowait()
        except queue.Empty:
            self.root.after(80, self._poll_chat)
            return
        # remove the trailing "thinking…" placeholder
        self.chat.configure(state="normal")
        idx = self.chat.search("thinking…", "end", backwards=True)
        if idx:
            self.chat.delete(f"{idx} linestart -1 lines", "end")
            self.chat.insert("end", "\n")
        self.chat.configure(state="disabled")

        role = "error" if engine == "error" else "ai"
        self._chat_add(role, reply, engine=None if role == "error" else engine)
        if role == "ai":
            self._chat_history.append({"role": "assistant", "content": reply})
        self._chat_busy = False
        self.send_btn.config(state="normal")


def run() -> None:
    config.setup_logging()
    root = tk.Tk()
    OptimizerApp(root)
    root.mainloop()
