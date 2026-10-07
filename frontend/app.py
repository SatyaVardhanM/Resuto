# app.py
"""
Resuto — desktop GUI built with CustomTkinter.

CustomTkinter gives modern rounded widgets, proper dark theme,
and clean typography with zero extra dependencies beyond pip.

Install:  pip install customtkinter
Run:      python app.py  (or double-click run.bat)

Architecture:
  - No separate "details" panels anywhere — accordion expands inline
  - History list is Canvas-drawn (zero widgets per row, instant render)
  - Stats use CTk widgets updated in-place (no full rebuild on refresh)
  - All DB access uses WAL mode + mtime guard
  - subprocess stdin=PIPE so Phase 3 prompts route through the GUI
"""

from __future__ import annotations
import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os, re, sys, json, queue, hashlib, sqlite3, subprocess
import threading, traceback
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

# ── CustomTkinter global config ───────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

from frontend.constants import (
    APP_TITLE, BOT_SCRIPT,
    BG, BG_CARD, BG_FIELD, BG_HOVER,
    ACCENT, ACCENT_HV, DANGER, SUCCESS, WARNING, STRETCH, MUTED,
    FG, FG_SOFT, FG_DIM,
    F, _init_fonts, _FONTS, _BASE_SIZE, _load_font_pref,
    _settings_file, _load_api_key, _save_api_key, _clear_api_key,
    _save_font_pref,
)

# Fonts are initialised inside App.__init__() after CTk root exists.

# Regex patterns (_PHASE_MAP, _ERROR_RE, …) live in frontend/constants.py
# so every view module can import them.
from frontend.constants import (  # noqa: E402
    _ERROR_RE, _PHASE_MAP, _DSQ_RE, _NF_RE, _APPLY_RE, _REAPPLY_RE,
    _IDLE_RE, _LAST_JOB_RE, _ACTIVITY_PATTERNS,
)

# ── DB helpers ─────────────────────────────────────────────────────
def _db_path() -> str:
    if not hasattr(_db_path, "_v"):
        from core.settings import get_output_dir
        _db_path._v = os.path.join(get_output_dir(), "applications.db")
    return _db_path._v

def _ensure_schema() -> None:
    """Run the tracker's schema upgrade once (adds new columns such as
    actioned_at) before the GUI queries the DB with its own connection."""
    if getattr(_ensure_schema, "_done", False):
        return
    try:
        import db.tracker as _t
        _t._connect().close()
        _ensure_schema._done = True
    except Exception:
        pass


def _read_stats(since: str = None, recent_since: str = None) -> dict:
    db = _db_path()
    if not os.path.exists(db):
        return {}
    _ensure_schema()
    try:
        # COALESCE: a job applied/skipped this run counts even if it was
        # scanned in an earlier run (actioned_at is set by mark_job_outcome)
        _t = "COALESCE(actioned_at, logged_at)"
        where = f"WHERE {_t}>='{since}'" if since else ""
        and_s = f"AND {_t}>='{since}'" if since else ""
        with sqlite3.connect(db, timeout=5) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA cache_size=-4096")
            c.row_factory = sqlite3.Row
            counts = {r["status"]: r["n"] for r in c.execute(
                f"SELECT status,COUNT(*)AS n FROM applications {where} GROUP BY status")}
            # scanning = currently being analyzed, show in recent but not counters
            counts.pop("scanning", None)
            avg_row = c.execute(
                f"SELECT AVG(match_score)AS a FROM applications "
                f"WHERE status IN('applied','skipped','failed','matched','resume_ready') "
                f"AND match_score > 0 {and_s}"   # only jobs Claude actually scored
            ).fetchone()
            avg = round(avg_row["a"] or 0) if avg_row and avg_row["a"] else 0
            # Recent: only jobs worth seeing — exclude hard pre-filtered/ineligible
            # Pre-filtered (title mismatch) and hard ineligible (citizenship)
            # pollute the tab with noise the user can't act on
            jobs = [dict(r) for r in c.execute(
                "SELECT job_title,company,status,match_score,"
                "skill_overlap,ai_reason,"
                "COALESCE(actioned_at, logged_at) AS logged_at,stretch,notes "
                "FROM applications "
                "WHERE NOT ("
                "  status='skipped' AND ("
                "    notes LIKE '%title not related%' OR "
                "    notes LIKE '%Pre-filter%' OR "
                "    notes LIKE '%citizenship%' OR "
                "    notes LIKE '%security clearance%' OR "
                "    notes LIKE '%Pipeline interrupted%'"
                "  )"
                ") "
                "ORDER BY COALESCE(actioned_at, logged_at) DESC LIMIT 12")]
        return {"counts": counts, "avg": avg,
                "jobs": jobs,
                "queued": counts.get("matched", 0) + counts.get("resume_ready", 0)}
    except Exception:
        return {}

def _read_history(filt: str) -> list:
    db = _db_path()
    if not os.path.exists(db):
        return []
    _ensure_schema()
    # Exclude hard-ineligible pre-filtered jobs from all history views
    # These are title-mismatch skips that have no value to the user
    _noise_filter = (
        " AND NOT (status='skipped' AND ("
        "  notes LIKE '%Pre-filter%' OR "
        "  notes LIKE '%title not related%' OR "
        "  notes LIKE '%Pipeline interrupted%'"
        "))")

    if filt == "all":
        where = "WHERE 1=1" + _noise_filter
    elif filt == "stretch":
        where = "WHERE status IN ('matched','resume_ready') AND stretch=1"
    elif filt == "skipped":
        # Show all skips except pure noise (no title, no company)
        where = (
            "WHERE status='skipped' AND ("
            "  match_score > 0 OR "      # AI-scored skip
            "  notes NOT LIKE '%Pre-filter%' AND "
            "  notes NOT LIKE '%title not related%'"
            ")"
        )
    elif filt == "matched":
        # "Queued" tab: waiting for a resume OR resume ready to apply
        where = "WHERE status IN ('matched','resume_ready')" + _noise_filter
    else:
        where = f"WHERE status='{filt}'" + _noise_filter
    try:
        with sqlite3.connect(db, timeout=5) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(
                f"SELECT id,job_title,company,status,match_score,"
                f"skill_overlap,ai_reason,applied_at,logged_at,notes "
                f"FROM applications {where} "
                f"ORDER BY logged_at DESC LIMIT 200")]
    except Exception:
        return []

# ── Bot subprocess ─────────────────────────────────────────────────
from frontend.bot_runner          import BotRunner
from frontend.views.run_view       import RunMixin
from frontend.views.history_view   import HistoryMixin
from frontend.views.stats_view     import StatsMixin
from frontend.views.settings_view  import SettingsMixin
from frontend.views.dialogs        import IntakeWindow, ProfileViewWindow, ReviewWindow
from frontend.views.auth_view      import run_access_gate

# ── Main application ───────────────────────────────────────────────
class App(ctk.CTk, RunMixin, HistoryMixin, StatsMixin, SettingsMixin):

    # ── Canvas row metrics ────────────────────────────────────────

    def __init__(self):
        super().__init__()
        # Fonts MUST be created after super().__init__() — CTkFont needs a live Tk root
        _init_fonts(_load_font_pref())
        # API key StringVar — loaded from settings (may be empty if user chose not to save)
        self._api_var       = ctk.StringVar(value=_load_api_key())
        self._api_save_pref = ctk.BooleanVar(value=bool(_load_api_key()))

        # ── Access gate (email + API key sign-in) ───────────────────
        # Keep the (still empty) main window invisible while the sign-in
        # window is up. NOT withdraw(): CustomTkinter remembers a withdraw()
        # made before the window is first shown and then never shows it again
        # (the app looked "closed" after login).
        self.attributes("-alpha", 0.0)
        self._access_granted = False
        self._gate_done      = tk.BooleanVar(value=False)

        def _on_access_granted(api_key: str = ""):
            self._access_granted = True
            # Use the key the user just signed in with — don't make them
            # type it again in Settings
            if api_key:
                self._api_var.set(api_key)
            self._gate_done.set(True)

        run_access_gate(self, _on_access_granted)
        self.wait_variable(self._gate_done)

        if not self._access_granted:
            self.destroy()
            return
        self.attributes("-alpha", 1.0)

        self.title(APP_TITLE)
        self.geometry("920x640")
        self.minsize(820, 560)

        self._q             = queue.Queue()
        self._runner        = None
        self._err_count     = 0
        self._live          = False
        from datetime import datetime as _dt
        self._session_start = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        self._stats_last_hash   = None
        self._stats_after_id    = None
        self._stat_vars         = {}

        # RunMixin state
        self._role_vars         = []
        self._search_mode       = None
        self._step_frames       = []
        self._user_stopped      = False
        self._restarting        = False
        self._tb_buffer         = []
        self._tb_active         = False
        self._bot_start         = None
        self._action_panel      = None
        self._act_strip_visible = False

        # HistoryMixin state
        self._h_page            = 0
        self._h_rows            = []
        self._h_selected        = set()
        self._h_row_widgets     = {}

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()
        self._sched_stats()

        # Record token cost of Claude calls made from the GUI too
        # (role suggestions, intake). fail_fast=False: errors pass through.
        try:
            from core.costs import install_usage_tracking
            install_usage_tracking(fail_fast=False)
        except Exception:
            pass

        # Auto-update: check GitHub releases in the background. The result is
        # handed to the Tk thread through the queue (see RunMixin._poll).
        try:
            from core.updater import check_in_background
            check_in_background(lambda info: self._q.put(("update_found", info)))
        except Exception:
            pass

    # ── Build ─────────────────────────────────────────────────────
    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._sidebar()
        self._content()

    def _sidebar(self):
        sb = ctk.CTkFrame(self, width=64, corner_radius=0, fg_color=BG_CARD)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)
        sb.grid_rowconfigure(1, weight=1)

        # Logo
        top = ctk.CTkFrame(sb, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(top, text="⚡", font=F("icon_lg"),
                     text_color=ACCENT).pack(pady=(20, 2))
        ctk.CTkLabel(top, text="Resuto", font=F("tiny"),
                     text_color=FG_DIM).pack()
        ctk.CTkLabel(top, text="by Zetene", font=F("tiny"),
                     text_color=MUTED).pack()
        ctk.CTkFrame(top, height=1, fg_color=BG_HOVER
                     ).pack(fill="x", padx=8, pady=14)

        # Main nav buttons
        self._nav_btns = []
        for icon, tip, idx in [("▶","Run",0), ("⚠","Errors",1),
                                 ("◉","Stats",2), ("▤","History",3)]:
            f = ctk.CTkFrame(top, fg_color="transparent", cursor="hand2")
            f.pack(fill="x", pady=2)
            icon_lbl = ctk.CTkLabel(f, text=icon, font=F("icon_lg"),
                                     text_color=FG_DIM)
            icon_lbl.pack()
            tip_lbl = ctk.CTkLabel(f, text=tip, font=F("tiny"),
                                    text_color=FG_DIM)
            tip_lbl.pack()
            # Bind ALL three widgets — frame, icon, AND label text
            for w in (f, icon_lbl, tip_lbl):
                w.bind("<Button-1>", lambda e, i=idx: self._nav(i))
            self._bind_nav_hover(f, (f, icon_lbl, tip_lbl))
            self._nav_btns.append((f, icon_lbl))

        # Spacer — pushes settings to bottom
        ctk.CTkFrame(sb, fg_color="transparent").grid(row=1, column=0,
                                                        sticky="nsew")

        # Settings pinned at bottom
        bot = ctk.CTkFrame(sb, fg_color="transparent")
        bot.grid(row=2, column=0, sticky="ew")
        ctk.CTkFrame(bot, height=1, fg_color=BG_HOVER
                     ).pack(fill="x", padx=8, pady=(0, 8))
        sf = ctk.CTkFrame(bot, fg_color="transparent", cursor="hand2")
        sf.pack(fill="x", pady=(0, 14))
        sico = ctk.CTkLabel(sf, text="⚙", font=F("icon_lg"),
                             text_color=FG_DIM)
        sico.pack()
        stip = ctk.CTkLabel(sf, text="Settings", font=F("tiny"),
                             text_color=FG_DIM)
        stip.pack()
        for w in (sf, sico, stip):
            w.bind("<Button-1>", lambda e: self._nav(4))
        self._bind_nav_hover(sf, (sf, sico, stip))
        self._nav_btns.append((sf, sico))   # index 4

        # Keyboard access: Ctrl+1..5 switch tabs (Run, Errors, Stats,
        # History, Settings) — the sidebar items are labels, not buttons
        for i in range(5):
            self.bind_all("<Control-Key-%d>" % (i + 1),
                          lambda e, i=i: self._nav(i))

    def _bind_nav_hover(self, frame, widgets):
        """Subtle hover highlight so sidebar items read as clickable."""
        def _on(_e):  frame.configure(fg_color=BG_HOVER)
        def _off(_e): frame.configure(fg_color="transparent")
        for w in widgets:
            w.bind("<Enter>", _on, add="+")
            w.bind("<Leave>", _off, add="+")

    def _content(self):
        """Main right-side area: header bar, tab frames, status bar."""
        cf = ctk.CTkFrame(self, corner_radius=0, fg_color=BG)
        cf.grid(row=0, column=1, sticky="nsew")
        cf.grid_rowconfigure(1, weight=1)
        cf.grid_columnconfigure(0, weight=1)

        # Header
        hdr = ctk.CTkFrame(cf, height=48, corner_radius=0, fg_color=BG_CARD)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        self._hdr_title = ctk.CTkLabel(hdr, text="Run",
                                       font=F("heading"), text_color=FG)
        self._hdr_title.pack(side="left", padx=16)
        self._phase_lbl = ctk.CTkLabel(hdr, text="Ready",
                                        font=F("label"), text_color=FG_DIM)
        self._phase_lbl.pack(side="right", padx=16)
        ctk.CTkFrame(cf, height=2, fg_color=ACCENT, corner_radius=0
                     ).grid(row=0, column=0, sticky="sew")

        # Tab stacking area
        self._tab_area = ctk.CTkFrame(cf, fg_color=BG, corner_radius=0)
        self._tab_area.grid(row=1, column=0, sticky="nsew")
        self._tab_area.grid_columnconfigure(0, weight=1)
        self._tab_area.grid_rowconfigure(0, weight=1)

        self._tabs = {}
        for name in ("run","errors","stats","history","settings"):
            tab = ctk.CTkFrame(self._tab_area, fg_color=BG, corner_radius=0)
            # Do NOT grid here — tabs are shown/hidden via pack/pack_forget in _nav
            self._tabs[name] = tab

        self._build_run()
        self._build_errors()
        self._build_stats()
        self._build_history()
        self._build_settings()

        # Status bar
        bar = ctk.CTkFrame(cf, height=24, corner_radius=0, fg_color=BG_CARD)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)
        self._status_var = ctk.StringVar(value="Ready.")
        ctk.CTkLabel(bar, textvariable=self._status_var,
                     font=F("small"), text_color=FG_DIM
                     ).pack(side="left", padx=10)

        # Show Run tab immediately — no delay, no flash
        self._nav(0)

    def _nav(self, idx: int):
        """
        Show exactly one tab frame, hide all others via pack/pack_forget.
        pack_forget removes the widget from layout entirely — tkinter stops
        rendering it, so there are zero ghost widgets during minimize/restore.
        """
        order = ("run", "errors", "stats", "history", "settings")

        # Track which tab is active to debounce history loads
        self._active_tab = idx

        for i, name in enumerate(order):
            tab = self._tabs[name]
            if i == idx:
                tab.pack(fill="both", expand=True)
            else:
                tab.pack_forget()

        # Page name in the header (the window title already says "Resuto")
        try:
            self._hdr_title.configure(
                text=("Run", "Errors", "Stats", "History", "Settings")[idx])
        except Exception:
            pass

        # Highlight active nav icon + dim others
        for i, (frm, ico) in enumerate(self._nav_btns):
            ico.configure(text_color=ACCENT if i == idx else FG_DIM)

        # Load history when navigating to it.
        # During a live run, _sched_stats also calls _load_history every 5s.
        if idx == 3:
            self._load_history()
        # Keep the resume-length info current (profile may have changed)
        try:
            if idx == 0:
                self._refresh_start_status()
            elif idx == 4:
                self._update_length_info()
        except Exception:
            pass

    # ── Run tab (wizard) ──────────────────────────────────────────
    # _build_run/_show_step → views/run_view.py

    def _card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=BG_CARD,
                             corner_radius=10, **kw)

    # run methods → views/run_view.py

    def _append_error(self, line: str):
        ts   = datetime.now().strftime("%H:%M:%S")
        warn = "[WARN]" in line and "[ERR]" not in line
        self._err_box.configure(state="normal")
        self._err_box.insert("end", f"[{ts}]  ", "ts")
        self._err_box.insert("end", line+"\n", "warn" if warn else "err")
        self._err_box.see("end")
        self._err_box.configure(state="disabled")
        self._err_count += 1
        self._err_cnt_lbl.configure(
            text=f"{self._err_count} issue{'s' if self._err_count!=1 else ''}",
            text_color=WARNING)
        # Only a real error (not a warning) may switch tabs, and never while
        # the bot is running — the user may be watching the Run tab
        if self._err_count == 1 and not warn and not self._live:
            self._nav(1)

    def _handle_done(self, code: int):
        if getattr(self, "_restarting", False):
            return
        # Flush any buffered traceback
        if getattr(self, "_tb_active", False) and self._tb_buffer:
            self._append_error("\n".join(self._tb_buffer))
            self._tb_buffer = []
            self._tb_active = False

        self._live = False
        self._live_dot.configure(text_color=MUTED)
        self._stop_btn.pack_forget()
        self._hide_action_bar()
        self._act_strip.grid_forget()
        self._act_strip_visible = False

        user_stopped = getattr(self, "_user_stopped", False)
        self._user_stopped = False  # reset flag
        ok = (code == 0) or user_stopped  # user stop is not an error

        if code == 3 and not user_stopped:
            # Bot exit code 3 = Anthropic credit balance too low (core/costs.py)
            from core.costs import BILLING_URL
            self._set_phase("Out of Anthropic credit")
            self._set_status("Run stopped — your Anthropic credit balance is too low.")
            if messagebox.askyesno(
                    "Out of Anthropic credit",
                    "The run stopped because your Anthropic credit balance "
                    "is too low.\n\nAdd credits, then start the run again.\n\n"
                    "Open the Anthropic billing page now?"):
                import webbrowser
                webbrowser.open(BILLING_URL)
            self._stats_last_hash = None
            self._refresh_stats()
            self._show_step(3)
            self._nav(0)
            return

        if user_stopped:
            self._set_phase("Stopped")
            self._set_status("Stopped by user.")
        elif ok:
            self._set_phase("Run complete")
            self._set_status("Finished. %d issue(s)." % self._err_count)
        else:
            self._set_phase("Stopped with errors")
            self._set_status("Exit code %d. Check Errors tab." % code)
            if self._err_count == 0:
                msg = (
                    "Bot process exited unexpectedly (code %d).\n"
                    "Check the log file for details.\n"
                    "Common causes: Chrome profile locked, "
                    "internet issue, or import error in bot subprocess."
                ) % code
                self._append_error(msg)

        self._stats_last_hash = None
        self._refresh_stats()
        self._show_step(3)

        if not ok and self._err_count > 0:
            self._nav(1)   # errors tab — only if actual errors logged
        else:
            self._nav(3)   # history tab (index 3; 2 is Stats)

    # ── Helpers ────────────────────────────────────────────────────
    def _set_status(self, t: str):
        self._status_var.set(t)

    def _set_phase(self, t: str):
        self._phase_lbl.configure(text=t)
        # Keep the bottom status bar in step during a run (it stayed "Starting...")
        if getattr(self, "_live", False):
            self._status_var.set(t)

    # ── Settings tab ─────────────────────────────────────────────
    # Settings methods → views/settings_view.py

    # ── Auto-update ───────────────────────────────────────────────
    def _offer_update(self, info: dict):
        if self._runner and self._runner.running():
            return   # don't interrupt a live run; offered again next launch
        notes = (info.get("release_notes") or "").strip()
        msg = "Resuto %s is available (you have the older version).\n\n" % info["version"]
        if notes:
            msg += notes[:400] + "\n\n"
        msg += "Download and install now? Resuto will restart."
        if not messagebox.askyesno("Update available", msg):
            return
        self._set_status("Downloading update %s..." % info["version"])

        def _work():
            from core.updater import download_and_install
            ok = download_and_install(
                info["download_url"], info["version"],
                progress_cb=lambda pct: self._q.put(("update_progress", pct)),
                sha256_url=info.get("sha256_url"))
            self._q.put(("update_done", ok))
        threading.Thread(target=_work, daemon=True).start()

    def _on_update_done(self, ok: bool):
        if ok:
            self._set_status("Installing update — Resuto will restart...")
            self.after(800, self._on_close)
        else:
            self._set_status("Update failed — see the log. You can keep using this version.")
            messagebox.showwarning(
                "Update failed",
                "The update could not be downloaded or verified.\n"
                "You can keep using this version and try again later.")

    def _on_close(self):
        """Clean shutdown: cancel timers, clear unsaved API key, close bot."""
        if self._stats_after_id:
            self.after_cancel(self._stats_after_id)
        # Clear API key if user chose not to save it
        if not self._api_save_pref.get():
            _clear_api_key()
        if self._runner and self._runner.running():
            self._runner.send("stop")   # unblocks stdin.readline in main.py
            # Give 400ms for clean browser close, then hard-terminate
            self.after(400, lambda: (
                self._runner.stop()
                if self._runner and self._runner.running() else None
            ))
            self.after(500, self.destroy)
        else:
            self.destroy()

    # _open_review_window/_on_review_done → views/run_view.py


def _install_browsers() -> int:
    """Download Playwright Chromium with a small progress window."""
    import subprocess as _sp
    import threading as _th
    result = {"code": 1, "error": ""}

    def _run():
        try:
            from playwright._impl._driver import (compute_driver_executable,
                                                  get_driver_env)
            driver = compute_driver_executable()
            cmd = list(driver) if isinstance(driver, (tuple, list)) else [str(driver)]
            flags = _sp.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            r = _sp.run(cmd + ["install", "chromium"], env=get_driver_env(),
                        capture_output=True, text=True, creationflags=flags)
            result["code"] = r.returncode
            if r.returncode != 0:
                result["error"] = (r.stderr or r.stdout)[-600:]
        except Exception as e:
            result["error"] = str(e)

    worker = _th.Thread(target=_run, daemon=True)
    try:
        import tkinter as _tk
        from tkinter import ttk as _ttk, messagebox as _mb
        root = _tk.Tk()
        root.title("Resuto — Downloading browser")
        root.geometry("420x120")
        root.resizable(False, False)
        _tk.Label(root, text="Downloading Chromium (about 150 MB).\n"
                             "This can take a few minutes…").pack(pady=(16, 8))
        bar = _ttk.Progressbar(root, mode="indeterminate", length=360)
        bar.pack()
        bar.start(12)

        def _check():
            if worker.is_alive():
                root.after(300, _check)
                return
            root.destroy()
            if result["code"] != 0:
                _mb.showerror("Resuto", "Chromium download failed:\n\n"
                              + (result["error"] or "unknown error")
                              + "\n\nYou can retry later with: resuto.exe --install-browsers")
        worker.start()
        root.after(300, _check)
        root.mainloop()
    except Exception:
        # No GUI available — run in the foreground
        if not worker.is_alive():
            worker.start()
        worker.join()
        print(result["error"] or "Chromium installed.", flush=True)
    return result["code"]


if __name__ == "__main__":
    # ── Bot mode — launched by the GUI as a subprocess ────────────
    # When running as a PyInstaller exe, the GUI spawns itself with
    # --bot-mode to run the orchestrator instead of opening another window.
    if "--install-browsers" in sys.argv:
        # Install Playwright's Chromium — called from the installer or by the user.
        # A compiled exe can't run "python -m playwright", so call Playwright's
        # bundled driver directly (that is what `playwright install` does).
        sys.exit(_install_browsers())

    if "--bot-mode" in sys.argv:
        # Wrap EVERYTHING in try/except — no silent crash possible
        try:
            sys.argv.remove("--bot-mode")
            import asyncio

            # Use sys.executable.parent — reliable in Nuitka (never use __file__ which can be None)
            _root = str(Path(sys.executable).parent)
            if _root not in sys.path:
                sys.path.insert(0, _root)

            print("[BOT] Step 1: importing orchestrator...", flush=True)
            from backend.orchestrator import run_from_argv as _bot_run
            print("[BOT] Step 2: starting bot...", flush=True)
            # Same argparse as source mode — every flag (filters, date,
            # --application-mode, --phase2-only, --job-ids) now works in the exe
            _bot_run(sys.argv[1:])
            print("[BOT] Done.", flush=True)

        except (KeyboardInterrupt, EOFError):
            pass
        except Exception as _bot_err:
            import traceback as _tb
            full_tb = _tb.format_exc()
            # Write to log file so Open Log shows it
            try:
                from core.logger import _get_log_file
                import os as _os2
                _lp = _get_log_file()
                _os2.makedirs(_os2.path.dirname(_lp), exist_ok=True)
                with open(_lp, "a", encoding="utf-8") as _lf:
                    _lf.write("\n[BOT CRASH]\n" + full_tb + "\n")
            except Exception:
                pass
            # Print each line so GUI traceback buffer captures all
            for _line in full_tb.split("\n"):
                if _line.strip():
                    print(_line, flush=True)
            print("[!!] " + type(_bot_err).__name__ + ": " + str(_bot_err), flush=True)
            sys.exit(1)

        sys.exit(0)



    # ── Normal GUI mode ───────────────────────────────────────────
    log = Path(sys.executable).parent / "app_error.log"
    try:
        app = App()
        # Only call mainloop if the window wasn't destroyed during __init__
        # (e.g. registration denied or user closed the gate window)
        try:
            if app.winfo_exists():
                app.mainloop()
        except Exception:
            pass
    except Exception:
        err = traceback.format_exc()
        # Ignore clean exit errors (TclError from destroyed window)
        if "can't invoke" in err or "application has been destroyed" in err:
            pass
        else:
            try:
                log.write_text(err, encoding="utf-8")
            except Exception:
                pass
            try:
                import tkinter as _tk
                _r = _tk.Tk()
                _r.title("Startup Error")
                _r.geometry("700x420")
                _r.configure(bg="#0F1117")
                _tk.Label(_r, text="Resuto — Startup Error",
                          bg="#0F1117", fg="#E84545",
                          font=("Segoe UI", 13, "bold")).pack(pady=(16,4))
                _tk.Label(_r, text=f"Log: {log}",
                          bg="#0F1117", fg="#6B7280",
                          font=("Segoe UI", 9)).pack()
                tb = _tk.Text(_r, bg="#1C1F26", fg="#F1F2F4",
                              font=("Consolas", 9), wrap="word")
                tb.pack(fill="both", expand=True, padx=12, pady=8)
                tb.insert("end", err)
                tb.configure(state="disabled")
                _tk.Button(_r, text="Close", command=_r.destroy,
                           bg="#2A2D35", fg="white").pack(pady=(0,12))
                _r.mainloop()
            except Exception:
                pass