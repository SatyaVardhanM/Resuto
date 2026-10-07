"""
frontend/views/run_view.py
──────────────────────────
RunView — the Run tab.
Manages: step flow, role analysis, bot start/stop,
_handle_line, one-by-one mode, review window.
"""
import json
import os
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import queue
import re
import shutil
import subprocess
import time
import traceback

from frontend.bot_runner import BotRunner
from frontend.constants import (
    BG, BG_CARD, BG_FIELD, BG_HOVER, LINE,
    ACCENT, ACCENT_HV, ACCENT_TXT, ACCENT_SOFT, QUEUED, C,
    DANGER, SUCCESS, WARNING, STRETCH, MUTED,
    FG, FG_SOFT, FG_DIM,
    F, _FONT_FAMILY, _FONTS, _BASE_SIZE,
    APP_TITLE, BOT_SCRIPT,
    _settings_file, _load_api_key, _save_api_key, _clear_api_key,
    _init_fonts, _load_font_pref,
    # Bot-output patterns — previously only defined in app.py, which made
    # _handle_line raise NameError on every line
    _ERROR_RE, _PHASE_MAP, _DSQ_RE, _NF_RE, _APPLY_RE,
    _IDLE_RE, _LAST_JOB_RE,
)
from frontend.views.dialogs   import ReviewWindow
from frontend.branding        import logo_image


class RunMixin:
    """Run tab — role selection, bot control, live output."""

    def _build_run(self):
        f = self._tabs["run"]
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)

        # Top bar: step label + stop button
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=24, pady=(14, 0))
        self._run_topbar = top
        self._step_lbl = ctk.CTkLabel(top, text="", font=F("small"),
                                       text_color=FG_DIM)
        self._step_lbl.pack(side="left")
        self._stop_btn = ctk.CTkButton(top, text="■  Stop", width=84, height=32,
                                        fg_color="transparent", text_color=DANGER,
                                        border_width=1, border_color=DANGER,
                                        hover_color=BG_HOVER, corner_radius=8,
                                        command=self._stop)
        self._stop_btn.pack(side="right")
        self._stop_btn.pack_forget()

        # Step container
        self._step_area = ctk.CTkFrame(f, fg_color="transparent")
        self._step_area.grid(row=1, column=0, sticky="nsew", padx=24, pady=(10, 14))
        self._step_area.grid_columnconfigure(0, weight=1)
        self._step_area.grid_rowconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)

        # Action bar for Phase 3 prompts
        self._action_bar = ctk.CTkFrame(f, fg_color=BG_CARD, corner_radius=8)

        # Steps: 0=start, 1=search settings, 2=analysing, 3=select roles, 4=running
        self._steps = []
        for build_fn in (self._s_start, self._s2, self._s3, self._s4, self._s_running):
            sf = ctk.CTkFrame(self._step_area, fg_color="transparent")
            sf.grid_columnconfigure(0, weight=1)
            build_fn(sf)
            self._steps.append(sf)
        self._show_step(0)

    def _show_step(self, idx: int):
        # Home (step 0) has no step title / Stop row
        try:
            if idx in (0, 4):
                self._run_topbar.grid_remove()
            else:
                self._run_topbar.grid()
        except Exception:
            pass
        for i, s in enumerate(self._steps):
            if i == idx:
                s.place(relx=0, rely=0, relwidth=1, relheight=1)
            else:
                s.place_forget()
        labels = ["",
                  "Step 1 of 3",
                  "Step 2 of 3",
                  "Step 3 of 3",
                  "Running"]
        self._step_lbl.configure(text=labels[min(idx, len(labels)-1)])


    def _s_start(self, f):
        """
        Step 0 — Home: hero card (Start run, this week's numbers), the search
        Resuto will run, and today's activity. Validates API key and profile
        when Start is pressed.
        """
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)

        # ── Hero ──────────────────────────────────────────────────
        hero = ctk.CTkFrame(f, fg_color=BG_CARD, corner_radius=16,
                            border_width=1, border_color=LINE)
        hero.grid(row=0, column=0, sticky="ew", pady=(4, 12))
        hero.grid_columnconfigure(0, weight=1)
        ctk.CTkFrame(hero, height=3, corner_radius=2, fg_color=ACCENT
                     ).grid(row=0, column=0, columnspan=2, sticky="ew",
                            padx=14, pady=(3, 0))
        self._home_eyebrow = ctk.CTkLabel(hero, text="READY", font=F("small_b"),
                                          text_color=ACCENT_TXT)
        self._home_eyebrow.grid(row=1, column=0, columnspan=2, sticky="w",
                                padx=26, pady=(14, 0))
        ctk.CTkLabel(hero, text="Your next role is out there.", font=F("stat"),
                     text_color=FG).grid(row=2, column=0, columnspan=2,
                                         sticky="w", padx=26)
        ctk.CTkLabel(hero, text=("Resuto searches, tailors your resume for each "
                                 "job, and applies while you do something else."),
                     font=F("label"), text_color=FG_DIM, justify="left",
                     wraplength=560).grid(row=3, column=0, columnspan=2,
                                          sticky="w", padx=26, pady=(2, 14))

        btns = ctk.CTkFrame(hero, fg_color="transparent")
        btns.grid(row=4, column=0, sticky="w", padx=26, pady=(0, 18))
        self._start_btn_outer = btns
        self._start_main_btn = ctk.CTkButton(
            btns, text="▶  Start run", width=140, height=40, font=F("body_b"),
            fg_color=ACCENT, hover_color=ACCENT_HV, text_color="#FFFFFF",
            corner_radius=10, command=self._start_validate)
        self._start_main_btn.pack(side="left")
        self._home_queue_btn = ctk.CTkButton(
            btns, text="Review queue", width=150, height=40, font=F("body_b"),
            fg_color=BG_FIELD, hover_color=BG_HOVER, text_color=FG,
            border_width=1, border_color=LINE, corner_radius=10,
            command=self._start_apply_queue)
        self._start_err = ctk.CTkLabel(btns, text="", font=F("small"),
                                       text_color=DANGER, wraplength=320,
                                       justify="left")
        self._start_err.pack(side="left", padx=(14, 0))

        stats = ctk.CTkFrame(hero, fg_color="transparent")
        stats.grid(row=4, column=1, sticky="e", padx=26, pady=(0, 18))
        self._home_stat = {}
        for key, cap in (("week", "applied this week"), ("avg", "avg match"),
                         ("queued", "queued")):
            col = ctk.CTkFrame(stats, fg_color="transparent")
            col.pack(side="left", padx=(24, 0))
            v = ctk.CTkLabel(col, text="–", font=F("title"), text_color=FG)
            v.pack(anchor="e")
            ctk.CTkLabel(col, text=cap, font=F("tiny"),
                         text_color=MUTED).pack(anchor="e")
            self._home_stat[key] = v

        # ── Search summary + today's activity ─────────────────────
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.grid(row=1, column=0, sticky="nsew")
        row.grid_rowconfigure(0, weight=1)
        row.grid_columnconfigure(0, weight=10, uniform="home")
        row.grid_columnconfigure(1, weight=19, uniform="home")

        sc = ctk.CTkFrame(row, fg_color=BG_CARD, corner_radius=14,
                          border_width=1, border_color=LINE)
        sc.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        sh = ctk.CTkFrame(sc, fg_color="transparent")
        sh.pack(fill="x", padx=18, pady=(14, 6))
        ctk.CTkLabel(sh, text="Search", font=F("body_b"),
                     text_color=FG).pack(side="left")
        ctk.CTkButton(sh, text="Edit", width=40, height=24, font=F("small_b"),
                      fg_color="transparent", hover_color=BG_HOVER,
                      text_color=ACCENT_TXT, command=lambda: self._nav(4)
                      ).pack(side="right")
        self._home_kv = {}
        kv = ctk.CTkFrame(sc, fg_color="transparent")
        kv.pack(fill="x", padx=18)
        kv.grid_columnconfigure(1, weight=1)
        for r, (key, cap) in enumerate((("where", "Where"), ("posted", "Posted"),
                                        ("filters", "Filters"), ("mode", "Mode"),
                                        ("resume", "Resume"))):
            ctk.CTkLabel(kv, text=cap, font=F("small"), text_color=MUTED,
                         anchor="w", width=62).grid(row=r, column=0, sticky="nw", pady=3)
            v = ctk.CTkLabel(kv, text="–", font=F("small_b"), text_color=FG,
                             anchor="w", justify="left", wraplength=190)
            v.grid(row=r, column=1, sticky="w", pady=3)
            self._home_kv[key] = v
        # Readiness (profile + API key) — click a missing item to fix it
        ready = ctk.CTkFrame(sc, fg_color="transparent")
        ready.pack(fill="x", padx=18, pady=(10, 14), side="bottom")
        self._start_profile_dot = ctk.CTkLabel(ready, text="", font=F("small"),
                                               text_color=MUTED, anchor="w")
        self._start_profile_dot.pack(fill="x")
        self._start_key_dot = ctk.CTkLabel(ready, text="", font=F("small"),
                                           text_color=MUTED, anchor="w")
        self._start_key_dot.pack(fill="x")
        # Kept for _refresh_start_status (resume length is shown in the card)
        self._start_len_lbl = ctk.CTkLabel(sc, text="")

        tc = ctk.CTkFrame(row, fg_color=BG_CARD, corner_radius=14,
                          border_width=1, border_color=LINE)
        tc.grid(row=0, column=1, sticky="nsew")
        th = ctk.CTkFrame(tc, fg_color="transparent")
        th.pack(fill="x", padx=18, pady=(14, 4))
        ctk.CTkLabel(th, text="Recent activity", font=F("body_b"),
                     text_color=FG).pack(side="left")
        ctk.CTkButton(th, text="All activity", width=80, height=24,
                      font=F("small_b"), fg_color="transparent",
                      hover_color=BG_HOVER, text_color=ACCENT_TXT,
                      command=lambda: self._nav(3)).pack(side="right")
        self._home_tl = ctk.CTkFrame(tc, fg_color="transparent")
        self._home_tl.pack(fill="both", expand=True, padx=18, pady=(0, 12))

        self._refresh_start_status()

    # ── Home data ─────────────────────────────────────────────────
    _STATUS_STYLE = {
        "applied":      ("Applied",  SUCCESS),
        "matched":      ("Queued",   QUEUED),
        "resume_ready": ("Queued",   QUEUED),
        "skipped":      ("Skipped",  MUTED),
        "failed":       ("Failed",   DANGER),
        "scanning":     ("Checking", ACCENT_TXT),
    }

    @staticmethod
    def _ago(ts: str) -> str:
        try:
            dt = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
            secs = max(0, (datetime.now() - dt).total_seconds())
        except Exception:
            return ""
        if secs < 60:
            return "now"
        if secs < 3600:
            return "%dm ago" % (secs // 60)
        if secs < 86400:
            return "%dh ago" % (secs // 3600)
        return "%dd ago" % (secs // 86400)

    def _home_refresh(self):
        """Fill the Home numbers, search summary and recent activity."""
        if not hasattr(self, "_home_stat"):
            return
        # Numbers + recent jobs
        try:
            from frontend.app import _read_stats
            from datetime import timedelta
            allst  = _read_stats() or {}
            week   = _read_stats(since=(datetime.now() - timedelta(days=7)
                                        ).strftime("%Y-%m-%d")) or {}
        except Exception:
            allst, week = {}, {}
        counts  = allst.get("counts", {}) or {}
        queued  = int(allst.get("queued", 0) or 0)
        applied_week = int((week.get("counts", {}) or {}).get("applied", 0) or 0)
        avg = int(allst.get("avg", 0) or 0)
        self._home_stat["week"].configure(text=str(applied_week))
        self._home_stat["avg"].configure(text=("%d%%" % avg) if avg else "–")
        self._home_stat["queued"].configure(text=str(queued))
        if queued > 0:
            self._home_queue_btn.configure(text="Review queue (%d)" % queued)
            if not self._home_queue_btn.winfo_ismapped():
                self._home_queue_btn.pack(side="left", padx=(10, 0),
                                          before=self._start_err)
        else:
            self._home_queue_btn.pack_forget()

        # Search summary (Settings → Job preferences + resume length)
        try:
            prefs = self._load_job_prefs()
        except Exception:
            prefs = {}
        place = {"on_site": "On-site", "remote": "Remote", "hybrid": "Hybrid"}
        wp = [place.get(v, v) for v in prefs.get("workplace", [])] or ["Any workplace"]
        posted = {"any": "Any time", "month": "Past month", "week": "Past week",
                  "24hr": "Past 24 hours"}.get(prefs.get("date_posted", "any"), "Any time")
        jt = [str(v).replace("_", " ").title() for v in prefs.get("job_types", [])]
        filt = ["Easy Apply only" if prefs.get("easy_apply_only", True) else "All apply types"]
        filt += jt[:3]
        mode = {"continuous": "Prepare all first",
                "one_at_a_time": "One job at a time"}.get(
                    prefs.get("application_mode", "continuous"), "Prepare all first")
        try:
            from api.resume_length import get_setting
            resume = {"auto": "Auto length", "1": "1 page",
                      "2": "Up to 2 pages"}[get_setting()] + " · tailored per job"
        except Exception:
            resume = "Tailored per job"
        for k, v in (("where", " · ".join(wp)), ("posted", posted),
                     ("filters", " · ".join(filt)), ("mode", mode),
                     ("resume", resume)):
            self._home_kv[k].configure(text=v)

        # Eyebrow
        if queued:
            self._home_eyebrow.configure(
                text="READY  ·  %d JOB%s QUEUED FOR REVIEW" % (queued, "" if queued == 1 else "S"))
        else:
            self._home_eyebrow.configure(text="READY")

        # Recent activity (timeline)
        self._fill_timeline(self._home_tl, (allst.get("jobs") or [])[:4],
                            "No activity yet. Press Start run to find your first matches.")

    def _fill_timeline(self, frame, jobs, empty_text):
        """Timeline rows: dot · role · company/match/reason · time · status."""
        for w in frame.winfo_children():
            w.destroy()
        if not jobs:
            ctk.CTkLabel(frame, text=empty_text, font=F("small"), text_color=MUTED,
                         wraplength=420, justify="left").pack(anchor="w", pady=(12, 0))
            return
        for j in jobs:
            label, col = self._STATUS_STYLE.get(j.get("status", ""),
                                                (str(j.get("status", "")).title(), MUTED))
            r = ctk.CTkFrame(frame, fg_color="transparent")
            r.pack(fill="x", pady=5)
            r.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(r, text="●", font=F("small"), text_color=col, width=14
                         ).grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 8))
            ctk.CTkLabel(r, text=(j.get("job_title") or "Untitled role")[:60],
                         font=F("small_b"), text_color=FG, anchor="w"
                         ).grid(row=0, column=1, sticky="w")
            sub = [j.get("company") or ""]
            if j.get("match_score"):
                sub.append("%s%% match" % j.get("match_score"))
            reason = (j.get("ai_reason") or j.get("notes") or "").strip().split("\n")[0]
            if reason:
                sub.append(reason[:70])
            ctk.CTkLabel(r, text=" · ".join(x for x in sub if x), font=F("tiny"),
                         text_color=MUTED, anchor="w"
                         ).grid(row=1, column=1, sticky="w")
            ctk.CTkLabel(r, text=self._ago(j.get("logged_at", "")), font=F("tiny"),
                         text_color=MUTED).grid(row=0, column=2, sticky="e", padx=(8, 8))
            ctk.CTkLabel(r, text=" %s " % label, font=F("tiny"), text_color=col,
                         fg_color=BG_FIELD, corner_radius=8
                         ).grid(row=0, column=3, sticky="e")

    def _refresh_start_status(self):
        """Update profile and API key status dots on the start screen."""
        has_xml = Path(self._xml_path()).exists()
        has_key = bool(self._api_var.get().strip())
        self._start_profile_dot.configure(
            text=("✓  Resume profile ready" if has_xml
                  else "○  Add your resume profile  →"),
            text_color=SUCCESS if has_xml else WARNING,
            cursor="arrow" if has_xml else "hand2")
        self._start_key_dot.configure(
            text=("✓  API key ready" if has_key
                  else "○  Add your API key  →"),
            text_color=SUCCESS if has_key else WARNING,
            cursor="arrow" if has_key else "hand2")
        for _lbl, _ok in ((self._start_profile_dot, has_xml),
                          (self._start_key_dot, has_key)):
            _lbl.unbind("<Button-1>")
            if not _ok:
                _lbl.bind("<Button-1>", lambda e: self._nav(4))
        try:
            self._home_refresh()
        except Exception:
            pass
        try:
            txt = ""
            if has_xml:
                from core.profile import load_profile_from_xml
                from api.resume_length import describe_target, profile_overview
                prof = load_profile_from_xml(self._xml_path())
                ov = profile_overview(prof)
                txt = ("Resume length: %s  •  your profile ≈ %.1f pages (%.1f yrs)"
                       % (describe_target(prof), ov["pages"], ov["years"]))
            self._start_len_lbl.configure(text=txt)
        except Exception:
            pass

    def _start_pulse(self):
        """Former pulsing Start button — the redesign keeps it still."""
        return
        if not hasattr(self, "_pulse_state"):
            self._pulse_state = 0
        colours = [ACCENT, ACCENT_HV, "#7B89F8", ACCENT_HV, ACCENT]
        idx = self._pulse_state % len(colours)
        try:
            visible = (getattr(self, "_active_tab", 0) == 0
                       and self._start_main_btn.winfo_ismapped())
            if visible and not getattr(self, "_pulse_hover", False):
                self._start_main_btn.configure(fg_color=colours[idx])
                self._pulse_state += 1
        except Exception:
            return   # window closed
        # Slow the pulse down — 600ms per step
        self.after(600, self._start_pulse)

    def _start_validate(self):
        """Validate prerequisites, then move to search settings."""
        key = self._api_var.get().strip()
        if not key:
            self._start_err.configure(
                text="Add your Claude API key in Settings first.")
            self._nav(4)   # open settings
            return
        if not key.startswith("sk-ant-"):
            self._start_err.configure(
                text="Claude API keys start with sk-ant-")
            self._nav(4)
            return
        xml = Path(self._xml_path())
        if not xml.exists():
            self._start_err.configure(
                text="No resume profile found. Upload your resume in Settings first.")
            self._nav(4)
            return
        self._start_err.configure(text="")

        # Profile already longer than 2 pages → explain once per session
        if not getattr(self, "_len_warned", False):
            try:
                from core.profile import load_profile_from_xml
                from api.resume_length import (profile_overview, target_pages)
                prof = load_profile_from_xml(str(xml))
                ov   = profile_overview(prof)
                if ov["pages"] > 2:
                    self._len_warned = True
                    tgt = target_pages(prof)
                    msg = ("Your resume profile is long: %d roles and %d bullets — "
                           "about %.1f pages.\n\n"
                           "Each tailored resume is limited to %s, so a lot will be "
                           "left out of every resume, and older roles will show only "
                           "title, company and dates. Your profile itself is never "
                           "changed.\n\n"
                           "You can upload a shorter resume in Settings, or continue "
                           "and let Resuto choose what fits for each job.\n\n"
                           "Update your resume now?"
                           % (ov["roles"], ov["bullets"], ov["pages"],
                              "1 page" if tgt == 1 else "2 pages"))
                    if messagebox.askyesno("Your resume profile is long", msg):
                        self._nav(4)
                        return
            except Exception:
                pass
        self._show_step(1)


    def _s2(self, f):
        ctk.CTkLabel(f, text="Search settings", font=F("title"),
                     text_color=FG).pack(anchor="w", pady=(6, 2))
        ctk.CTkLabel(f, text="Where to look and how many jobs to apply to on this run.",
                     font=F("small"), text_color=FG_DIM).pack(anchor="w", pady=(0, 12))
        c = self._card(f); c.pack(fill="x", pady=(0,4))

        row = ctk.CTkFrame(c, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=14)
        row.grid_columnconfigure(0, weight=3)  # location gets more space
        row.grid_columnconfigure(1, weight=0, minsize=16)
        row.grid_columnconfigure(2, weight=1)  # max-jobs narrower

        ctk.CTkLabel(row, text="Location", font=F("small"),
                     text_color=FG_DIM).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row, text="Max applications", font=F("small"),
                     text_color=FG_DIM).grid(row=0, column=2, sticky="w")
        self._loc_var = ctk.StringVar()
        ctk.CTkEntry(row, textvariable=self._loc_var,
                     placeholder_text="United States", height=36
                     ).grid(row=1, column=0, sticky="ew", pady=(3,0))
        self._maxjobs_var = ctk.StringVar(value="5")
        ctk.CTkEntry(row, textvariable=self._maxjobs_var,
                     height=36).grid(row=1, column=2, sticky="ew", pady=(3,0))
        ctk.CTkLabel(row, text="Blank = United States", font=F("small"),
                     text_color=FG_DIM).grid(row=2, column=0, sticky="w", pady=(3,0))
        ctk.CTkLabel(row, text="0 = unlimited", font=F("small"),
                     text_color=FG_DIM).grid(row=2, column=2, sticky="w", pady=(3,0))

        # Estimated Claude cost — Anthropic has no balance API, so show what
        # this run will roughly cost and let the user check their balance
        cost_row = ctk.CTkFrame(c, fg_color="transparent")
        cost_row.pack(fill="x", padx=16, pady=(0, 12))
        self._cost_lbl = ctk.CTkLabel(cost_row, text="", font=F("small"),
                                      text_color=FG_DIM, justify="left",
                                      anchor="w", wraplength=360)
        self._cost_lbl.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(cost_row, text="Check my balance", width=130, height=28,
                      font=F("small"), fg_color=BG_FIELD, text_color=FG, hover_color=BG_HOVER,
                      command=self._open_billing).pack(side="right")
        self._maxjobs_var.trace_add("write", lambda *_: self._update_cost_estimate())
        self._update_cost_estimate()

        ctk.CTkFrame(c, height=1, fg_color=LINE).pack(fill="x", padx=16)
        ctk.CTkLabel(c,
                     text="Job preferences (experience level, job type, workplace, easy apply) are set in the Settings tab.",
                     font=F("small"), text_color=FG_DIM,
                     wraplength=440, justify="left").pack(anchor="w", padx=16, pady=(10, 14))

        self._clear_var = ctk.BooleanVar()
        ctk.CTkCheckBox(f, text="Clear unfinished previous run before starting",
                         variable=self._clear_var, font=F("small"),
                         text_color=FG_DIM).pack(anchor="w", pady=(10, 0))
        nav = ctk.CTkFrame(f, fg_color="transparent")
        nav.pack(fill="x", pady=(16, 0))
        ctk.CTkButton(nav, text="Back", width=100,
                      fg_color=BG_CARD, text_color=FG, hover_color=BG_HOVER,
                      command=lambda: self._show_step(0)).pack(side="left")
        ctk.CTkButton(nav, text="Analyse my profile",
                      command=self._s2_next).pack(side="right")

    # ── Cost estimate ─────────────────────────────────────────────
    def _open_billing(self):
        import webbrowser
        from core.costs import BILLING_URL
        webbrowser.open(BILLING_URL)

    def _update_cost_estimate(self):
        try:
            from core.costs import estimate_run
            est = estimate_run(self._maxjobs_var.get().strip() or 5, roles=1)
            basis = ("Based on your recent runs." if est["learned"]
                     else "Rough estimate — gets more accurate after a few runs.")
            self._cost_lbl.configure(
                text="Estimated Claude cost: ~$%.2f per role searched (up to $%.2f)\n%s"
                     % (est["typical"], est["high"], basis))
        except Exception:
            self._cost_lbl.configure(text="")

    def _confirm_run_cost(self, roles: int) -> bool:
        """Show the estimated cost of this run and ask to continue."""
        try:
            from core.costs import estimate_run
            est = estimate_run(self._maxjobs_var.get().strip() or 5, roles=roles)
        except Exception:
            return True   # never block a run because the estimate failed
        basis = ("Based on the real cost of your recent runs." if est["learned"]
                 else "Rough estimate — it gets more accurate after a few runs.")
        msg = ("This run searches %d role(s), up to %d job(s) each.\n\n"
               "Estimated Anthropic cost: about $%.2f (up to $%.2f).\n%s\n\n"
               "Make sure your Anthropic credit balance covers this.\n"
               "(\"Check my balance\" on the previous screen opens the billing page.)\n\n"
               "Start the run?"
               % (est["roles"], est["per_role"], est["typical"], est["high"], basis))
        return messagebox.askokcancel("Estimated cost", msg)

    def _s2_next(self):
        self._show_step(2)
        threading.Thread(target=self._fetch_roles, daemon=True).start()

    def _s3(self, f):
        f.grid_rowconfigure(0, weight=1)
        inner = ctk.CTkFrame(f, fg_color="transparent")
        inner.place(relx=0.5, rely=0.4, anchor="center")
        ctk.CTkLabel(inner, text="Analysing your profile",
                     font=F("title"), text_color=FG).pack(pady=(0,12))
        self._s3_status = ctk.CTkLabel(inner, text="Connecting to Claude...",
                                        font=F("label"), text_color=FG_DIM)
        self._s3_status.pack()
        self._s3_err = ctk.CTkLabel(inner, text="", font=F("small"),
                                     text_color=DANGER, wraplength=500)
        self._s3_err.pack(pady=(10,0))

    def _fetch_roles(self):
        try:
            import anthropic as _ant
            from core.config import AI_MODEL as _model

            # ── Pre-flight checks — fail fast with clear messages ──
            api_key = self._api_var.get().strip()
            if not api_key:
                self._q.put(("roles_error",
                    "No API key set. Please enter your Anthropic API key in Settings."))
                return

            xml = self._xml_path()
            if not xml:
                self._q.put(("roles_error",
                    "No resume XML file set. Please link your resume_data.xml in Settings."))
                return

            import os as _os
            if not _os.path.exists(xml):
                self._q.put(("roles_error",
                    f"Resume XML not found: {xml}\nPlease check the path in Settings."))
                return

            # Log what we're working with so failures are traceable
            import os as _os2
            print(f"[Analyse] API key: {'set' if api_key else 'MISSING'}")
            print(f"[Analyse] XML path: {xml}")
            print(f"[Analyse] XML exists: {_os2.path.exists(xml)}")
            self._q.put(("s3_status", "Loading your profile..."))

            # Load profile from XML
            _mp = {}
            try:
                from core.profile import load_profile_from_xml
                _mp = load_profile_from_xml(xml)
            except Exception as xml_err:
                try:
                    from core.profile import MY_PROFILE as _mp_mod
                    _mp = _mp_mod
                except Exception:
                    self._q.put(("roles_error",
                        f"Failed to load profile: {xml_err}"))
                    return

            if not _mp:
                self._q.put(("roles_error",
                    "Profile is empty. Please complete your resume_data.xml first."))
                return

            skills  = [s for v in _mp.get("skills",{}).values() for s in v]
            years   = _mp.get("years_experience", "unknown")
            summary = _mp.get("summary", "")
            exp     = _mp.get("experience", [])
            exp_str = "; ".join(
                f"{j.get('title','')} at {j.get('company','')}"
                for j in exp[:3] if j.get("title")
            )
            edu     = ", ".join(
                f"{d.get('degree','')} at {d.get('school','')}"
                for d in _mp.get("education",[]) if d.get("degree")
            ) or "not specified"

            prompt = (
                "You are a senior tech recruiter and career advisor.\n"
                "Suggest LinkedIn job search keywords for this candidate.\n"
                "For each role, estimate the probability (0-100%) that a job "
                "with that title actually matches this candidate's background.\n\n"
                f"Years of experience: {years}\n"
                f"Recent roles: {exp_str}\n"
                f"Education: {edu}\n"
                f"Summary: {summary}\n"
                f"Skills: {json.dumps(skills[:30])}\n\n"
                "Rules:\n"
                "- Only suggest roles where the candidate has REAL matching skills\n"
                "- estimate_match is the % of job postings with this title "
                "that would actually match this candidate's background\n"
                "- Include 12-15 roles ordered from highest to lowest match\n\n"
                "Return ONLY a JSON array:\n"
                "[{\"role\": \"Job Title\", \"match\": 85, "
                "\"reason\": \"Why this matches in 5 words\"}, ...]"
            )
            self._q.put(("s3_status", "Asking Claude for suggestions..."))
            client = _ant.Anthropic(
                api_key=self._api_var.get().strip(),
                timeout=60.0,          # 60 second timeout -- no silent hangs
            )
            msg = client.messages.create(
                model=_model,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            text = msg.content[0].text.strip()
            if "```" in text:
                parts = text.split("```")
                text = parts[1][4:] if parts[1].startswith("json") else parts[1]
            # Find JSON array boundaries
            _js = text.find("[")
            _je = text.rfind("]") + 1
            if _js >= 0 and _je > _js:
                text = text[_js:_je]
            try:
                parsed = json.loads(text.strip())
            except json.JSONDecodeError as je:
                self._q.put(("roles_error",
                    f"Claude returned unexpected format.\n"
                    f"Please try again.\n\nDetails: {je}"))
                return
            # Handle both formats: [{role,match}] or ["string"]
            roles_with_scores = []
            for item in parsed:
                if isinstance(item, dict):
                    roles_with_scores.append({
                        "role":   str(item.get("role", item.get("title",""))).strip(),
                        "match":  int(item.get("match", item.get("score", 50))),
                        "reason": str(item.get("reason","")).strip()
                    })
                elif isinstance(item, str) and item.strip():
                    roles_with_scores.append({"role": item.strip(), "match": 50, "reason": ""})
            if not roles_with_scores:
                self._q.put(("roles_error",
                    "Claude couldn't suggest roles from your profile.\n"
                    "Make sure your resume_data.xml has experience and skills filled in."))
                return
            self._q.put(("roles_ready", roles_with_scores))
        except Exception as e:
            self._q.put(("roles_error", str(e)))

    def _s4(self, f):
        f.grid_rowconfigure(1, weight=1)
        f.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(6, 10))
        ctk.CTkLabel(hdr, text="Choose roles to search", font=F("title"),
                     text_color=FG).pack(anchor="w")
        ctk.CTkLabel(hdr, text="Suggested from your resume profile. Pick one or more.",
                     font=F("small"), text_color=FG_DIM).pack(anchor="w")

        c = self._card(f)
        c.grid(row=1, column=0, sticky="nsew", pady=(0,4))
        c.grid_columnconfigure(0, weight=1)
        c.grid_rowconfigure(1, weight=1)

        ctrl = ctk.CTkFrame(c, fg_color="transparent")
        ctrl.grid(row=0, column=0, sticky="ew", padx=16, pady=(10,4))
        ctk.CTkButton(ctrl, text="Select all", width=90, height=28,
                      font=F("small"), fg_color=BG_FIELD, hover_color=BG_HOVER,
                      text_color=ACCENT, command=self._roles_all).pack(side="left")
        ctk.CTkButton(ctrl, text="Clear all", width=80, height=28,
                      font=F("small"), fg_color=BG_FIELD, hover_color=BG_HOVER,
                      text_color=FG_DIM, command=self._roles_clear).pack(side="left", padx=6)
        self._role_cnt = ctk.CTkLabel(ctrl, text="", font=F("small"), text_color=FG_DIM)
        self._role_cnt.pack(side="right")

        ctk.CTkFrame(c, height=1, fg_color=LINE).grid(row=0, column=0, sticky="sew")

        scroll = ctk.CTkScrollableFrame(c, fg_color="transparent", corner_radius=0)
        scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        scroll.grid_columnconfigure(0, weight=1)
        self._role_scroll = scroll
        self._role_vars   = []

        # Search mode selector
        mode_frame = ctk.CTkFrame(f, fg_color=BG_CARD, corner_radius=14,
                                  border_width=1, border_color=LINE)
        mode_frame.grid(row=2, column=0, sticky="ew", pady=(10,0))
        mode_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(mode_frame, text="Search strategy",
                     font=F("label_b"), text_color=FG
                     ).grid(row=0, column=0, sticky="w", padx=14, pady=(10,4))

        self._search_mode = ctk.StringVar(value="specific")
        modes = [
            ("specific",
             "Exact names",
             "Searches LinkedIn for the exact role names above\n"
             "Best when your profile is highly specialised"),
            ("broad",
             "Broader terms",
             "Converts roles to general titles (Software Engineer, Developer)\n"
             "More results — better when exact names return few jobs"),
            ("both",
             "Both",
             "Runs exact names first, then broad terms\n"
             "Maximum coverage — takes longer"),
            ("location",
             "Location only",
             "No keyword — returns all jobs in your location\n"
             "Bot queues only jobs matching your profile via AI filter"),
        ]
        btn_row = ctk.CTkFrame(mode_frame, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", padx=14, pady=(0,10))

        self._mode_info = ctk.StringVar(value="Searches LinkedIn for the exact role names above")
        for val, label, tooltip in modes:
            rb = ctk.CTkRadioButton(
                btn_row, text=label, variable=self._search_mode, value=val,
                font=F("label"), text_color=FG_SOFT,
                command=lambda t=tooltip.split("\n")[0]: self._mode_info.set(t),
            )
            rb.pack(side="left", padx=(0, 18))

        ctk.CTkLabel(mode_frame, textvariable=self._mode_info,
                     font=F("small"), text_color=FG_DIM, anchor="w"
                     ).grid(row=2, column=0, sticky="ew", padx=14, pady=(0,8))

        nav = ctk.CTkFrame(f, fg_color="transparent")
        nav.grid(row=3, column=0, sticky="ew", pady=(8,0))
        ctk.CTkButton(nav, text="← Back", width=100,
                      fg_color=BG_CARD, text_color=FG, hover_color=BG_HOVER,
                      command=lambda: self._show_step(1)).pack(side="left")
        self._start_btn = ctk.CTkButton(nav, text="▶  Start run", height=36,
                                         command=self._start)
        self._start_btn.pack(side="right")

    def _populate_roles(self, roles: list):
        import customtkinter as _ctk2
        for w in self._role_scroll.winfo_children():
            w.destroy()
        self._role_vars   = []
        self._role_scores = {}

        for item in roles:
            if isinstance(item, dict):
                role_name = str(item.get("role", item.get("title", ""))).strip()
                score     = int(item.get("match", 50))
                reason    = str(item.get("reason", "")).strip()
            else:
                role_name = str(item).strip()
                score     = 50
                reason    = ""

            if not role_name:
                continue

            self._role_scores[role_name] = score
            auto_checked = score >= 60
            var = _ctk2.BooleanVar(value=auto_checked)

            if score >= 70:
                score_color, score_bg = ("#15803D", "#88DD88"), ("#E7F6EC", "#1A3A1A")
            elif score >= 50:
                score_color, score_bg = ("#A16207", "#FFCC44"), ("#FFF6DD", "#3A3000")
            else:
                score_color, score_bg = ("#B42318", "#FF8888"), ("#FDECEC", "#3A1A1A")

            row = _ctk2.CTkFrame(self._role_scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)

            cb = _ctk2.CTkCheckBox(row, text=role_name, variable=var,
                                    font=F("label"), text_color=FG_SOFT,
                                    command=self._update_role_cnt)
            cb.pack(side="left", padx=(0,8))

            badge = _ctk2.CTkFrame(row, fg_color=score_bg, corner_radius=4)
            badge.pack(side="right")
            _ctk2.CTkLabel(badge, text="~%d%% match" % score,
                            font=F("small"),
                            text_color=score_color).pack(padx=6, pady=2)

            if reason:
                tip = _ctk2.CTkLabel(self._role_scroll,
                                      text="   " + reason,
                                      font=F("small"),
                                      text_color=FG_DIM,
                                      anchor="w")
                tip.pack(fill="x", pady=(0,2))

            self._role_vars.append((var, role_name))

        self._update_role_cnt()
        self._show_step(3)

    def _roles_auto_select(self):
        """Select only roles with 60%+ estimated match."""
        n = 0
        for var, role in self._role_vars:
            score = self._role_scores.get(role, 50)
            var.set(score >= 60)
            if score >= 60:
                n += 1
        self._update_role_cnt()
        self._set_status("Auto-selected %d role(s) with 60%%+ estimated match" % n)

    def _update_role_cnt(self):
        n = sum(1 for v,_ in self._role_vars if v.get())
        self._role_cnt.configure(text=f"{n} selected",
                                  text_color=ACCENT if n else DANGER)

    def _roles_all(self):
        for v,_ in self._role_vars: v.set(True)
        self._update_role_cnt()

    def _roles_clear(self):
        for v,_ in self._role_vars: v.set(False)
        self._update_role_cnt()

    def _s_running(self, f):
        """Running screen: hero card (what Resuto is doing now, stages,
        progress, this run's totals, Stop) and a live activity timeline.
        Phase 3 prompts replace the timeline while Resuto waits for you."""
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)
        self._p3_card = None

        hero = ctk.CTkFrame(f, fg_color=BG_CARD, corner_radius=16,
                            border_width=1, border_color=LINE)
        hero.grid(row=0, column=0, sticky="ew", pady=(4, 12))
        hero.grid_columnconfigure(0, weight=1)
        self._run_inner = hero
        ctk.CTkFrame(hero, height=3, corner_radius=2, fg_color=ACCENT
                     ).grid(row=0, column=0, columnspan=2, sticky="ew",
                            padx=14, pady=(3, 0))
        self._run_eyebrow = ctk.CTkLabel(hero, text="RUN IN PROGRESS",
                                         font=F("small_b"), text_color=ACCENT_TXT)
        self._run_eyebrow.grid(row=1, column=0, sticky="w", padx=26, pady=(14, 0))
        self._run_phase_lbl = ctk.CTkLabel(hero, text="Bot is running...",
                                           font=F("stat"), text_color=FG,
                                           anchor="w", justify="left")
        self._run_phase_lbl.grid(row=2, column=0, sticky="w", padx=26)
        self._run_sub_lbl = ctk.CTkLabel(hero, text="", font=F("label"),
                                         text_color=FG_DIM, anchor="w",
                                         justify="left", wraplength=620)
        self._run_sub_lbl.grid(row=3, column=0, sticky="w", padx=26, pady=(2, 0))

        # Stop (mirrors the Stop button the wizard uses) + queue button
        self._run_btnrow = ctk.CTkFrame(hero, fg_color="transparent")
        self._run_btnrow.grid(row=1, column=1, rowspan=3, sticky="ne",
                              padx=22, pady=(16, 0))
        self._run_stop_btn = ctk.CTkButton(
            self._run_btnrow, text="■  Stop", width=92, height=34,
            fg_color="transparent", text_color=DANGER, border_width=1,
            border_color=DANGER, hover_color=BG_HOVER, corner_radius=8,
            command=self._stop)
        # Shown on the end screen when jobs are still queued
        self._run_queue_btn = ctk.CTkButton(
            self._run_btnrow, text="Apply to queued jobs", height=34,
            fg_color=ACCENT, hover_color=ACCENT_HV, text_color="#FFFFFF",
            corner_radius=8, command=self._start_apply_queue)

        # Stages + progress
        stages = ctk.CTkFrame(hero, fg_color="transparent")
        stages.grid(row=4, column=0, columnspan=2, sticky="ew", padx=26, pady=(14, 0))
        self._run_stage_lbls = []
        for i, name in enumerate(("Search & match", "Tailor resumes", "Apply")):
            if i:
                ctk.CTkLabel(stages, text="›", font=F("small"),
                             text_color=MUTED).pack(side="left", padx=8)
            l = ctk.CTkLabel(stages, text=name, font=F("small_b"), text_color=MUTED)
            l.pack(side="left")
            self._run_stage_lbls.append(l)
        self._run_totals = ctk.CTkLabel(stages, text="", font=F("small"),
                                        text_color=FG_DIM)
        self._run_totals.pack(side="right")
        self._run_prog = ctk.CTkProgressBar(hero, height=8, corner_radius=4,
                                            progress_color=ACCENT,
                                            fg_color=BG_FIELD, mode="indeterminate")
        self._run_prog.grid(row=5, column=0, columnspan=2, sticky="ew",
                            padx=26, pady=(8, 20))
        self._run_prog_on = False

        # Live activity
        self._run_tl_card = ctk.CTkFrame(f, fg_color=BG_CARD, corner_radius=14,
                                         border_width=1, border_color=LINE)
        self._run_tl_card.grid(row=1, column=0, sticky="nsew")
        ctk.CTkLabel(self._run_tl_card, text="Live activity", font=F("body_b"),
                     text_color=FG).pack(anchor="w", padx=18, pady=(14, 4))
        self._run_tl = ctk.CTkFrame(self._run_tl_card, fg_color="transparent")
        self._run_tl.pack(fill="both", expand=True, padx=18, pady=(0, 12))

        # Attention card (shown when bot asks for input)
        self._attention_card = ctk.CTkFrame(f, fg_color=BG_CARD,
                                             corner_radius=14,
                                             border_color=ACCENT, border_width=2)
        self._attention_hl   = ctk.CTkLabel(self._attention_card, text="",
                                             font=F("body_b"), text_color=FG,
                                             wraplength=500)
        self._attention_hl.pack(padx=20, pady=(16,4))
        self._attention_sub  = ctk.CTkLabel(self._attention_card, text="",
                                             font=F("label"), text_color=FG_DIM)
        self._attention_sub.pack(padx=20, pady=(0,16))

        self._run_tick_n = 0
        self.after(1000, self._run_tick)

    def _run_tick(self):
        """Keep the running screen in step with the run (every second)."""
        try:
            if self._steps[4].winfo_ismapped():
                live = bool(getattr(self, "_live", False))
                # Stop button mirrors the wizard's Stop button
                if self._stop_btn.winfo_manager():
                    if not self._run_stop_btn.winfo_ismapped():
                        self._run_stop_btn.pack(side="left", padx=(8, 0))
                else:
                    self._run_stop_btn.pack_forget()
                # Eyebrow
                done = self._run_phase_lbl.cget("text") in ("Run complete", "Stopped")
                waiting = (getattr(self, "_p3_card", None) is not None
                           and self._p3_card.winfo_ismapped())
                self._run_eyebrow.configure(
                    text=("WAITING FOR YOU" if waiting and live else
                          "RUN IN PROGRESS" if live else
                          "RUN COMPLETE" if done else "RUN"))
                # Stages
                ph = int(getattr(self, "_current_phase", 1) or 1)
                for i, l in enumerate(self._run_stage_lbls):
                    n = i + 1
                    name = ("Search & match", "Tailor resumes", "Apply")[i]
                    if done or n < ph:
                        l.configure(text="✓ " + name, text_color=SUCCESS)
                    elif n == ph and live:
                        l.configure(text=name, text_color=ACCENT_TXT)
                    else:
                        l.configure(text=name, text_color=MUTED)
                # Progress bar animates while live
                if live and not self._run_prog_on:
                    self._run_prog.configure(mode="indeterminate")
                    self._run_prog.start()
                    self._run_prog_on = True
                elif not live and self._run_prog_on:
                    self._run_prog.stop()
                    self._run_prog.configure(mode="determinate")
                    self._run_prog.set(1 if done else 0)
                    self._run_prog_on = False
                # Totals + timeline every 3 seconds
                self._run_tick_n += 1
                if self._run_tick_n % 3 == 1:
                    self._run_refresh_data()
        except Exception:
            pass
        try:
            self.after(1000, self._run_tick)
        except Exception:
            pass

    def _run_refresh_data(self):
        since = getattr(self, "_bot_start", None)
        try:
            from frontend.app import _read_stats
            st = _read_stats(since=since) if since else {}
            recent = (_read_stats() or {}).get("jobs") or []
        except Exception:
            st, recent = {}, []
        c = (st or {}).get("counts", {}) or {}
        self._run_totals.configure(
            text="%d applied  ·  %d skipped  ·  %d failed  ·  %d queued" % (
                int(c.get("applied", 0) or 0), int(c.get("skipped", 0) or 0),
                int(c.get("failed", 0) or 0), int((st or {}).get("queued", 0) or 0)))
        if since:
            recent = [j for j in recent if str(j.get("logged_at", "")) >= since]
        self._fill_timeline(self._run_tl, recent[:5],
                            "Waiting for the first job… Resuto is searching LinkedIn.")

    # ── Errors tab ────────────────────────────────────────────────
    def _build_errors(self):
        f = self._tabs["errors"]
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=24, pady=(6, 8))
        ctk.CTkLabel(top, text="Errors and warnings",
                     font=F("body_b"), text_color=FG).pack(side="left")
        ctk.CTkLabel(top, text="from the current session",
                     font=F("small"), text_color=MUTED).pack(side="left", padx=(8, 0))

        # Open log file button
        def _open_log():
            """Show last 200 lines of bot.log in an in-app popup window."""
            try:
                from core.logger import _get_log_file
                import os
                log_path = _get_log_file()

                # Create file if it doesn't exist yet
                if not os.path.exists(log_path):
                    os.makedirs(os.path.dirname(log_path), exist_ok=True)
                    open(log_path, "a").close()

                # Read last 200 lines
                try:
                    with open(log_path, encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    text = "".join(lines[-200:]) if lines else "(log is empty)"
                except Exception:
                    text = f"Log file: {log_path}\n(Could not read contents)"

                # Show in a simple in-app popup — no external app needed
                win = ctk.CTkToplevel(self)
                win.title("Resuto — Log")
                win.geometry("900x600")
                win.grab_set()

                # Header
                ctk.CTkLabel(win, text=f"Log: {log_path}",
                             font=F("small"), text_color=FG_DIM
                             ).pack(anchor="w", padx=12, pady=(10,0))

                # Scrollable text
                txt = ctk.CTkTextbox(win, font=ctk.CTkFont(family="Consolas", size=11),
                                     fg_color=BG_FIELD, text_color=FG,
                                     wrap="none")
                txt.pack(fill="both", expand=True, padx=12, pady=8)
                txt.insert("end", text)
                txt.configure(state="disabled")
                txt.see("end")

                # Close button
                ctk.CTkButton(win, text="Close", width=100,
                              command=win.destroy).pack(pady=(0,10))

            except Exception as e:
                self._append_error(f"Could not open log: {e}")

        def _show_log_path():
            try:
                from core.logger import _get_log_file
                messagebox.showinfo("Log File Location", _get_log_file())
            except Exception:
                messagebox.showinfo("Log File", "Log file not available")

        ctk.CTkButton(top, text="Open log", width=96,
                      height=32, font=F("small_b"), corner_radius=8,
                      fg_color="transparent", text_color=FG, hover_color=BG_HOVER,
                      border_width=1, border_color=LINE,
                      command=_open_log).pack(side="right", padx=(8,0))
        ctk.CTkButton(top, text="Log location", width=104,
                      height=32, font=F("small_b"), corner_radius=8,
                      fg_color="transparent", text_color=FG, hover_color=BG_HOVER,
                      border_width=1, border_color=LINE,
                      command=_show_log_path).pack(side="right", padx=(8,0))
        self._err_cnt_lbl = ctk.CTkLabel(top, text="0 issues",
                                          font=F("small"), text_color=FG_DIM)
        self._err_cnt_lbl.pack(side="right", padx=(0,8))

        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)
        self._err_box = ctk.CTkTextbox(f, font=F("mono"),
                                        fg_color=BG_CARD, corner_radius=12,
                                        border_width=1, border_color=LINE,
                                        text_color=FG_SOFT, state="disabled",
                                        wrap="word")
        self._err_box.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0,16))
        self._apply_err_tags()

    def _apply_err_tags(self):
        """Error-log colours for the current theme (Text tags need plain hex)."""
        try:
            self._err_box.tag_config("err",  foreground=C(("#B42318", "#FF6B6B")))
            self._err_box.tag_config("warn", foreground=C(("#A16207", "#FFD166")))
            self._err_box.tag_config("ts",   foreground=C(MUTED))
        except Exception:
            pass

    # ── Stats tab ─────────────────────────────────────────────────
    # Stats methods → views/stats_view.py


    # History methods → views/history_view.py

    # ── Run controls ───────────────────────────────────────────────
    def _start(self):
        selected = [r for v,r in self._role_vars if v.get()]

        # Expand roles based on search mode
        search_mode = getattr(self, "_search_mode", None)
        mode = search_mode.get() if search_mode else "specific"

        if mode == "location":
            # No keyword search — use empty string so scraper searches all jobs in location
            # The AI relevance filter queues only matching ones
            selected = [""]   # empty keyword = all jobs in location
            self._set_status("Location-only mode — AI will filter jobs matching your profile")

        elif mode in ("broad", "both"):
            broad_roles = self._claude_broad_terms(selected)
            if mode == "broad":
                selected = broad_roles if broad_roles else selected
            else:
                for b in broad_roles:
                    if b not in selected:
                        selected.append(b)

        if mode != "specific":
            self._set_status("Search mode: %s — %d role(s) to search" % (mode, len(selected)))
        if not selected:
            messagebox.showwarning("No Roles","Select at least one role."); return

        # Show the estimated Claude cost before anything starts
        if not self._confirm_run_cost(len(selected)):
            self._set_status("Run cancelled.")
            return

        # ── Stop any previous run that is still alive ─────────────
        # After a run completes the browser is minimized but the subprocess
        # stays alive waiting on stdin. Starting a new run without closing
        # the old one means two processes try to own the same Chrome profile
        # → Chrome delegates to the running instance → blank tabs + errors.
        if self._runner and self._runner.running():
            self._restarting = True    # suppress _handle_done UI reset
            self._runner.send("stop")
            # Wait for process to exit (poll every 100ms, timeout 2s) then start
            self._wait_and_start(selected, attempts=20)
            return
        self._restarting = False
        self._do_start(selected)

    def _wait_and_start(self, selected: list, attempts: int):
        """Poll until the old runner process has exited, then start fresh."""
        if self._runner is not None and self._runner.running():
            if attempts > 0:
                self.after(100, self._wait_and_start, selected, attempts - 1)
            else:
                # Timeout — hard kill and proceed
                try:
                    self._runner.stop()
                except Exception:
                    pass
                self._restarting = False
                self.after(300, self._do_start, selected)
        else:
            # Old process has exited (or was None) — launch fresh now
            self._restarting = False
            self._do_start(selected)

    def _do_start(self, selected: list):
        """Actually launch the bot subprocess — called after any old runner is stopped."""
        if not selected:
            return

        # Prevent double launch
        if self._live and self._runner and self._runner.running():
            return

        self._clear_errors()
        self._err_count = 0
        self._live = True

        # ── Metric check before launch ────────────────────────────────
        # Check profile for missing metrics BEFORE bot starts
        # so user can provide real numbers for factual scaffolding
        try:
            from api.metric_guard import scan_profile_for_missing_metrics
            from core.profile import load_profile_from_xml
            from core.settings import get_resume_data_path as _grp5
            _xml = _grp5()
            if _xml:
                _prof = load_profile_from_xml(_xml)
                _miss = scan_profile_for_missing_metrics(_prof)
                if _miss:
                    # Show metric collection popup — bot launch waits
                    self._show_metric_collection_popup(
                        _miss,
                        on_continue=lambda: self._launch_bot(selected),
                        on_skip=lambda: self._launch_bot(selected))
                    return  # don't launch yet — popup will call _launch_bot
        except Exception:
            pass  # if check fails, launch normally

        self._launch_bot(selected)

    def _claude_broad_terms(self, roles: list) -> list:
        """
        Ask Claude to generate broader LinkedIn search terms.
        Works for ANY domain - tech, healthcare, finance, legal, etc.
        Falls back to original roles on failure.
        """
        if not roles:
            return roles
        try:
            api_key = self._api_var.get().strip()
        except Exception:
            return roles
        if not api_key:
            return roles
        try:
            import anthropic as _ant, json as _j
            client = _ant.Anthropic(api_key=api_key, timeout=20.0)
            role_list = ", ".join(repr(r) for r in roles)
            prompt = (
                "The candidate wants to search LinkedIn for jobs related to: " + role_list + ".\n"
                "Suggest 2-4 broader search terms that would return more results "
                "while staying in the same career field.\n"
                "Use common LinkedIn job title keywords. No seniority words (no Senior/Junior/Lead).\n"
                "Return ONLY a JSON array of strings. No explanation."
            )
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}]
            )
            text = msg.content[0].text.strip()
            s = text.find("["); e = text.rfind("]") + 1
            if s >= 0 and e > s:
                broad = _j.loads(text[s:e])
                if isinstance(broad, list):
                    return [str(r).strip() for r in broad if r]
        except Exception as _e:
            print("[WARN] Broad terms failed: %s" % _e)
        return roles

    def _launch_bot(self, selected: list):
        """Actually start the bot subprocess after metric check."""

        # Pre-flight: check browser is available
        import shutil, os as _os
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            shutil.which("google-chrome") or "",
            shutil.which("chromium") or "",
        ]
        # Also check Playwright bundled Chromium
        from pathlib import Path as _P
        playwright_chromium = _P.home() / "AppData" / "Local" / "ms-playwright"

        chrome_found = (
            any(_os.path.exists(p) for p in chrome_paths if p)
            or playwright_chromium.exists()
        )

        if not chrome_found:
            self._append_error(
                "No browser found. The bot needs Chrome or Playwright Chromium to run.\n\n"
                "Fix options:\n"
                "  1. Install Google Chrome from https://google.com/chrome\n"
                "  2. Run in Command Prompt: resuto.exe --install-browsers\n"
                "     (downloads Playwright Chromium automatically)"
            )
            self._set_status("Browser not found — see Errors tab")
            return

        self._live_dot.configure(text_color=SUCCESS)
        self._bot_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._stats_last_hash = None
        self._current_phase = 0
        self._stop_btn.pack(side="right")
        self._show_step(4)
        self._set_status("Starting...")
        self._set_phase("Starting...")

        # Read job preferences from settings
        prefs = self._load_job_prefs()
        easy_apply = prefs.get("easy_apply_only", True)
        exp_levels = prefs.get("experience_levels", [])
        job_types  = prefs.get("job_types", [])
        workplace  = prefs.get("workplace", [])

        args = []
        loc = self._loc_var.get().strip()
        if loc: args += ["--location", loc]
        mj = self._maxjobs_var.get().strip()
        if mj.isdigit(): args += ["--max-jobs", mj]

        # Easy apply from settings (replaces old --mode flag)
        args += ["--mode", "easy_apply" if easy_apply else "all"]
        args += ["--date-posted", prefs.get("date_posted", "any")]

        # Job preference filters
        if exp_levels: args += ["--exp-levels"]  + exp_levels
        if job_types:  args += ["--job-types"]   + job_types
        if workplace:  args += ["--workplace"]   + workplace

        if self._clear_var.get(): args += ["--clear-runs"]
        args += ["--application-mode", prefs.get("application_mode", "continuous")]
        args += ["--roles"] + selected

        # Clear leftover action bar / end-screen button from previous run
        self._hide_action_panel()
        self._run_summary = {}
        try:
            self._run_queue_btn.pack_forget()
        except Exception:
            pass
        # Drain any stale queue messages from previous run
        try:
            while True: self._q.get_nowait()
        except Exception: pass
        # Reset bot_start for fresh session counters
        self._bot_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Reset apply regex flag so previous run signal doesn't retrigger
        self._apply_prompt_shown = False

        self._runner = BotRunner(self._api_var.get().strip(), args,
                                  self._on_bot_line, self._on_bot_done)
        self._runner.start()

    def _stop(self):
        self._user_stopped = True   # flag: user initiated stop, not a crash
        if self._runner:
            self._runner.send("stop")
            self.after(500, self._force_stop)
        self._live = False
        # session_start stays set — Recent keeps showing this session's jobs
        self._live_dot.configure(text_color=MUTED)
        self._stop_btn.pack_forget()
        self._hide_action_panel()
        self._act_strip.grid_forget()
        self._act_strip_visible = False
        self._set_phase("Stopped")
        self._set_status("Stopped by user.")
        self._show_step(3)

    def _force_stop(self):
        """Hard-terminate the subprocess if it hasn't exited yet."""
        if self._runner and self._runner.running():
            self._runner.stop()

    def _clear_errors(self):
        self._err_box.configure(state="normal")
        self._err_box.delete("1.0","end")
        self._err_box.configure(state="disabled")
        self._err_count = 0
        self._err_cnt_lbl.configure(text="0 issues", text_color=FG_DIM)
        try:
            self._update_issue_banner()
        except Exception:
            pass

    # ── Bot output ─────────────────────────────────────────────────
    def _on_bot_line(self, line: str):
        self._q.put(("line", line))

    # Traceback buffering state
    _tb_buffer: list = []
    _tb_active: bool = False

    def _on_bot_done(self, code: int):
        self._q.put(("done", code))

    # ── Phase 3: one card per job (Applied / Skip / Stop) ─────────
    # The bot opens the job page, then prints "BOT_APPLY: {json}".
    # Uses the run tab's _action_bar + attention card (same widgets as the
    # d/s/q prompt in history_view). The answers match what
    # backend.browser.apply_to_job accepts: "applied" / "skip" / "stop".
    # One card for every Phase 3 question, with its buttons INSIDE it.
    def _ensure_p3_card(self):
        """'Your turn' card: a structured job card that takes the timeline's
        place while Resuto waits for a decision."""
        if getattr(self, "_p3_card", None) is not None:
            return
        f = self._steps[4]
        c = ctk.CTkFrame(f, fg_color=BG_CARD, corner_radius=14,
                         border_width=1, border_color=LINE)
        c.grid_columnconfigure(0, weight=1)
        self._p3_card = c
        ctk.CTkFrame(c, height=3, corner_radius=2, fg_color=ACCENT
                     ).grid(row=0, column=0, sticky="ew", padx=14, pady=(3, 0))

        # Header: company initial · role / company / progress · match chip
        head = ctk.CTkFrame(c, fg_color="transparent")
        head.grid(row=1, column=0, sticky="ew", padx=22, pady=(16, 12))
        head.grid_columnconfigure(1, weight=1)
        self._p3_tile = ctk.CTkLabel(head, text="", width=44, height=44,
                                     corner_radius=10, fg_color=BG_FIELD,
                                     font=F("heading"), text_color=FG_SOFT)
        self._p3_tile.grid(row=0, column=0, rowspan=3, sticky="nw", padx=(0, 14))
        self._p3_title = ctk.CTkLabel(head, text="", font=F("heading"),
                                      text_color=FG, anchor="w", justify="left",
                                      wraplength=560)
        self._p3_title.grid(row=0, column=1, sticky="w")
        self._p3_company = ctk.CTkLabel(head, text="", font=F("label"),
                                        text_color=FG_SOFT, anchor="w")
        self._p3_company.grid(row=1, column=1, sticky="w")
        self._p3_meta = ctk.CTkLabel(head, text="", font=F("small"),
                                     text_color=MUTED, anchor="w")
        self._p3_meta.grid(row=2, column=1, sticky="w", pady=(2, 0))
        self._p3_chip = ctk.CTkLabel(head, text="", font=F("small_b"),
                                     text_color=ACCENT_TXT, fg_color=ACCENT_SOFT,
                                     corner_radius=10, height=26)
        self._p3_chip.grid(row=0, column=2, sticky="ne", padx=(12, 0))

        ctk.CTkFrame(c, height=1, fg_color=LINE).grid(row=2, column=0, sticky="ew", padx=22)

        # Body: why it fits / resume, then the instruction line
        body = ctk.CTkFrame(c, fg_color="transparent")
        body.grid(row=3, column=0, sticky="nsew", padx=22, pady=(12, 0))
        body.grid_columnconfigure(0, weight=1)
        self._p3_reason_cap = ctk.CTkLabel(body, text="", font=F("small_b"),
                                           text_color=MUTED, anchor="w")
        self._p3_reason_box = ctk.CTkFrame(body, fg_color=BG_FIELD, corner_radius=10)
        self._p3_reason = ctk.CTkLabel(self._p3_reason_box, text="", font=F("small"),
                                       text_color=FG_SOFT, anchor="w", justify="left",
                                       wraplength=640)
        self._p3_reason.pack(fill="x", padx=14, pady=10)
        self._p3_status = ctk.CTkLabel(body, text="", font=F("small"),
                                       text_color=FG_DIM, anchor="w", justify="left",
                                       wraplength=660)
        self._p3_body = body

        # Footer: links (left) · actions (right)
        ctk.CTkFrame(c, height=1, fg_color=LINE).grid(row=4, column=0, sticky="ew",
                                                      padx=22, pady=(12, 0))
        foot = ctk.CTkFrame(c, fg_color="transparent")
        foot.grid(row=5, column=0, sticky="ew", padx=22, pady=12)
        self._p3_links = ctk.CTkFrame(foot, fg_color="transparent")
        self._p3_links.pack(side="left")
        self._p3_btns = ctk.CTkFrame(foot, fg_color="transparent")
        self._p3_btns.pack(side="right")

    def _p3_show(self, title, company="", meta="", reason="", status="",
                 buttons=(), links=(), score=None, reason_label="Why it fits",
                 tile=None, waiting_text="Resuto is waiting for your decision below."):
        """buttons: (text, value, style) — style primary/success/neutral.
        links: (text, callback) — quiet text buttons on the left."""
        self._ensure_p3_card()
        self._hide_action_bar()           # legacy d/s/q bar, if it was up
        self._p3_title.configure(text=title)
        self._p3_company.configure(text=company)
        self._p3_meta.configure(text=meta)
        for _w, _t in ((self._p3_company, company), (self._p3_meta, meta)):
            if _t:
                _w.grid()
            else:
                _w.grid_remove()
        initial = tile or ((company or "").strip()[:1].upper() or "•")
        self._p3_tile.configure(text=initial)
        try:
            sc = int(score) if score not in (None, "") else None
        except (TypeError, ValueError):
            sc = None
        if sc:
            self._p3_chip.configure(text="  %d%% match  " % sc)
            self._p3_chip.grid()
        else:
            self._p3_chip.grid_remove()

        for w in (self._p3_reason_cap, self._p3_reason_box, self._p3_status):
            w.pack_forget()
        if reason:
            self._p3_reason_cap.configure(text=reason_label.upper())
            self._p3_reason_cap.pack(fill="x")
            self._p3_reason.configure(text=reason)
            self._p3_reason_box.pack(fill="x", pady=(4, 0))
        if status:
            self._p3_status.configure(text="ⓘ  " + status)
            self._p3_status.pack(fill="x", pady=(10, 0))

        for fr in (self._p3_btns, self._p3_links):
            for w in fr.winfo_children():
                w.destroy()
        # Quiet actions first, the main action last (right-most, indigo)
        ordered = ([b for b in buttons if b[2] == "neutral"] +
                   [b for b in buttons if b[2] != "neutral"])
        for txt, val, st in ordered:
            if st == "neutral":
                kw = dict(fg_color="transparent", hover_color=BG_HOVER,
                          text_color=FG, border_width=1, border_color=LINE)
            else:
                kw = dict(fg_color=ACCENT, hover_color=ACCENT_HV,
                          text_color="#FFFFFF")
            ctk.CTkButton(self._p3_btns, text=txt, height=36, corner_radius=8,
                          width=110 if st == "neutral" else 160,
                          font=F("small_b"),
                          command=lambda v=val: self._answer_apply(v), **kw
                          ).pack(side="left", padx=(8, 0))
        for txt, cb in links:
            ctk.CTkButton(self._p3_links, text=txt, command=cb, height=30, width=0,
                          fg_color="transparent", hover_color=BG_HOVER,
                          text_color=ACCENT_TXT, font=F("small_b")
                          ).pack(side="left", padx=(0, 4))

        self._run_tl_card.grid_remove()
        self._p3_card.grid(row=1, column=0, sticky="new")
        self._step_lbl.configure(text="Waiting for you")
        self._run_sub_lbl.configure(text=waiting_text)
        self._nav(0)

    def _p3_progress(self, data: dict) -> str:
        applied, limit = data.get("applied"), data.get("limit")
        idx, total = int(data.get("index") or 1), int(data.get("total") or 1)
        where = ("Job %d of %d in queue" if data.get("mode", "queue") == "queue"
                 else "Match %d of %d this run") % (idx, total)
        if applied is None:
            return where
        done = ("Applied %d of %d" % (applied, limit)) if limit else ("Applied %d" % applied)
        return "%s   •   %s" % (done, where)

    def _p3_common(self, data: dict, phase_word: str):
        idx, total = int(data.get("index") or 1), int(data.get("total") or 1)
        self._current_phase = 3
        self._set_phase("Phase 3 — %s %d of %d" % (phase_word, idx, total))
        self._run_phase_lbl.configure(text="Your turn")

    def _reopen_links(self, url: str):
        return [("Show job again",
                 lambda: self._runner and self._runner.send("reopen"))] if url else []

    def _show_decide_card(self, data: dict):
        """One job at a time, step 1: decide BEFORE a resume is made."""
        self._p3_common(data, "Job")
        url = str(data.get("url") or "")
        reason = str(data.get("reason") or "").strip()
        if len(reason) > 420:
            reason = reason[:420].rsplit(" ", 1)[0] + "…"
        self._p3_show(
            title=str(data.get("title") or "Unknown role"),
            company=str(data.get("company") or ""),
            meta=self._p3_progress(data),
            score=data.get("score"),
            reason=reason,
            reason_label="Why it fits",
            status=("The job is open in the bot's browser. Want a tailored resume for it?"
                    if data.get("opened") else
                    "Couldn't open the job page automatically — try \"Show job again\"."),
            buttons=[("Tailor resume & apply", "tailor", "primary"),
                     ("Skip", "skip", "neutral"),
                     ("Finish", "finish", "neutral")],
            links=self._reopen_links(url))
        self._set_status("Decide: tailor a resume for %s?" % str(data.get("title"))[:60])

    def _show_apply_card(self, data: dict):
        """Resume is ready: apply in the browser, then Applied / Didn't apply."""
        self._p3_common(data, "Job")
        url    = str(data.get("url") or "")
        resume = str(data.get("resume") or "")
        res_txt = ""
        if resume:
            res_txt = "Resume: %s\\%s" % (os.path.basename(os.path.dirname(resume)),
                                          os.path.basename(resume))
        links = []
        if resume:
            links.append(("Preview resume", lambda p=resume: self._open_file(p)))
            links.append(("Open folder", lambda p=resume: self._open_folder(p)))
        links += self._reopen_links(url)
        self._p3_show(
            title=str(data.get("title") or "Unknown role"),
            company=str(data.get("company") or ""),
            meta=self._p3_progress(data),
            score=data.get("score"),
            reason=res_txt,
            reason_label="Your tailored resume",
            status=("Apply in the bot's browser, then tell Resuto what you did."
                    if data.get("opened") else
                    "Couldn't open the job page automatically — try \"Show job again\"."),
            buttons=[("✓  I applied", "applied", "success"),
                     ("Didn't apply", "skip", "neutral"),
                     ("Finish", "stop", "neutral")],
            links=links)
        self._set_status("Waiting for you: %s" % str(data.get("title"))[:60])

    def _show_limit_card(self, data: dict):
        limit = int(data.get("limit") or 0)
        left  = int(data.get("left") or 0)
        self._run_phase_lbl.configure(text="Limit reached")
        self._p3_show(
            title="You've reached your limit of %d application%s"
                  % (limit, "" if limit == 1 else "s"),
            reason="",
            status="%d more job%s ready. Continue applying?"
                   % (left, " is" if left == 1 else "s are"),
            tile=str(limit) if limit else "•",
            waiting_text="Your application limit for this run is reached.",
            buttons=[("Continue applying", "continue", "success"),
                     ("Finish", "finish", "neutral")])
        self._set_status("Limit reached — continue or finish?")

    def _start_apply_queue(self):
        """Go through queued jobs (resume ready) without scanning LinkedIn."""
        if self._runner and self._runner.running():
            messagebox.showinfo(
                "Bot is running",
                "Click Stop to close the current run first, then try again.")
            return
        if not self._api_var.get().strip():
            messagebox.showerror("No API Key", "Add your Anthropic API key in Settings first.")
            return
        try:
            self._run_queue_btn.pack_forget()
        except Exception:
            pass
        self._clear_errors()
        self._err_count = 0
        self._run_summary = {}
        self._live = True
        self._live_dot.configure(text_color=SUCCESS)
        self._bot_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._stats_last_hash = None
        self._current_phase = 3
        self._stop_btn.pack(side="right")
        self._show_step(4)
        self._run_phase_lbl.configure(text="Bot is running...")
        self._run_sub_lbl.configure(text="Opening the browser for your queued jobs...")
        self._set_phase("Applying to queued jobs")
        self._hide_action_panel()
        self._runner = BotRunner(self._api_var.get().strip(),
                                 ["--apply-queue"],
                                 self._on_bot_line, self._on_bot_done)
        self._runner.start()
        self._nav(0)

    def _answer_apply(self, val: str):
        if self._runner:
            self._runner.send(val)
        if val == "tailor":
            # Keep the card; the resume takes a little while
            for fr in (self._p3_btns, self._p3_links):
                for w in fr.winfo_children():
                    w.destroy()
            self._p3_status.configure(text="Tailoring your resume for this job…  (about 30–60 s)")
            self._set_status("Tailoring resume...")
            return
        self._hide_action_panel()
        self._run_phase_lbl.configure(text="Bot is running...")
        self._step_lbl.configure(text="Running...")
        self._set_status({"applied":  "Marked as applied — next job...",
                          "skip":     "Skipped — next job...",
                          "stop":     "Finishing the run...",
                          "continue": "Continuing with the queued jobs...",
                          "finish":   "Finishing the run..."}.get(val, ""))

    def _open_file(self, path: str):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Open resume", "Could not open:\n%s\n\n%s" % (path, e))

    def _open_folder(self, path: str):
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror("Open folder", "Could not open:\n%s\n\n%s" % (folder, e))

    def _hide_action_panel(self):
        """Hide the Phase 3 card (and the legacy d/s/q bar)."""
        try:
            self._hide_action_bar()
        except Exception:
            pass
        if getattr(self, "_p3_card", None) is not None:
            self._p3_card.grid_remove()
        try:
            self._run_tl_card.grid()
        except Exception:
            pass

    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "line":           self._handle_line(data)
                elif kind == "done":         self._handle_done(data)
                elif kind == "s3_status":    self._s3_status.configure(text=data)
                elif kind == "roles_ready":  self._populate_roles(data)
                elif kind == "roles_error":
                    # Step 2 (loading screen) is about to be hidden
                    # Show error in status bar AND popup so user sees it
                    self._show_step(1)   # go back to setup screen
                    self._set_status(f"Error: {data}")
                    try:
                        import tkinter.messagebox as _mb
                        _mb.showerror("Profile Analysis Failed", data)
                    except Exception:
                        pass
                elif kind == "stats_data":   self._apply_stats_data(data)
                elif kind == "history_data": self._apply_history_data(data)
                elif kind == "push_latest":   self._do_push_latest(data)
                elif kind == "key_ok":
                    self._key_status_lbl.configure(
                        text="✓  API key is valid", text_color=SUCCESS)
                    if self._api_save_pref.get():
                        _save_api_key(self._api_var.get().strip())
                    try: self._refresh_start_status()
                    except Exception: pass
                elif kind == "update_found":    self._offer_update(data)
                elif kind == "update_progress":
                    self._set_status("Downloading update... %d%%" % data)
                elif kind == "update_done":     self._on_update_done(data)
                elif kind == "key_fail":
                    self._key_status_lbl.configure(
                        text=f"✕  {data}", text_color=DANGER)
                    try: self._refresh_start_status()
                    except Exception: pass
        except queue.Empty:
            pass
        finally:
            self.after(50 if self._live else 120, self._poll)

    def _handle_line(self, line: str):
        s = line.strip()

        # ── Limit reached with jobs still queued: continue or finish ──
        if s.startswith("BOT_LIMIT:"):
            try:
                self._show_limit_card(json.loads(s[len("BOT_LIMIT:"):].strip()))
            except Exception as e:
                self._append_error("Could not show the limit prompt: %s" % e)
            return
        # ── End-of-run numbers for the summary screen ──────────────
        if s.startswith("BOT_SUMMARY:"):
            try:
                self._run_summary = json.loads(s[len("BOT_SUMMARY:"):].strip())
            except Exception:
                self._run_summary = {}
            return

        # ── One job at a time: decide before a resume is made ──────
        if s.startswith("BOT_DECIDE:"):
            try:
                self._show_decide_card(json.loads(s[len("BOT_DECIDE:"):].strip()))
            except Exception as e:
                self._append_error("Could not show the job card: %s" % e)
            return

        # ── Phase 3: bot opened a job and waits for Applied/Skip/Stop ──
        if s.startswith("BOT_APPLY:"):
            try:
                self._show_apply_card(json.loads(s[len("BOT_APPLY:"):].strip()))
            except Exception as e:
                self._append_error("Could not show the apply card: %s" % e)
            return

        # ── Real-time activity updates from every pipeline stage ──
        # Stage 0: Browser / LinkedIn
        if "Browser launched" in s:
            self._set_phase("Opening Chrome...")
            self._run_sub_lbl.configure(text="Launching browser...")
        elif "LinkedIn: logged in" in s or "already logged in" in s:
            self._set_phase("LinkedIn connected")
            self._run_sub_lbl.configure(text="Logged in to LinkedIn")

        # Stage 1: Job found (printed as "   * title @ company")
        elif ("   * " in line or s.startswith("*")) and "@" in s:
            job = s.lstrip("* ").strip()
            self._set_phase("Job found: %s" % job[:60])
            self._run_sub_lbl.configure(text="Analyzing: %s" % job[:60])

        # Stage 2: JD extraction
        elif "Stage 2:" in s or "JD metadata" in s.lower():
            self._set_phase("Extracting job requirements...")

        # Stage 3: Bullet budget
        elif "Stage 3:" in s or "Bullet budget" in s:
            self._set_phase("Calculating resume budget...")

        # Already seen — update activity so GUI doesn't freeze
        elif s.startswith("<-") and "Already seen" in s:
            job = s.replace("<-", "").replace("Already seen:", "").strip()
            self._set_phase("Already seen: %s" % job[:55])

        # Stage 4a: Relevance
        elif "Checking relevance:" in s:
            job = s.split("Checking relevance:")[-1].strip().rstrip("...")
            self._set_phase("Checking fit: %s" % job[:55])
            self._run_sub_lbl.configure(text="Asking Claude about: %s" % job[:55])
        elif "Relevance done:" in s:
            score = s.split("score=")[-1].split()[0] if "score=" in s else ""
            relevant = "relevant=True" in s
            icon = "Matched" if relevant else "Skipped"
            self._set_phase("%s %s%%" % (icon, score) if score else icon)

        # Stage 4b: Resume generation
        elif any(p in s for p in (
                "Phase 2: generating", "Phase 2 -- Generating",
                "Phase 2: Generating", "Generating tailored resume",
                "Phase 2 --")):
            self._set_phase("Generating tailored resume...")
        elif any(p in s for p in (
                "Resume saved:", "Resume generated:",
                "Step 3 complete", "DOCX written successfully")):
            self._set_phase("✅ Resume ready")
        elif "Phase 2 complete" in s:
            # Only the real end-of-Phase-2 line — "resumes" used to match
            # "Generating resumes..." and showed this far too early
            self._set_phase("✅ Resumes ready")

        # Timeouts / warnings
        elif "Relevance check timed out" in s:
            self._set_phase("Timed out — skipping job")

        for pat, label in _PHASE_MAP:
            if pat.search(line):
                self._set_phase(label)
                self._run_phase_lbl.configure(text="Bot is running...")
                self._run_sub_lbl.configure(text=label)
                if "Phase 3" in label or "Applied" in label or "Skipped" in label or "Waiting" in label:
                    self._current_phase = 3
                elif "Phase 2" in label:
                    self._current_phase = 2
                elif "Phase 1" in label:
                    self._current_phase = 1
                break
        self._parse_activity(s)

        # Incremental Recent update — 4 trigger points matching the pipeline:
        # 1. Job found    → scanning row written to DB immediately
        # 2. Relevance done → row updated to matched/skipped
        # 3. Resume ready → row updated to resume_ready
        # 4. Applied      → row updated to applied
        # Check ORIGINAL line (not stripped) for whitespace-prefixed signals
        # Check stripped s for signals that don't have leading whitespace
        _line_triggers = (
            "   * ",          # job found — save_scanning_job() (has leading spaces)
        )
        _s_triggers = (
            "Matched (",
            "[OK] Saved for resume",
            "Relevance done:",
            "Phase 2 complete",
            "[OK] Marked as applied",
            "[SKIP]  Marked as skipped",
            "Resume saved:",
            "Saved -- will generate",
        )
        if any(sig in line for sig in _line_triggers) or            any(sig in s for sig in _s_triggers):
            self._h_push_latest()          # update History tab
            self._stats_last_hash = None   # force cache invalidation
            # Small delay to ensure orchestrator DB write is committed
            # before GUI queries it (print fires after commit, but be safe)
            self.after(150, self._force_stats_refresh)

        if _LAST_JOB_RE.search(line):
            self._is_last_job = True
            return
        if _IDLE_RE.search(line):
            # Run finished but browser is still open (minimized).
            # Refresh stats and show idle state — don't mark run as done yet.
            self._stats_last_hash = None
            self._refresh_stats()
            summ   = getattr(self, "_run_summary", {}) or {}
            queued = int(summ.get("queued") or 0)
            result = "Applied %d  •  Skipped %d  •  %d still queued" % (
                int(summ.get("applied") or 0), int(summ.get("skipped") or 0), queued)
            self._hide_action_panel()
            self._run_phase_lbl.configure(text="Run complete")
            self._run_sub_lbl.configure(
                text=result + "\n\nThe browser is still open — click Stop to close it.")
            self._step_lbl.configure(text="Finished")
            self._set_phase("Run complete")
            try:
                self._act_action.configure(text="Run complete — " + result)
            except Exception:
                pass
            if queued > 0:
                self._run_queue_btn.configure(text="Apply to queued jobs (%d)" % queued)
                self._run_queue_btn.pack(side="left", padx=(8, 0))
            self._live = False
            self._live_dot.configure(text_color=MUTED)
            # Keep Stop button visible — clicking it closes the browser
            self._nav(2)   # switch to stats tab to show results
            return

        if _DSQ_RE.search(line):
            self._show_action_bar("dsq")
        elif _NF_RE.search(line):
            self._show_action_bar("nf")
        elif _APPLY_RE.search(line):
            # Guard: only show once per run, ignore replayed signal
            if not getattr(self, "_apply_prompt_shown", False):
                self._apply_prompt_shown = True
                self._show_action_bar("yn_apply")

        # Buffer multi-line tracebacks so full error is shown
        if s.startswith("Traceback (most recent call last):"):
            self._tb_buffer = [s]
            self._tb_active = True
        elif self._tb_active:
            if not s:
                # Blank line ends the traceback
                self._flush_traceback()
            else:
                self._tb_buffer.append(s)
                # The final "SomeError: message" line is NOT indented;
                # "File ..." and source lines are.
                if (not line.startswith((" ", "\t"))
                        and re.match(r"[A-Za-z_][\w.]*(Error|Exception|Interrupt|Exit)\b", s)):
                    self._flush_traceback()
        elif _ERROR_RE.search(line) and not s.startswith("[WARN]"):
            self._append_error(line)

    def _flush_traceback(self):
        if self._tb_buffer:
            self._append_error("\n".join(self._tb_buffer))
        self._tb_buffer = []
        self._tb_active = False

    def _parse_activity(self, s: str):
        """Update the live activity strip. Phase-aware — never shows
        Phase 1 labels once Phase 3 has started."""
        phase = getattr(self, "_current_phase", 1)
        role = action = None

        if phase <= 1:
            # Phase 1 patterns
            m = re.search(r"Scanning.*filtering.*['\"](.+?)['\"]", s, re.I)
            if m:
                role, action = m.group(1), "Phase 1 — Scanning LinkedIn"
            elif re.match(r"\*\s+.+\s+@\s+.+", s):
                role, action = s.lstrip("* ").strip(), "Checking relevance..."
            elif re.match(r"\|\s+.+\s+@\s+.+", s):
                role, action = s.lstrip("| ").strip(), "Analysing match..."

        if phase <= 2:
            # Phase 2 patterns
            m2 = re.match(r"\[(\d+)/(\d+)\]\s+(.+@.+)$", s)
            if m2:
                role   = m2.group(3)
                action = f"Generating resume ({m2.group(1)} of {m2.group(2)})"
            elif "[OK] Resume ready" in s:
                action = "Resume generated [OK]"

        if phase == 3:
            # Phase 3 patterns
            m3 = re.match(r"\[(\d+)/(\d+)\]\s+(.+?)\s+@\s+(.+)$", s)
            if m3:
                role   = f"{m3.group(3)} @ {m3.group(4)}"
                action = f"Job {m3.group(1)} of {m3.group(2)}"
            elif "[OK] Marked as applied" in s:
                action = "Applied ✓ — moving to next job"
            elif "[SKIP]" in s and "skipped" in s.lower():
                action = "Skipped — moving to next job"
            elif "YOUR TURN" in s or "job is open" in s.lower():
                action = "Waiting for you to apply..."

        if role or action:
            try:
                if role:   self._act_role.configure(text=role[:80])
                if action: self._act_action.configure(text=action)
                # Mirror progress on the Run screen (it only said "Bot is running...")
                if self._live:
                    self._run_sub_lbl.configure(
                        text=("%s\n%s" % (action or "", role[:70] if role else "")).strip())
                if not self._act_strip_visible:
                    self._act_strip.grid(row=1, column=0, sticky="ew",
                                          padx=20, pady=(0, 4))
                    self._act_strip_visible = True
            except Exception:
                pass

    # _h_push_latest/_bg_push_latest → views/history_view.py


    def _open_review_window(self):
        """Open the review checklist — queued jobs + old applied jobs."""
        try:
            from db.tracker import get_reapply_candidates, get_jobs_ready_to_apply
            from datetime import datetime, timedelta

            # Queued jobs now have their own button ("Apply to queued jobs"),
            # which uses the same one-card-per-job flow as a normal run
            queued = []

            # Previously applied jobs from older sessions
            cutoff = (datetime.now() - timedelta(hours=1)).strftime(
                "%Y-%m-%d %H:%M:%S")
            applied_old = get_reapply_candidates(session_start=cutoff)

            # Tag each so the review window knows how to handle them
            for j in queued:
                j["_review_type"] = "queued"
            for j in applied_old:
                j["_review_type"] = "reapply"

            candidates = queued + applied_old
            if not candidates:
                messagebox.showinfo("Nothing to Review",
                    "No previously-applied jobs to review.")
                return
            key = self._api_var.get().strip()
            ReviewWindow(self, candidates, key,
                         runner=self._runner,
                         on_done=self._on_review_done)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _on_review_done(self):
        self._refresh_review_btn()
        self._refresh_stats()