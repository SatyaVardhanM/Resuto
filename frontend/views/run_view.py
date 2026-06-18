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

import customtkinter as ctk

from frontend.constants import (
    BG, BG_CARD, BG_FIELD, BG_HOVER,
    ACCENT, ACCENT_HV, DANGER, SUCCESS, WARNING, STRETCH, MUTED,
    FG, FG_SOFT, FG_DIM,
    F, _FONT_FAMILY, _FONTS, _BASE_SIZE,
    APP_TITLE, BOT_SCRIPT,
    _settings_file, _load_api_key, _save_api_key, _clear_api_key,
    _init_fonts, _load_font_pref,
)
from frontend.views.dialogs   import ReviewWindow


class RunMixin:
    """Run tab — role selection, bot control, live output."""

    def _build_run(self):
        f = self._tabs["run"]
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)

        # Top bar: step label + stop button
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 0))
        self._step_lbl = ctk.CTkLabel(top, text="", font=F("small"),
                                       text_color=FG_DIM)
        self._step_lbl.pack(side="left")
        self._stop_btn = ctk.CTkButton(top, text="■  Stop", width=80,
                                        fg_color=DANGER, hover_color="#C0392B",
                                        command=self._stop)
        self._stop_btn.pack(side="right")
        self._stop_btn.pack_forget()

        # Step container
        self._step_area = ctk.CTkFrame(f, fg_color="transparent")
        self._step_area.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
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
        for i, s in enumerate(self._steps):
            if i == idx:
                s.place(relx=0, rely=0, relwidth=1, relheight=1)
            else:
                s.place_forget()
        labels = ["",
                  "Search Settings",
                  "Analysing profile...",
                  "Select Roles",
                  "Running..."]
        self._step_lbl.configure(text=labels[min(idx, len(labels)-1)])


    def _s_start(self, f):
        """
        Step 0 — Centered Start screen with animated pulsing button.
        Validates API key and profile before proceeding.
        """
        f.grid_rowconfigure(0, weight=1)
        f.grid_columnconfigure(0, weight=1)

        center = ctk.CTkFrame(f, fg_color="transparent")
        center.place(relx=0.5, rely=0.45, anchor="center")

        # App icon / greeting
        ctk.CTkLabel(center, text="⚡", font=(_FONT_FAMILY, 48),
                     text_color=ACCENT).pack(pady=(0, 8))
        ctk.CTkLabel(center, text="Resuto",
                     font=F("heading"), text_color=FG).pack()
        ctk.CTkLabel(center, text="Find and apply to matching jobs automatically.",
                     font=F("small"), text_color=FG_DIM).pack(pady=(4, 32))

        # Animated Start button
        self._start_btn_outer = ctk.CTkFrame(center, fg_color="transparent")
        self._start_btn_outer.pack()
        self._start_main_btn = ctk.CTkButton(
            self._start_btn_outer, text="▶  Start Run",
            width=220, height=56, font=F("body_b"),
            fg_color=ACCENT, hover_color=ACCENT_HV,
            corner_radius=28,
            command=self._start_validate)
        self._start_main_btn.pack()

        # Start the pulse animation
        self._pulse_active = False
        self._start_pulse()

        # Error / status message below button
        self._start_err = ctk.CTkLabel(center, text="",
                                        font=F("small"), text_color=DANGER,
                                        wraplength=420, justify="center")
        self._start_err.pack(pady=(16, 0))

        # Quick status row: profile + API key
        status_row = ctk.CTkFrame(center, fg_color="transparent")
        status_row.pack(pady=(20, 0))
        self._start_profile_dot = ctk.CTkLabel(
            status_row, text="", font=F("small"), text_color=MUTED)
        self._start_profile_dot.pack()
        self._start_key_dot = ctk.CTkLabel(
            status_row, text="", font=F("small"), text_color=MUTED)
        self._start_key_dot.pack()
        self._refresh_start_status()

    def _refresh_start_status(self):
        """Update profile and API key status dots on the start screen."""
        has_xml = Path(self._xml_path()).exists()
        has_key = bool(self._api_var.get().strip())
        self._start_profile_dot.configure(
            text=("✓  Resume profile loaded" if has_xml
                  else "✕  No profile — add in Settings"),
            text_color=SUCCESS if has_xml else DANGER)
        self._start_key_dot.configure(
            text=("✓  API key ready" if has_key
                  else "✕  No API key — add in Settings"),
            text_color=SUCCESS if has_key else DANGER)

    def _start_pulse(self):
        """Pulse the start button border to draw attention."""
        if not hasattr(self, "_pulse_state"):
            self._pulse_state = 0
        colours = [ACCENT, ACCENT_HV, "#7B89F8", ACCENT_HV, ACCENT]
        idx = self._pulse_state % len(colours)
        try:
            self._start_main_btn.configure(fg_color=colours[idx])
        except Exception:
            return
        self._pulse_state += 1
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
        self._show_step(1)


    def _s2(self, f):
        ctk.CTkLabel(f, text="Search Settings", font=F("heading"),
                     text_color=FG).pack(anchor="w", pady=(30,8))
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

        ctk.CTkFrame(c, height=1, fg_color=BG_HOVER).pack(fill="x", padx=16)
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
                      fg_color=BG_CARD, hover_color=BG_HOVER,
                      command=lambda: self._show_step(0)).pack(side="left")
        ctk.CTkButton(nav, text="Analyse my profile",
                      command=self._s2_next).pack(side="right")

    def _s2_next(self):
        self._show_step(2)
        threading.Thread(target=self._fetch_roles, daemon=True).start()

    def _s3(self, f):
        f.grid_rowconfigure(0, weight=1)
        inner = ctk.CTkFrame(f, fg_color="transparent")
        inner.place(relx=0.5, rely=0.4, anchor="center")
        ctk.CTkLabel(inner, text="◉  Analysing your profile",
                     font=F("heading"), text_color=FG).pack(pady=(0,12))
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

        ctk.CTkLabel(f, text="Select Roles", font=F("heading"),
                     text_color=FG).grid(row=0, column=0, sticky="w", pady=(20,4))

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

        ctk.CTkFrame(c, height=1, fg_color=BG_HOVER).grid(row=0, column=0, sticky="sew")

        scroll = ctk.CTkScrollableFrame(c, fg_color="transparent", corner_radius=0)
        scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        scroll.grid_columnconfigure(0, weight=1)
        self._role_scroll = scroll
        self._role_vars   = []

        # Search mode selector
        mode_frame = ctk.CTkFrame(f, fg_color=BG_CARD, corner_radius=8)
        mode_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(8,0))
        mode_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(mode_frame, text="Search strategy:",
                     font=F("label_b"), text_color=FG
                     ).grid(row=0, column=0, sticky="w", padx=14, pady=(10,4))

        self._search_mode = ctk.StringVar(value="specific")
        modes = [
            ("specific",
             "🎯  Exact names",
             "Searches LinkedIn for the exact role names above\n"
             "Best when your profile is highly specialised"),
            ("broad",
             "🔍  Broader terms",
             "Converts roles to general titles (Software Engineer, Developer)\n"
             "More results — better when exact names return few jobs"),
            ("both",
             "⚡  Both",
             "Runs exact names first, then broad terms\n"
             "Maximum coverage — takes longer"),
            ("location",
             "📍  Location only",
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
                      fg_color=BG_CARD, hover_color=BG_HOVER,
                      command=lambda: self._show_step(1)).pack(side="left")
        self._start_btn = ctk.CTkButton(nav, text="▶  Start Run",
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
                score_color, score_bg = "#88DD88", "#1A3A1A"
            elif score >= 50:
                score_color, score_bg = "#FFCC44", "#3A3000"
            else:
                score_color, score_bg = "#FF8888", "#3A1A1A"

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
        f.grid_rowconfigure(0, weight=1)
        inner = ctk.CTkFrame(f, fg_color="transparent")
        inner.place(relx=0.5, rely=0.35, anchor="center")
        self._run_phase_lbl = ctk.CTkLabel(inner, text="Bot is running...",
                                            font=F("heading"), text_color=FG)
        self._run_phase_lbl.pack(pady=(0,8))
        self._run_sub_lbl = ctk.CTkLabel(inner, text="",
                                          font=F("label"), text_color=FG_DIM)
        self._run_sub_lbl.pack()

        # Attention card (shown when bot asks for input)
        self._attention_card = ctk.CTkFrame(f, fg_color=BG_CARD,
                                             corner_radius=10,
                                             border_color=ACCENT, border_width=2)
        self._attention_hl   = ctk.CTkLabel(self._attention_card, text="",
                                             font=F("body_b"), text_color=FG,
                                             wraplength=500)
        self._attention_hl.pack(padx=20, pady=(16,4))
        self._attention_sub  = ctk.CTkLabel(self._attention_card, text="",
                                             font=F("label"), text_color=FG_DIM)
        self._attention_sub.pack(padx=20, pady=(0,16))

    # ── Errors tab ────────────────────────────────────────────────
    def _build_errors(self):
        f = self._tabs["errors"]
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(14,6))
        ctk.CTkLabel(top, text="Errors and Warnings",
                     font=F("body_b"), text_color=FG).pack(side="left")

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
                win.title("Bot Log")
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

        ctk.CTkButton(top, text="📂 Open Log", width=100,
                      height=28, font=F("small"),
                      fg_color=BG_CARD, hover_color=BG_HOVER,
                      command=_open_log).pack(side="right", padx=(4,0))
        ctk.CTkButton(top, text="📍 Log Path", width=90,
                      height=28, font=F("small"),
                      fg_color=BG_CARD, hover_color=BG_HOVER,
                      command=_show_log_path).pack(side="right", padx=(4,0))
        self._err_cnt_lbl = ctk.CTkLabel(top, text="0 issues",
                                          font=F("small"), text_color=FG_DIM)
        self._err_cnt_lbl.pack(side="right", padx=(0,8))

        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)
        self._err_box = ctk.CTkTextbox(f, font=F("mono"),
                                        fg_color=BG_CARD, corner_radius=8,
                                        text_color=FG_SOFT, state="disabled",
                                        wrap="word")
        self._err_box.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0,16))
        self._err_box.tag_config("err",  foreground="#FF6B6B")
        self._err_box.tag_config("warn", foreground="#FFD166")
        self._err_box.tag_config("ts",   foreground=MUTED)

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
        args += ["--roles"] + selected

        # Clear leftover action bar from previous run
        self._hide_action_bar()
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
        self._hide_action_bar()
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

    # ── Bot output ─────────────────────────────────────────────────
    def _on_bot_line(self, line: str):
        self._q.put(("line", line))

    # Traceback buffering state
    _tb_buffer: list = []
    _tb_active: bool = False

    def _on_bot_done(self, code: int):
        self._q.put(("done", code))

    def _show_one_by_one_action(self, line: str):
        """
        Parse BOT_WAITING line and show Applied/Skip action panel.
        Line format: "BOT_WAITING: title | company | url | score"
        """
        try:
            payload = line.replace("BOT_WAITING:", "").strip()
            parts   = [p.strip() for p in payload.split("|")]
            title   = parts[0] if len(parts) > 0 else "Unknown"
            company = parts[1] if len(parts) > 1 else "Unknown"
            url     = parts[2] if len(parts) > 2 else ""
            score   = parts[3] if len(parts) > 3 else "?"
        except Exception:
            title = line; company = ""; url = ""; score = "?"

        # Show in action panel (runs on main thread via after())
        self.after(0, lambda: self._show_action_panel(title, company, url, score))

    def _show_action_panel(self, title: str, company: str, url: str, score: str):
        """Show the Applied/Skip panel while bot waits for user."""
        # Remove existing panel if any
        self._hide_action_panel()

        # Create panel above the log area
        panel = ctk.CTkFrame(self._run_frame, fg_color=BG_CARD,
                              corner_radius=10, border_width=1,
                              border_color=ACCENT)
        panel.pack(fill="x", padx=16, pady=(0, 8), before=self._run_log)
        self._action_panel = panel

        # Job info
        ctk.CTkLabel(panel,
            text="Waiting for your action",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT).pack(anchor="w", padx=12, pady=(10,2))

        ctk.CTkLabel(panel,
            text=f"{title}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=FG, wraplength=500).pack(anchor="w", padx=12)

        ctk.CTkLabel(panel,
            text=f"{company}   |   Match: {score}%",
            font=ctk.CTkFont(size=11),
            text_color=FG_DIM).pack(anchor="w", padx=12, pady=(0,8))

        # Buttons
        btn_row = ctk.CTkFrame(panel, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0,10))

        ctk.CTkButton(btn_row,
            text="✓  Applied",
            width=130,
            fg_color=SUCCESS,
            hover_color="#1a9e4a",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda: self._send_bot_action("APPLIED")
        ).pack(side="left", padx=(0,8))

        ctk.CTkButton(btn_row,
            text="✗  Skip",
            width=130,
            fg_color=BG_FIELD,
            hover_color=BG_HOVER,
            font=ctk.CTkFont(size=12),
            command=lambda: self._send_bot_action("SKIP")
        ).pack(side="left")

        if url:
            ctk.CTkButton(btn_row,
                text="Open Job",
                width=100,
                fg_color="transparent",
                text_color=ACCENT,
                hover_color=BG_HOVER,
                font=ctk.CTkFont(size=11),
                command=lambda: __import__("webbrowser").open(url)
            ).pack(side="left", padx=(8,0))

        self._set_status(f"Review: {title} @ {company} — click Applied or Skip")

    def _hide_action_panel(self):
        """Remove the action panel."""
        if hasattr(self, "_action_panel") and self._action_panel:
            try:
                self._action_panel.destroy()
            except Exception:
                pass
            self._action_panel = None

    def _send_bot_action(self, action: str):
        """Send APPLIED or SKIP to orchestrator stdin."""
        self._hide_action_panel()
        if self._runner:
            self._runner.send(action)
        label = "Applied — resume sent" if action == "APPLIED" else "Skipped — finding next job"
        self._set_status(label)
        self._set_phase("Generating next resume...")

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

        # ── One-by-one mode: bot waiting for user action ──────────
        if s.startswith("BOT_WAITING:"):
            self._show_one_by_one_action(s)
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
        elif "Phase 2: generating" in s:
            self._set_phase("Generating tailored resume...")
        elif "Resume saved:" in s:
            self._set_phase("Resume ready")

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
            self._run_phase_lbl.configure(text="Run complete")
            self._run_sub_lbl.configure(text="Browser is open. Click Stop to close it.")
            self._set_phase("Run complete — browser open")
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
            if s.strip():
                self._tb_buffer.append(s)
                # Flush on ErrorType: message line — not on File/indented lines
                import re as _re2
                if (s and not s.startswith(" ") and not s.startswith("\t")
                        and not s.startswith("File ")
                        and _re2.match(r"[A-Za-z][A-Za-z0-9_]*Error.*:", s)):
                    self._append_error("\n".join(self._tb_buffer))
                    self._tb_buffer = []
                    self._tb_active = False
                # Blank line ends traceback
                if self._tb_buffer:
                    self._append_error("\n".join(self._tb_buffer))
                self._tb_buffer = []
                self._tb_active = False
        elif _ERROR_RE.search(line) and not s.startswith("[WARN]"):
            self._append_error(line)

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

            # Queued jobs ready to apply (skipped Phase 3 last time)
            queued = get_jobs_ready_to_apply()

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
                    "No queued or previously-applied jobs to review.")
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