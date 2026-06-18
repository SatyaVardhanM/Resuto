"""
frontend/views/settings_view.py
───────────────────────────────
SettingsView — configuration tab.
Handles: API key, XML path, Chrome profile, job filters,
         date posted, work auth, font size.
"""
import json
import os
import sys
from pathlib import Path

import customtkinter as ctk

from frontend.constants import (
    BG, BG_CARD, BG_FIELD, BG_HOVER,
    ACCENT, ACCENT_HV, DANGER, SUCCESS, WARNING, MUTED,
    FG, FG_SOFT, FG_DIM,
    F, _FONT_FAMILY, _FONTS, _BASE_SIZE,
    _init_fonts, _load_font_pref,
    _settings_file, _load_api_key, _save_api_key, _clear_api_key,
)


class SettingsMixin:
    """Settings tab — save/load all user preferences."""

    def _build_settings(self):
        f = self
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(0, weight=1)

        # Scrollable container — content never gets cut off at any window size
        scroll = ctk.CTkScrollableFrame(f, fg_color="transparent", corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)
        f = scroll   # everything below adds to scroll, not the raw tab

        row = [0]
        def next_row():
            r = row[0]; row[0] += 1; return r

        def section_label(text):
            ctk.CTkLabel(f, text=text, font=F("label_b"), text_color=FG
                         ).grid(row=next_row(), column=0, sticky="w",
                                padx=24, pady=(20, 6))

        def section_card(**kw):
            c = ctk.CTkFrame(f, fg_color=BG_CARD, corner_radius=10, **kw)
            c.grid(row=next_row(), column=0, sticky="ew", padx=24, pady=(0, 4))
            c.grid_columnconfigure(0, weight=1)
            return c
        # ── Title ─────────────────────────────────────────────────
        ctk.CTkLabel(f, text="Settings", font=F("heading"),
                     text_color=FG).grid(row=next_row(), column=0,
                                          sticky="w", padx=24, pady=(24, 4))

        # ══ SECTION 0: API KEY ════════════════════════════════════
        section_label("Claude API Key")
        key_card = section_card()
        key_card.grid_columnconfigure(0, weight=1)

        key_row = ctk.CTkFrame(key_card, fg_color="transparent")
        key_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        key_row.grid_columnconfigure(0, weight=1)

        self._settings_key_entry = ctk.CTkEntry(
            key_row, textvariable=self._api_var,
            show="•", font=F("body"), height=38,
            placeholder_text="sk-ant-api03-...")
        self._settings_key_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(key_row, text="👁", width=38, height=38,
                      fg_color=BG_FIELD, hover_color=BG_HOVER,
                      font=F("body"),
                      command=self._toggle_key_visibility
                      ).grid(row=0, column=1, padx=(6, 0))
        self._key_visible = False

        # Save preference
        save_row = ctk.CTkFrame(key_card, fg_color="transparent")
        save_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 14))
        ctk.CTkSwitch(save_row, text="Remember API key",
                       variable=self._api_save_pref,
                       font=F("small"),
                       command=self._on_api_save_toggle
                       ).pack(side="left")
        ctk.CTkLabel(save_row,
                      text="Saved to local_settings.json on this machine only",
                      font=F("tiny"), text_color=MUTED
                      ).pack(side="left", padx=(12, 0))

        # Validate button
        self._key_status_lbl = ctk.CTkLabel(key_card, text="",
                                             font=F("small"), text_color=MUTED)
        self._key_status_lbl.grid(row=2, column=0, sticky="w", padx=16,
                                   pady=(0, 4))
        ctk.CTkButton(key_card, text="Validate key", height=30, width=110,
                      font=F("small"), fg_color=BG_FIELD, hover_color=BG_HOVER,
                      command=self._validate_api_key
                      ).grid(row=2, column=0, sticky="e", padx=16, pady=(0, 14))

        # Keep status dots fresh on the start screen whenever this tab is opened
        self._api_var.trace_add("write", lambda *_: self._on_api_key_changed())


        # ══ SECTION 0b: WORK AUTHORIZATION ═══════════════════════
        section_label("Work Authorization")
        visa_card = section_card()
        visa_card.grid_columnconfigure(0, weight=1)

        visa_info = ctk.CTkFrame(visa_card, fg_color="transparent")
        visa_info.grid(row=0, column=0, sticky="ew", padx=16, pady=(14,8))
        visa_info.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(visa_info, text="Visa / Work Status",
                     font=F("label_b"), text_color=FG,
                     width=160, anchor="w").grid(row=0, column=0, sticky="w")

        VISA_OPTIONS = [
            "CPT (F-1 student — work authorized)",
            "OPT (F-1 — post-graduation, work authorized)",
            "STEM OPT (F-1 — 24-month extension)",
            "H-1B (employer sponsored)",
            "Green Card (permanent resident)",
            "US Citizen",
            "Other / Not listed",
        ]
        # Mapping: display label → short code stored in settings + XML
        VISA_CODE = {
            "CPT (F-1 student — work authorized)":          "CPT",
            "OPT (F-1 — post-graduation, work authorized)": "OPT",
            "STEM OPT (F-1 — 24-month extension)":          "STEM OPT",
            "H-1B (employer sponsored)":                    "H-1B",
            "Green Card (permanent resident)":              "Green Card",
            "US Citizen":                                   "US Citizen",
            "Other / Not listed":                           "Other",
        }

        saved_visa = _load_settings().get("work_authorization", "")
        # Find display label for saved code
        _initial_visa = next(
            (k for k, v in VISA_CODE.items() if v == saved_visa),
            VISA_OPTIONS[0])
        self._visa_var = ctk.StringVar(value=_initial_visa)

        ctk.CTkOptionMenu(
            visa_info,
            values=VISA_OPTIONS,
            variable=self._visa_var,
            width=360, height=36,
            font=F("body"),
            command=self._on_visa_changed
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))

        # Contextual note that changes with selection
        self._visa_note = ctk.CTkLabel(
            visa_card, font=F("small"), text_color=MUTED,
            anchor="w", justify="left",
            wraplength=520)
        self._visa_note.grid(row=1, column=0, sticky="ew", padx=16, pady=(0,12))
        self._update_visa_note(self._visa_var.get())


        # ══ SECTION 1: RESUME PROFILE ═════════════════════════════
        section_label("Resume Profile")
        resume_card = section_card()

        self._resume_status_var = ctk.StringVar(value=self._resume_status_text())
        ctk.CTkLabel(resume_card, textvariable=self._resume_status_var,
                     font=F("small"), text_color=FG_DIM, anchor="w"
                     ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        btn_row = ctk.CTkFrame(resume_card, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))
        ctk.CTkButton(btn_row, text="📄  Upload Resume PDF",
                      height=36, font=F("small"),
                      command=self._upload_resume
                      ).pack(side="left", padx=(0, 8))
        self._view_profile_btn = ctk.CTkButton(
            btn_row, text="👁  View Profile",
            height=36, font=F("small"),
            fg_color=BG_FIELD, hover_color=BG_HOVER,
            command=self._view_profile)
        # Only show if XML exists
        if Path(self._xml_path()).exists():
            self._view_profile_btn.pack(side="left")

        # Regenerate prompts button — always visible
        ctk.CTkButton(
            btn_row, text="🔄  Regenerate Prompts",
            height=36, font=F("small"),
            fg_color=BG_FIELD, hover_color=BG_HOVER,
            command=self._regenerate_prompts_manual
        ).pack(side="left", padx=(8, 0))

        # ══ SECTION 2: BROWSER PROFILE ════════════════════════════
        section_label("Browser Profile")
        browser_card = section_card()
        browser_card.grid_columnconfigure(0, weight=1)

        # ── Init vars FIRST before any widget references them ─────
        stored_chrome = self._load_chrome_profile_path()
        self._chrome_default_var = ctk.BooleanVar(
            value=(stored_chrome == ""))
        self._chrome_path_var = ctk.StringVar()

        def _default_chrome_path() -> str:
            import sys as _s, os as _o
            if getattr(_s, "frozen", False):
                return _o.path.join(_o.path.dirname(_s.executable),
                                    "BotChromeProfile")
            return str(Path(__file__).parent.parent / "BotChromeProfile")

        self._chrome_path_var.set(
            stored_chrome if stored_chrome else _default_chrome_path())

        ctk.CTkLabel(browser_card,
                     text="Chrome profile folder for bot LinkedIn session. "
                          "Use default or custom.",
                     font=F("small"), text_color=FG_DIM,
                     wraplength=520, justify="left"
                     ).grid(row=0, column=0, sticky="w", padx=16, pady=(12,8))

        toggle_row = ctk.CTkFrame(browser_card, fg_color="transparent")
        toggle_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0,4))

        # Preview label
        self._chrome_preview_lbl = ctk.CTkLabel(
            browser_card, text="",
            font=F("tiny"), text_color=MUTED,
            wraplength=520, justify="left", anchor="w")
        self._chrome_preview_lbl.grid(row=2, column=0, sticky="w", padx=16, pady=(0,2))

        # Custom path row — created BEFORE radio buttons so lambdas can ref it
        self._chrome_path_row = ctk.CTkFrame(browser_card, fg_color="transparent")
        self._chrome_path_row.grid(row=3, column=0, sticky="ew", padx=16, pady=(0,12))
        self._chrome_path_row.grid_columnconfigure(0, weight=1)

        self._chrome_path_entry = ctk.CTkEntry(
            self._chrome_path_row, textvariable=self._chrome_path_var,
            height=36, font=F("small"))
        self._chrome_path_entry.grid(row=0, column=0, sticky="ew", padx=(0,8))

        def _browse_chrome():
            from tkinter import filedialog
            p = filedialog.askdirectory(title="Select Chrome profile folder")
            if p:
                self._chrome_path_var.set(p)
                self._save_chrome_profile_path(p)
                self._chrome_preview_lbl.configure(text="Path: " + p)

        ctk.CTkButton(self._chrome_path_row, text="Browse",
                      width=80, height=36, font=F("small"),
                      fg_color=BG_FIELD, hover_color=BG_HOVER,
                      command=_browse_chrome
                      ).grid(row=0, column=1)

        ctk.CTkButton(self._chrome_path_row, text="Save",
                      width=80, height=36, font=F("small"),
                      command=lambda: (
                          self._save_chrome_profile_path(
                              self._chrome_path_var.get().strip()),
                          self._chrome_preview_lbl.configure(
                              text="Saved: " + self._chrome_path_var.get().strip())
                      )).grid(row=0, column=2, padx=(8,0))

        # Radio buttons — now safe since _chrome_path_row already exists
        self._chrome_use_default = ctk.CTkRadioButton(
            toggle_row, text="Use default path",
            font=F("small"), text_color=FG,
            variable=self._chrome_default_var, value=True,
            command=lambda: self._on_chrome_toggle(
                True, self._chrome_path_row,
                self._chrome_path_entry, _default_chrome_path()))
        self._chrome_use_default.pack(side="left", padx=(0,16))

        self._chrome_use_custom = ctk.CTkRadioButton(
            toggle_row, text="Use custom path",
            font=F("small"), text_color=FG,
            variable=self._chrome_default_var, value=False,
            command=lambda: self._on_chrome_toggle(
                False, self._chrome_path_row,
                self._chrome_path_entry, _default_chrome_path()))
        self._chrome_use_custom.pack(side="left")

        # Init toggle state
        self._on_chrome_toggle(
            self._chrome_default_var.get(),
            self._chrome_path_row, self._chrome_path_entry,
            _default_chrome_path())

        # ══ SECTION 3: JOB PREFERENCES ════════════════════════════
        section_label("Job Preferences")
        pref_card = section_card()
        pref_card.grid_columnconfigure((0,1,2), weight=1)

        # Load saved prefs
        prefs = self._load_job_prefs()

        # Experience level
        ctk.CTkLabel(pref_card, text="Experience Level",
                     font=F("small"), text_color=MUTED
                     ).grid(row=0, column=0, columnspan=3, sticky="w",
                             padx=16, pady=(14, 4))
        self._exp_vars = {}
        exp_levels = [
            ("Internship",  "internship"),
            ("Entry Level", "entry"),
            ("Associate",   "associate"),
            ("Mid-Senior",  "mid_senior"),
            ("Director",    "director"),
            ("Executive",   "executive"),
        ]
        for i, (label, val) in enumerate(exp_levels):
            var = ctk.BooleanVar(value=val in prefs.get("experience_levels", []))
            ctk.CTkCheckBox(pref_card, text=label, variable=var,
                             font=F("small"),
                             command=self._save_job_prefs
                             ).grid(row=1+i//3, column=i%3, sticky="w",
                                     padx=16, pady=2)
            self._exp_vars[val] = var

        # Job type
        ctk.CTkFrame(pref_card, height=1, fg_color=BG_HOVER
                     ).grid(row=3, column=0, columnspan=3,
                             sticky="ew", padx=16, pady=(10, 6))
        ctk.CTkLabel(pref_card, text="Job Type",
                     font=F("small"), text_color=MUTED
                     ).grid(row=4, column=0, columnspan=3, sticky="w",
                             padx=16, pady=(0, 4))
        self._type_vars = {}
        job_types = [
            ("Full-time",   "full_time"),
            ("Part-time",   "part_time"),
            ("Contract",    "contract"),
            ("Temporary",   "temporary"),
            ("Internship",  "internship"),
            ("Volunteer",   "volunteer"),
        ]
        for i, (label, val) in enumerate(job_types):
            var = ctk.BooleanVar(value=val in prefs.get("job_types", []))
            ctk.CTkCheckBox(pref_card, text=label, variable=var,
                             font=F("small"),
                             command=self._save_job_prefs
                             ).grid(row=5+i//3, column=i%3, sticky="w",
                                     padx=16, pady=2)
            self._type_vars[val] = var

        # Workplace
        ctk.CTkFrame(pref_card, height=1, fg_color=BG_HOVER
                     ).grid(row=7, column=0, columnspan=3,
                             sticky="ew", padx=16, pady=(10, 6))
        ctk.CTkLabel(pref_card, text="Workplace Type",
                     font=F("small"), text_color=MUTED
                     ).grid(row=8, column=0, columnspan=3, sticky="w",
                             padx=16, pady=(0, 4))
        self._place_vars = {}
        workplaces = [
            ("On-site", "on_site"),
            ("Remote",  "remote"),
            ("Hybrid",  "hybrid"),
        ]
        for i, (label, val) in enumerate(workplaces):
            var = ctk.BooleanVar(value=val in prefs.get("workplace",
                                 ["on_site","remote","hybrid"]))
            ctk.CTkCheckBox(pref_card, text=label, variable=var,
                             font=F("small"),
                             command=self._save_job_prefs
                             ).grid(row=9, column=i, sticky="w",
                                     padx=16, pady=(0, 2))
            self._place_vars[val] = var

        # Easy Apply toggle
        ctk.CTkFrame(pref_card, height=1, fg_color=BG_HOVER
                     ).grid(row=10, column=0, columnspan=3,
                             sticky="ew", padx=16, pady=(10, 6))
        easy_row = ctk.CTkFrame(pref_card, fg_color="transparent")
        easy_row.grid(row=11, column=0, columnspan=3, sticky="ew",
                       padx=16, pady=(0, 14))
        self._easy_var = ctk.BooleanVar(
            value=prefs.get("easy_apply_only", True))
        ctk.CTkSwitch(easy_row, text="Easy Apply only",
                       variable=self._easy_var, font=F("small"),
                       command=self._save_job_prefs
                       ).pack(side="left")
        ctk.CTkLabel(easy_row,
                      text="Only apply to jobs with the LinkedIn Easy Apply button",
                      font=F("tiny"), text_color=MUTED
                      ).pack(side="left", padx=(12, 0))

        # ── Date Posted filter ─────────────────────────────────
        date_row = ctk.CTkFrame(pref_card, fg_color="transparent")
        date_row.grid(row=12, column=0, columnspan=3, sticky="ew",
                      padx=16, pady=(0, 14))
        ctk.CTkLabel(date_row, text="Date Posted:",
                     font=F("small"), text_color=FG
                     ).pack(side="left", padx=(0, 10))
        self._date_var = ctk.StringVar(
            value=prefs.get("date_posted", "any"))
        for _lbl, _val in [("Any time","any"),("Past month","month"),
                            ("Past week","week"),("Past 24 hrs","24hr")]:
            ctk.CTkRadioButton(
                date_row, text=_lbl, variable=self._date_var, value=_val,
                font=F("small"), command=self._save_job_prefs,
            ).pack(side="left", padx=6)

        # ══ SECTION 3: TEXT SIZE ══════════════════════════════════
        section_label("Text Size")
        size_card = section_card()
        size_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(size_card, text="Changes apply instantly across the whole app.",
                     font=F("small"), text_color=FG_DIM
                     ).grid(row=0, column=0, columnspan=3,
                             sticky="w", padx=18, pady=(14, 8))
        ctk.CTkButton(size_card, text="A−", width=42, height=36,
                      font=F("label_b"), fg_color=BG_FIELD,
                      hover_color=BG_HOVER, text_color=FG,
                      command=lambda: self._change_font_size(-1)
                      ).grid(row=1, column=0, padx=(18, 6), pady=(0, 14))
        self._font_slider = ctk.CTkSlider(
            size_card, from_=8, to=18, number_of_steps=10,
            command=self._on_font_slider)
        self._font_slider.set(_BASE_SIZE)
        self._font_slider.grid(row=1, column=1, sticky="ew",
                                padx=6, pady=(0, 14))
        ctk.CTkButton(size_card, text="A+", width=42, height=36,
                      font=F("label_b"), fg_color=BG_FIELD,
                      hover_color=BG_HOVER, text_color=FG,
                      command=lambda: self._change_font_size(+1)
                      ).grid(row=1, column=2, padx=(6, 18), pady=(0, 14))
        self._font_preview_var = ctk.StringVar(value=f"Current: {_BASE_SIZE}pt")
        ctk.CTkLabel(size_card, textvariable=self._font_preview_var,
                     font=F("small"), text_color=MUTED
                     ).grid(row=2, column=0, columnspan=3, pady=(0, 8))

    # ── Resume profile helpers ────────────────────────────────────

    # ── Chrome profile helpers ───────────────────────────────────

    def _load_chrome_profile_path(self) -> str:
        """Return stored custom chrome profile path, or '' for default."""
        try:
            p = _settings_file()
            if p.exists():
                import json as _j
                return _j.loads(p.read_text(encoding="utf-8")).get(
                    "chrome_profile_path", "")
        except Exception:
            pass
        return ""

    def _save_chrome_profile_path(self, path: str):
        """Persist chrome profile path to local_settings.json."""
        try:
            p      = _settings_file()
            data   = {}
            if p.exists():
                import json as _j
                data = _j.loads(p.read_text(encoding="utf-8"))
            # Empty string means use default
            data["chrome_profile_path"] = path.strip()
            p.write_text(__import__("json").dumps(data, indent=2),
                         encoding="utf-8")
        except Exception as e:
            print("Could not save chrome profile path: %s" % e)

    def _on_chrome_toggle(self, use_default: bool, path_row=None,
                           path_entry=None, default_path: str = ""):
        """Show/hide the custom path row and update preview label."""
        # Fall back to instance vars if not passed
        row   = path_row   or getattr(self, "_chrome_path_row",   None)
        if not default_path:
            import sys as _s, os as _o
            default_path = (
                _o.path.join(_o.path.dirname(_s.executable), "BotChromeProfile")
                if getattr(_s, "frozen", False)
                else str(Path(__file__).parent.parent / "BotChromeProfile")
            )
        if use_default:
            if row:
                row.grid_remove()
            self._chrome_preview_lbl.configure(
                text="Default: %s" % default_path)
            self._save_chrome_profile_path("")
        else:
            if row:
                row.grid()
            current = self._chrome_path_var.get().strip()
            if not current or current == default_path:
                self._chrome_path_var.set(default_path)
            self._chrome_preview_lbl.configure(
                text="Custom: %s" % self._chrome_path_var.get())

    def _xml_path(self) -> str:
        """
        Resolve resume_data.xml path using core/settings.py.
        Single source of truth - no duplicate logic.
        """
        try:
            from core.settings import get_resume_data_path
            return get_resume_data_path()
        except Exception:
            # Fallback: Documents\Resuto\resume_data.xml
            from pathlib import Path
            return str(Path.home() / "Documents" / "Resuto" / "resume_data.xml")


    def _toggle_key_visibility(self):
        self._key_visible = not self._key_visible
        self._settings_key_entry.configure(
            show="" if self._key_visible else "•")

    def _on_api_key_changed(self):
        """Called whenever the API key StringVar changes."""
        if self._api_save_pref.get():
            _save_api_key(self._api_var.get().strip())
        # Refresh start screen status dots if they exist
        try: self._refresh_start_status()
        except Exception: pass

    def _on_api_save_toggle(self):
        """User toggled the 'Remember API key' switch."""
        if self._api_save_pref.get():
            _save_api_key(self._api_var.get().strip())
            self._key_status_lbl.configure(
                text="✓  Key saved to local_settings.json",
                text_color=SUCCESS)
        else:
            _clear_api_key()
            self._key_status_lbl.configure(
                text="Key will be cleared when the app closes.",
                text_color=MUTED)

    def _on_visa_changed(self, selection: str):
        """Save visa selection → local_settings.json + resume_data.xml meta tag."""
        VISA_CODE = {
            "CPT (F-1 student — work authorized)":          "CPT",
            "OPT (F-1 — post-graduation, work authorized)": "OPT",
            "STEM OPT (F-1 — 24-month extension)":          "STEM OPT",
            "H-1B (employer sponsored)":                    "H-1B",
            "Green Card (permanent resident)":              "Green Card",
            "US Citizen":                                   "US Citizen",
            "Other / Not listed":                           "Other",
        }
        code = VISA_CODE.get(selection, selection)

        # 1. Save to local_settings.json
        try:
            s = _load_settings()
            s["work_authorization"] = code
            _save_settings(s)
        except Exception as e:
            print("[WARN] Could not save visa status: %s" % e)

        # 2. Write into resume_data.xml <meta><work_authorization>
        try:
            import xml.etree.ElementTree as ET
            from core.settings import get_resume_data_path
            xml_path = get_resume_data_path()
            if xml_path and os.path.exists(xml_path):
                ET.register_namespace("", "")
                tree = ET.parse(xml_path)
                root = tree.getroot()
                meta = root.find("meta")
                if meta is None:
                    meta = ET.SubElement(root, "meta")
                    root.insert(0, meta)   # meta goes first
                wa = meta.find("work_authorization")
                if wa is None:
                    wa = ET.SubElement(meta, "work_authorization")
                wa.text = code
                ET.indent(tree, space="    ")
                tree.write(xml_path, encoding="unicode", xml_declaration=True)
        except Exception as e:
            print("[WARN] Could not update XML work_authorization: %s" % e)

        # 3. Update config at runtime so blocking phrases adjust immediately
        try:
            import core.config as _cfg
            _cfg.CPT_SCREENING["screen_sponsorship"] = False  # always False for CPT/OPT
            if code in ("CPT", "OPT", "STEM OPT"):
                # CPT/OPT: drop all sponsorship blocking phrases, keep citizenship only
                _cfg.CPT_SCREENING["blocking_phrases"] = [
                    p for p in _cfg.CPT_SCREENING.get("blocking_phrases", [])
                    if any(x in p for x in ["citizen", "clearance", "permanent resident"])
                ]
        except Exception:
            pass

        self._update_visa_note(selection)
        self._set_status("Work authorization set to: %s" % code)

    def _update_visa_note(self, selection: str):
        notes = {
            "CPT (F-1 student — work authorized)":
                "✅  CPT = work authorized immediately. Jobs saying 'no sponsorship' are ELIGIBLE. "
                "Only real blockers: US citizenship required, security clearance.",
            "OPT (F-1 — post-graduation, work authorized)":
                "✅  OPT = work authorized. No sponsorship needed now. "
                "Employer needs to be E-Verify registered for STEM OPT extension later.",
            "STEM OPT (F-1 — 24-month extension)":
                "✅  STEM OPT = work authorized. Employer must be E-Verify registered. "
                "Same eligibility as OPT — 'no sponsorship' jobs are fine.",
            "H-1B (employer sponsored)":
                "⚠️  H-1B = employer must be willing to sponsor. "
                "Jobs saying 'no sponsorship' mean they won't support H-1B renewal. Relevant to filter.",
            "Green Card (permanent resident)":
                "✅  Green Card = permanent work authorization. No sponsorship needed. "
                "No meaningful restrictions on most jobs.",
            "US Citizen":
                "✅  US Citizen = unrestricted. Eligible for all jobs including security clearance roles.",
            "Other / Not listed":
                "ℹ️  Work authorization status unknown. The bot will not apply sponsorship filters. "
                "Manually verify job eligibility before applying.",
        }
        note = notes.get(selection, "")
        if hasattr(self, "_visa_note"):
            self._visa_note.configure(text=note)

    def _validate_api_key(self):
        """Quick ping to Anthropic to confirm the key works."""
        key = self._api_var.get().strip()
        if not key:
            self._key_status_lbl.configure(
                text="Enter a key first.", text_color=DANGER); return
        self._key_status_lbl.configure(
            text="Validating...", text_color=MUTED)
        def _do():
            try:
                import anthropic as _ant
                client = _ant.Anthropic(api_key=key)
                client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1,
                    messages=[{"role":"user","content":"hi"}])
                self._q.put(("key_ok", None))
            except Exception as e:
                self._q.put(("key_fail", str(e)[:80]))
        threading.Thread(target=_do, daemon=True).start()

    def _resume_status_text(self) -> str:
        xml = Path(self._xml_path())
        if xml.exists():
            import xml.etree.ElementTree as _ET
            try:
                root = _ET.parse(str(xml)).getroot()
                name = root.findtext(".//name") or "Unknown"
                return f"✓  Profile loaded  —  {name}"
            except Exception:
                return "✓  resume_data.xml found"
        return "No profile yet.  Upload your resume PDF to get started."

    def _upload_resume(self):
        """Open file dialog, then launch the intake window."""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select your resume",
            filetypes=[("PDF files","*.pdf"),("Word files","*.docx"),
                       ("All files","*.*")]
        )
        if not path:
            return
        # Check API key
        key = self._api_var.get().strip()
        if not key or not key.startswith("sk-ant-"):
            messagebox.showwarning("API Key Needed",
                "Enter your Claude API key on the Run tab first.")
            return
        IntakeWindow(self, path, key, self._xml_path(),
                     on_done=self._on_intake_done)

    def _on_intake_done(self):
        """Called by IntakeWindow when XML is saved."""
        self._resume_status_var.set(self._resume_status_text())
        self._view_profile_btn.pack(side="left")
        try:
            self._refresh_start_status()
        except Exception:
            pass

    def _regenerate_prompts_manual(self):
        """Force-regenerate personalised prompts from current XML."""
        key = self._api_var.get().strip()
        if not key or not key.startswith("sk-ant-"):
            messagebox.showwarning("API Key Needed",
                "Enter your Claude API key first.")
            return
        if not Path(self._xml_path()).exists():
            messagebox.showwarning("No Profile",
                "Upload your resume PDF first to create a profile.")
            return

        if not messagebox.askyesno("Regenerate Prompts",
            "This will rebuild your personalised relevance and resume "
            "prompts from your current profile. "
            "Takes about 30 seconds. Continue?"):
            return

        self._set_status("Regenerating prompts...")

        def _do():
            try:
                import anthropic as _ant
                from api.prompts import (setup_all_prompts,
                                          delete_prompt,
                                          PROMPT_RELEVANCE_CHECK,
                                          PROMPT_RESUME_TAILOR,
                                          get_profile_hash,
                                          store_profile_hash)
                from core.profile import load_profile_from_xml

                profile = load_profile_from_xml(self._xml_path())
                client  = _ant.Anthropic(api_key=key)

                # Clear old personal prompts
                delete_prompt(PROMPT_RELEVANCE_CHECK)
                delete_prompt(PROMPT_RESUME_TAILOR)

                result = setup_all_prompts(profile, client, overwrite=False)
                store_profile_hash(get_profile_hash(profile))

                self._q.put(("s_status",
                    "Prompts regenerated (%d created)." % result["created"]))
                messagebox.showinfo("Done",
                    "Prompts regenerated. The bot will now use your "
                    "updated profile for job filtering.")
            except Exception as e:
                self._q.put(("s_status", "Regeneration failed: %s" % e))
                messagebox.showerror("Error",
                    "Could not regenerate prompts: %s" % e)
        threading.Thread(target=_do, daemon=True).start()

    def _view_profile(self):
        """Open the profile viewer window."""
        ProfileViewWindow(self, self._xml_path())

    # ── Job preferences helpers ───────────────────────────────────

    def _prefs_path(self) -> str:
        """Return path to job_prefs.json — stored in Documents/Resuto/."""
        try:
            from core.settings import get_settings
            d = get_settings()
            out = d.get("output_dir", "")
            if out and Path(out).is_dir():
                return str(Path(out) / "job_prefs.json")
        except Exception:
            pass
        import sys as _sp
        return str(Path(_sp.executable).parent / "job_prefs.json")

    def _load_job_prefs(self) -> dict:
        try:
            p = Path(self._prefs_path())
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        # Sensible defaults — generic, not CPT-specific
        return {
            "experience_levels": [],
            "job_types":         [],
            "workplace":         ["on_site","remote","hybrid"],
            "easy_apply_only":   True,
            "date_posted":       "any",
        }

    def _save_job_prefs(self):
        prefs = {
            "experience_levels": [v for v,var in self._exp_vars.items()
                                   if var.get()],
            "job_types":         [v for v,var in self._type_vars.items()
                                   if var.get()],
            "workplace":         [v for v,var in self._place_vars.items()
                                   if var.get()],
            "easy_apply_only":   self._easy_var.get(),
            "date_posted":       self._date_var.get(),
        }
        try:
            p = Path(self._prefs_path())
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        except Exception:
            pass

        """Called by slider drag — round to int and apply."""
    def _on_font_slider(self, value: float):
        """Debounced: only apply after 150ms settle to avoid lag on drag."""
        if hasattr(self, "_font_debounce_id") and self._font_debounce_id:
            try: self.after_cancel(self._font_debounce_id)
            except Exception: pass
        size = int(round(value))
        self._font_debounce_id = self.after(
            150, lambda: self._apply_font_size(size))

    def _change_font_size(self, delta: int):
        """Called by A+ / A− buttons."""
        new_size = max(8, min(18, _BASE_SIZE + delta))
        self._apply_font_size(new_size)
        self._font_slider.set(new_size)

    def _apply_font_size(self, size: int):
        """
        Update all CTkFont objects to a new base size.
        Because widgets hold a reference to the font object (not a copy),
        calling font.configure() propagates the change everywhere instantly.
        """
        _init_fonts(size)     # reconfigures every font in _FONTS dict
        _save_font_pref(size) # persist to local_settings.json
        self._font_preview_var.set(f"Current: {size}pt")
        try:
            self._font_slider.set(size)
        except Exception:
            pass