# app.py
"""
Job Application Automation GUI — built with CustomTkinter.

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

# ── App identity ──────────────────────────────────────────────────
APP_TITLE  = "Resuto"
def _get_bot_script() -> Path:
    """
    Return the path to orchestrator.py.
    Works both from source and inside a PyInstaller bundle.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent.parent

    p = base / "backend" / "orchestrator.py"

    # Fallback: try common alternative locations
    if not p.exists():
        for alt in [
            base / "main.py",
            base / "orchestrator.py",
            Path(__file__).parent / "orchestrator.py",
        ]:
            if alt.exists():
                return alt

    return p

BOT_SCRIPT = _get_bot_script()

# ── Palette (matches CTk dark theme) ─────────────────────────────
BG        = "#0F1117"
BG_CARD   = "#1C1F26"
BG_FIELD  = "#2A2D35"
BG_HOVER  = "#252830"
ACCENT    = "#5B6AF0"
ACCENT_HV = "#4A58D4"   # hover shade
DANGER    = "#E84545"
SUCCESS   = "#22C55E"
WARNING   = "#F59E0B"
MUTED     = "#6B7280"
FG        = "#F1F2F4"
FG_SOFT   = "#C8CBD2"
FG_DIM    = "#8B8FA8"


def _settings_file() -> Path:
    """Return local_settings.json path — works both from source and exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "local_settings.json"
    return Path(__file__).parent.parent / "local_settings.json"  # source mode

def _load_settings() -> dict:
    """Load full local_settings.json."""
    try:
        p = Path(sys.executable).parent / "local_settings.json" if getattr(sys, "frozen", False)             else Path(__file__).parent.parent / "local_settings.json"
        if p.exists():
            import json as _j
            return _j.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_settings(data: dict) -> None:
    """Save full local_settings.json."""
    try:
        p = Path(sys.executable).parent / "local_settings.json" if getattr(sys, "frozen", False)             else Path(__file__).parent.parent / "local_settings.json"
        import json as _j
        p.write_text(_j.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

def _load_api_key() -> str:
    """Load saved API key from local_settings.json."""
    try:
        p = _settings_file()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return d.get("api_key", "")
    except Exception:
        pass
    return ""

def _save_api_key(key: str) -> None:
    """Persist API key to local_settings.json."""
    try:
        p = _settings_file()
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        d["api_key"] = key
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:
        pass

def _clear_api_key() -> None:
    """Remove API key from local_settings.json."""
    try:
        p = _settings_file()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            d.pop("api_key", None)
            p.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:
        pass

# ── Font system ────────────────────────────────────────────────────
# Using ctk.CTkFont objects instead of plain tuples.
# When you call font.configure(size=N), every widget using that font
# updates instantly — no restart needed.
#
# _BASE_SIZE is the reference size. All other sizes are offsets from it.
# Saving to / loading from local_settings.json keeps the preference across sessions.

_BASE_SIZE   = 14   # changed by the settings slider; loaded from prefs on startup
import platform as _platform
_FONT_FAMILY = {
    "Darwin":  "SF Pro Display",
    "Windows": "Segoe UI",
}.get(_platform.system(), "DejaVu Sans")

# Named font objects — created once in _init_fonts(), referenced everywhere.
# Never construct ("Segoe UI", N) tuples inline — use these names.
_FONTS: dict[str, "ctk.CTkFont"] = {}

def _init_fonts(base: int = 11) -> None:
    """Create (or reconfigure) all named CTkFont objects."""
    global _BASE_SIZE
    _BASE_SIZE = max(8, min(20, base))
    specs = {
        # name         : (offset, bold)
        "tiny"         : (-3, False),
        "small"        : (-2, False),
        "small_b"      : (-2, True),
        "body"         : ( 0, False),
        "body_b"       : ( 0, True),
        "label"        : (-1, False),
        "label_b"      : (-1, True),
        "heading"      : (+2, True),
        "title"        : (+4, True),
        "stat"         : (+11, True),
        "icon_lg"      : (+5, False),
        "icon"         : (+1, False),
        "mono"         : ( 0, False),   # Consolas for error log
    }
    for name, (offset, bold) in specs.items():
        size   = max(7, _BASE_SIZE + offset)
        family = "Consolas" if name == "mono" else _FONT_FAMILY
        weight = "bold" if bold else "normal"
        if name in _FONTS:
            _FONTS[name].configure(family=family, size=size, weight=weight)
        else:
            _FONTS[name] = ctk.CTkFont(family=family, size=size, weight=weight)

def F(name: str):
    """
    Return the named CTkFont if fonts have been initialised (i.e. after App.__init__
    creates the CTk root), otherwise fall back to a plain tuple so module-level
    code never crashes.
    """
    if name in _FONTS:
        return _FONTS[name]
    # Fallback tuple — used only before _init_fonts() is called
    _fallback = {
        "tiny":    (_FONT_FAMILY,  8),
        "small":   (_FONT_FAMILY,  9), "small_b":  (_FONT_FAMILY,  9, "bold"),
        "label":   (_FONT_FAMILY, 10), "label_b":  (_FONT_FAMILY, 10, "bold"),
        "body":    (_FONT_FAMILY, 11), "body_b":   (_FONT_FAMILY, 11, "bold"),
        "heading": (_FONT_FAMILY, 13, "bold"),
        "title":   (_FONT_FAMILY, 15, "bold"),
        "stat":    (_FONT_FAMILY, 22, "bold"),
        "icon_lg": (_FONT_FAMILY, 16),
        "icon":    (_FONT_FAMILY, 12),
        "mono":    ("Consolas", 11),
    }
    return _fallback.get(name, (_FONT_FAMILY, 11))

def _load_font_pref() -> int:
    """Read saved base font size from local_settings.json."""
    try:
        p = _settings_file()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return int(d.get("font_size", 14))
    except Exception:
        pass
    return 14

def _save_font_pref(size: int) -> None:
    """Persist base font size to local_settings.json."""
    try:
        p = _settings_file()
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        d["font_size"] = size
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:
        pass

# Fonts are initialised inside App.__init__() after CTk root exists.

# ── Regex patterns ────────────────────────────────────────────────
_ERROR_RE = re.compile(
    r"(Traceback|EOFError|KeyError|ValueError|TypeError|AttributeError|"
    r"authentication_error|invalid.{0,20}key|"
    r"\[ERR\].*(?:failed|crash|invalid)(?!.*:\s*0))",
    re.IGNORECASE,
)
_PHASE_MAP = [
    (re.compile(r"Checking.*API|Checking your Claude"), "Verifying API key..."),
    (re.compile(r"API key is valid"),                   "API key verified"),
    (re.compile(r"Analysing your profile"),             "Analysing profile..."),
    (re.compile(r"log in manually"),                    "Waiting: log in to LinkedIn"),
    (re.compile(r"PHASE 1|Scanning jobs"),              "Phase 1 — Scanning jobs..."),
    (re.compile(r"Phase 2.*Generating|Phase 2 --"),     "Phase 2 — Generating resumes..."),
    (re.compile(r"Phase 2 complete"),                   "Resumes ready"),
    (re.compile(r"Phase 3|guided apply"),               "Phase 3 — Guided applying..."),
    # Phase 3 per-job patterns
    (re.compile(r"\[WAIT\].*YOUR TURN|job is open in the browser"), "Phase 3 — Waiting for you..."),
    (re.compile(r"\[OK\] Marked as applied"),           "Phase 3 — Applied ✓"),
    (re.compile(r"\[SKIP\].*Marked as skipped"),        "Phase 3 — Skipped, next job..."),
    (re.compile(r"\[BOT_IDLE\]"),                       "Run complete — browser open"),
    (re.compile(r"Session complete|roles processed"),   "Run complete"),
    (re.compile(r"Final Summary|Done!|done\."),         "Run complete"),
]
_DSQ_RE     = re.compile(r"d / s / q|d/s/q",          re.IGNORECASE)
_NF_RE      = re.compile(r"n / f",                    re.IGNORECASE)
_APPLY_RE   = re.compile(r"ready to start applying",  re.IGNORECASE)
_REAPPLY_RE = re.compile(r"review them for re-application", re.IGNORECASE)
_IDLE_RE     = re.compile(r"\[BOT_IDLE\]")
_LAST_JOB_RE = re.compile(r"\[BOT_LAST_JOB\]")
_ACTIVITY_PATTERNS = [
    (re.compile(r"Scanning.*filtering.*['\"](.+?)['\"]", re.I), "role",    "Phase 1 — Scanning LinkedIn"),
    (re.compile(r"^\*\s+(.+@.+)$"),                             "role",    "Checking relevance..."),
    (re.compile(r"^\|\s+(.+@.+)$"),                             "role",    "Analysing match..."),
    (re.compile(r"\[(\d+)/(\d+)\]\s+(.+@.+)$"),                "resume",  None),
    (re.compile(r"\[OK\] Resume ready"),                        "action",  "Resume generated"),
    (re.compile(r"Highlighting experience"),                    "action",  "Tailoring experience..."),
]

# ── DB helpers ─────────────────────────────────────────────────────
def _db_path() -> str:
    if not hasattr(_db_path, "_v"):
        from core.settings import get_output_dir
        _db_path._v = os.path.join(get_output_dir(), "applications.db")
    return _db_path._v

def _read_stats(since: str = None, recent_since: str = None) -> dict:
    db = _db_path()
    if not os.path.exists(db):
        return {}
    try:
        where = f"WHERE logged_at>='{since}'" if since else ""
        and_s = f"AND logged_at>='{since}'" if since else ""
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
                f"WHERE status IN('applied','skipped','failed') "
                f"AND match_score IS NOT NULL {and_s}"
            ).fetchone()
            avg = round(avg_row["a"] or 0) if avg_row and avg_row["a"] else 0
            # Recent: only jobs worth seeing — exclude hard pre-filtered/ineligible
            # Pre-filtered (title mismatch) and hard ineligible (citizenship)
            # pollute the tab with noise the user can't act on
            jobs = [dict(r) for r in c.execute(
                "SELECT job_title,company,status,match_score,"
                "skill_overlap,ai_reason,logged_at,stretch,notes "
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
                "ORDER BY logged_at DESC LIMIT 12")]
        return {"counts": counts, "avg": avg,
                "jobs": jobs, "queued": counts.get("matched", 0)}
    except Exception:
        return {}

def _read_history(filt: str) -> list:
    db = _db_path()
    if not os.path.exists(db):
        return []
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
        where = "WHERE status='matched' AND stretch=1"
    elif filt == "skipped":
        # Show scored skips only (had API relevance check) not pre-filtered
        where = ("WHERE status='skipped' AND match_score > 0")
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
class BotRunner:
    def __init__(self, key, args, on_line, on_done):
        self._key, self._args = key, args
        self._on_line, self._on_done = on_line, on_done
        self._proc = None

    def start(self):
        env = os.environ.copy()
        env.update({"ANTHROPIC_API_KEY": self._key,
                     "PYTHONIOENCODING": "utf-8",
                     "PYTHONUTF8":       "1",
                     "PYTHONUNBUFFERED": "1",
                     "BOT_GUI_MODE":     "1"})

        if getattr(sys, "frozen", False):
            # Running as PyInstaller exe — sys.executable IS the exe.
            # Pass --bot-mode so the exe runs orchestrator instead of GUI.
            cmd = [sys.executable, "--bot-mode", "--gui"] + self._args
        else:
            # Running from source — launch orchestrator.py with Python
            script = str(BOT_SCRIPT)
            if not os.path.exists(script):
                self._on_done(-1)
                return
            cmd = [sys.executable, "-u", script, "--gui"] + self._args

        self._proc = subprocess.Popen(
            cmd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=0)
        threading.Thread(target=self._read, daemon=True).start()

    def send(self, text: str):
        try:
            if self._proc and self._proc.stdin:
                self._proc.stdin.write(text + "\n")
                self._proc.stdin.flush()
        except Exception:
            pass

    def stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def running(self): return self._proc is not None and self._proc.poll() is None

    def _read(self):
        try:
            for line in self._proc.stdout:
                self._on_line(line.rstrip())
        finally:
            self._on_done(self._proc.wait())

# ── Main application ───────────────────────────────────────────────
class App(ctk.CTk):

    # ── Canvas row metrics ────────────────────────────────────────

    def __init__(self):
        super().__init__()
        # Fonts MUST be created after super().__init__() — CTkFont needs a live Tk root
        _init_fonts(_load_font_pref())
        # API key StringVar — loaded from settings (may be empty if user chose not to save)
        self._api_var       = ctk.StringVar(value=_load_api_key())
        self._api_save_pref = ctk.BooleanVar(value=bool(_load_api_key()))

        # First-time registration gate — STRICT: any failure blocks the app
        _license_ok = False
        try:
            from core.license import is_approved
            try:
                _license_ok = is_approved()
            except Exception as _chk_err:
                # is_approved() failed (e.g. no internet, sheet error)
                # Treat as not approved — show registration
                _license_ok = False

        except ImportError as _ie:
            import tkinter as _tk
            _r = _tk.Tk(); _r.withdraw()
            _tk.messagebox.showerror(
                "Missing dependency",
                f"License module failed to load:\n{_ie}\n\n"
                f"Run: pip install cryptography gspread")
            _r.destroy()
            self.destroy()
            return

        except Exception as _ex:
            import tkinter as _tk
            _r = _tk.Tk(); _r.withdraw()
            _tk.messagebox.showerror("License Error",
                f"Unexpected error in license check:\n{_ex}")
            _r.destroy()
            self.destroy()
            return

        if not _license_ok:
            _ok = RegistrationWindow.run_gate(self)
            if not _ok:
                self.destroy()
                return

        self.title(APP_TITLE)
        self.geometry("920x640")
        self.minsize(820, 560)

        self._q             = queue.Queue()
        self._runner        = None
        self._err_count     = 0
        self._live          = False
        from datetime import datetime as _dt
        self._session_start = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        self._stats_last_hash = None
        self._stats_after_id  = None


        # Stats state
        self._stat_vars = {}   # key -> ctk.StringVar

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()
        self._sched_stats()

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
        ctk.CTkLabel(top, text="Bot", font=F("tiny"),
                     text_color=FG_DIM).pack()
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
        self._nav_btns.append((sf, sico))   # index 4

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
        ctk.CTkLabel(hdr, text="Job Application Automation",
                     font=F("heading"), text_color=FG).pack(side="left", padx=16)
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
        prev = getattr(self, "_active_tab", -1)
        self._active_tab = idx

        for i, name in enumerate(order):
            tab = self._tabs[name]
            if i == idx:
                tab.pack(fill="both", expand=True)
            else:
                tab.pack_forget()

        # Highlight active nav icon + dim others
        for i, (frm, ico) in enumerate(self._nav_btns):
            ico.configure(text_color=ACCENT if i == idx else FG_DIM)

        # Load history when navigating to it.
        # During a live run, _sched_stats also calls _load_history every 5s.
        if idx == 3:
            self._load_history()

    # ── Run tab (wizard) ──────────────────────────────────────────
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

    def _card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=BG_CARD,
                             corner_radius=10, **kw)

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
            self._q.put(("s3_status", "Loading your profile..."))

            # Always reload from XML directly — MY_PROFILE may be stale
            # if the XML was linked after the app started
            _mp = {}
            xml = self._xml_path()
            try:
                from core.profile import load_profile_from_xml
                _mp = load_profile_from_xml(xml)
            except Exception:
                # Fallback to module-level if function not available
                from core.profile import MY_PROFILE as _mp_mod
                _mp = _mp_mod

            if not _mp:
                self._q.put(("roles_error",
                    "Profile is empty. Please complete the resume intake in Settings first."))
                return

            skills  = [s for v in _mp.get("skills",{}).values() for s in v]
            years   = _mp.get("years_experience", "unknown")
            summary = _mp.get("summary", "")
            title   = _mp.get("current_title", "")
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
            client = _ant.Anthropic(api_key=self._api_var.get().strip())
            msg = client.messages.create(model=_model, max_tokens=1000,
                                          messages=[{"role":"user","content":prompt}])
            text = msg.content[0].text.strip()
            if "```" in text:
                parts = text.split("```")
                text = parts[1][4:] if parts[1].startswith("json") else parts[1]
            # Find JSON array boundaries
            _js = text.find("[")
            _je = text.rfind("]") + 1
            if _js >= 0 and _je > _js:
                text = text[_js:_je]
            parsed = json.loads(text.strip())
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

        nav = ctk.CTkFrame(f, fg_color="transparent")
        nav.grid(row=2, column=0, sticky="ew", pady=(8,0))
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
            try:
                from core.logger import LOG_FILE
                import subprocess, platform
                if platform.system() == "Windows":
                    subprocess.Popen(["notepad.exe", LOG_FILE])
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", "-a", "TextEdit", LOG_FILE])
                else:
                    subprocess.Popen(["xdg-open", LOG_FILE])
            except Exception as e:
                self._append_error(f"Could not open log: {e}")

        def _show_log_path():
            try:
                from core.logger import LOG_FILE
                messagebox.showinfo("Log File Location", LOG_FILE)
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
    def _build_stats(self):
        f = self._tabs["stats"]
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(0, weight=0)  # header
        f.grid_rowconfigure(1, weight=0)  # activity strip
        f.grid_rowconfigure(2, weight=0)  # stat cards
        f.grid_rowconfigure(3, weight=1)  # mid row (donut + list) — gets all space

        # Row 0: header
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 4))
        ctk.CTkLabel(hdr, text="Application Stats",
                     font=F("body_b"), text_color=FG).pack(side="left")
        self._live_dot = ctk.CTkLabel(hdr, text="● Live",
                                       font=F("small"), text_color=MUTED)
        self._live_dot.pack(side="right")

        # Review button — enabled only when re-apply candidates exist
        self._review_btn = ctk.CTkButton(
            hdr, text="📋  Review Old Applications",
            height=30, font=F("small"),
            fg_color=BG_FIELD, hover_color=BG_HOVER,
            state="disabled",
            command=self._open_review_window)
        self._review_btn.pack(side="right", padx=(0, 12))

        # Row 1: activity strip (hidden until bot runs)
        self._act_strip = ctk.CTkFrame(f, fg_color=BG_CARD, corner_radius=8)
        self._act_role  = ctk.CTkLabel(self._act_strip, text="",
                                        font=F("label_b"), text_color=ACCENT, anchor="w")
        self._act_role.pack(anchor="w", padx=14, pady=(8, 0))
        self._act_action = ctk.CTkLabel(self._act_strip, text="",
                                         font=F("small"), text_color=FG_DIM, anchor="w")
        self._act_action.pack(anchor="w", padx=14, pady=(2, 8))
        # Start hidden — shown via grid() when bot output arrives
        self._act_strip_visible = False

        # Row 2: stat cards
        self._cards_row = ctk.CTkFrame(f, fg_color="transparent")
        self._cards_row.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 8))
        self._stat_vars = {}
        self._cards_row.grid_columnconfigure((0,1,2,3,4), weight=1)
        card_defs = [
            ("applied", "✓ Applied",   SUCCESS),
            ("skipped", "− Skipped",   WARNING),
            ("failed",  "✕ Failed",    DANGER),
            ("avg",     "◉ Avg Match", FG_SOFT),
            ("queued",  "⌛ Queued",    ACCENT),
        ]
        for col_idx, (key, label, color) in enumerate(card_defs):
            c = ctk.CTkFrame(self._cards_row, fg_color=BG_CARD,
                              corner_radius=10)
            c.grid(row=0, column=col_idx, sticky="ew",
                   padx=(0, 6), pady=4, ipadx=8, ipady=8)
            var = ctk.StringVar(value="--")
            ctk.CTkLabel(c, textvariable=var, font=F("stat"),
                         text_color=color).pack()
            ctk.CTkLabel(c, text=label, font=F("small"),
                         text_color=MUTED).pack()
            self._stat_vars[key] = (var, c)


        # Middle: donut + job list
        mid = ctk.CTkFrame(f, fg_color="transparent")
        mid.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 8))
        mid.grid_columnconfigure(1, weight=1)
        mid.grid_rowconfigure(0, weight=1)

        # Donut canvas
        self._donut_cv = tk.Canvas(mid, width=130, height=180,
                                    bg=BG, highlightthickness=0)
        self._donut_cv.grid(row=0, column=0, padx=(0, 16), sticky="n")
        self._draw_donut(0, 0, 0)

        # Scrollable job list — height=1 lets it fill via sticky="nsew"
        list_col = ctk.CTkFrame(mid, fg_color="transparent")
        list_col.grid(row=0, column=1, sticky="nsew")
        list_col.grid_columnconfigure(0, weight=1)
        list_col.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(list_col, text="Recent  (click to expand)",
                     font=F("small"), text_color=FG_DIM
                     ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        self._job_scroll = ctk.CTkScrollableFrame(
            list_col, fg_color="transparent", corner_radius=0)
        self._job_scroll.grid(row=1, column=0, sticky="nsew")
        self._job_scroll.grid_columnconfigure(0, weight=1)
        self._job_rows    = []
        self._job_expanded = None

    def _draw_donut(self, applied: int, skipped: int, failed: int):
        cv = self._donut_cv
        cv.delete("arc","hole","txt")
        cx, cy, ro, ri = 65, 65, 52, 30
        total = applied + skipped + failed or 1
        start = -90.0
        for n, col in [(applied, SUCCESS),(skipped, WARNING),(failed, DANGER)]:
            ext = n / total * 360
            if ext > 0.5:
                cv.create_arc(cx-ro, cy-ro, cx+ro, cy+ro,
                              start=start, extent=ext,
                              fill=col, outline=BG, width=3, tags="arc")
            start += ext
        if applied+skipped+failed == 0:
            cv.create_oval(cx-ro, cy-ro, cx+ro, cy+ro,
                           fill=BG_CARD, outline=BG, tags="arc")
        cv.create_oval(cx-ri, cy-ri, cx+ri, cy+ri,
                       fill=BG, outline=BG, tags="hole")
        cv.create_text(cx, cy-8, text=str(applied+skipped+failed),
                       fill=FG, font=F("heading"), tags="txt")
        cv.create_text(cx, cy+9, text="done",
                       fill=MUTED, font=F("tiny"), tags="txt")

        # Legend labels below donut
        cv.delete("leg")
        for i, (label, col) in enumerate([("● Applied", SUCCESS),
                                           ("● Skipped", WARNING),
                                           ("● Failed",  DANGER)]):
            cv.create_text(5, 138 + i * 14, text=label,
                           fill=col, font=F("small"), anchor="w", tags="leg")
        cv.configure(height=180)

    def _refresh_stats(self):
        """Compatibility wrapper — kicks off background fetch."""
        threading.Thread(target=self._bg_fetch_stats, daemon=True).start()

    def _force_stats_refresh(self):
        """Force an immediate stats refresh, bypassing the hash cache."""
        self._stats_last_hash = None
        threading.Thread(target=self._bg_fetch_stats, daemon=True).start()

    def _apply_stats_data(self, d: dict):
        """Called on the main thread with pre-fetched data. Zero DB I/O here."""
        if not d:
            return
        h = hashlib.md5(
            json.dumps(d, sort_keys=True, default=str).encode()
        ).hexdigest()
        if h == self._stats_last_hash:
            return
        self._stats_last_hash = h
        self._job_expanded = None   # reset stale ref on data change
        counts  = d.get("counts", {})
        avg     = d.get("avg", 0)
        jobs    = d.get("jobs", [])
        queued  = d.get("queued", 0)
        applied = counts.get("applied", 0)
        skipped = counts.get("skipped", 0)
        failed  = counts.get("failed",  0)

        updates = {"applied": str(applied), "skipped": str(skipped),
                   "failed": str(failed),   "avg": f"{avg}%",
                   "queued": str(queued)}
        for key, (var, card) in self._stat_vars.items():
            var.set(updates[key])
            if key == "queued":
                # Queued card always visible; just update value
                pass  # grid layout keeps all cards visible

        self._draw_donut(applied, skipped, failed)

        # Incremental job list update
        # Track rows by position; only destroy/create if count changed
        self._job_expanded = None
        STATUS_ICON = {"applied":"✓","skipped":"−","failed":"✕","matched":"▶","scanning":"●"}
        STATUS_COL  = {"applied":SUCCESS,"skipped":WARNING,"failed":DANGER,"matched":ACCENT,"scanning":MUTED}
        DECISION    = {"applied":"Applied","skipped":"Skipped","failed":"Failed","matched":"Queued","scanning":"Analyzing..."}

        def _badge(job, status, decision, s_col):
            """Return (label, color) — stretch jobs get amber badge."""
            if status == "matched" and job.get("stretch"):
                return "  Stretch  ", STRETCH
            return "  %s  " % decision, s_col
        if not hasattr(self, "_job_svars"):
            self._job_svars = []   # list of dicts of StringVars per row
        # Full rebuild only when row count changes
        existing = len(self._job_scroll.winfo_children())
        if existing != len(jobs):
            for w in self._job_scroll.winfo_children():
                w.destroy()
            self._job_svars = []
            self._job_rows  = []
            self._rebuild_job_rows = True
        else:
            self._rebuild_job_rows = False

        for row_i, job in enumerate(jobs):
            score    = job.get("match_score") or 0
            status   = job.get("status", "")
            title    = job.get("job_title") or "Unknown"
            company  = job.get("company") or ""
            reason   = job.get("ai_reason") or (
                "Queued — resume being generated..." if status == "matched"
                else "No reason recorded.")
            overlap  = job.get("skill_overlap") or 0
            when     = (job.get("logged_at") or "")[:16]
            decision = DECISION.get(status, status.capitalize())
            s_col    = STATUS_COL.get(status, MUTED)
            bar_col  = SUCCESS if score >= 70 else WARNING if score >= 45 else DANGER
            icon     = STATUS_ICON.get(status, "•")
            sk_txt   = f"  |  Overlap: {overlap}%" if overlap else ""

            # If rows already exist, update StringVars in-place (no widget create)
            if not self._rebuild_job_rows and row_i < len(self._job_svars):
                sv = self._job_svars[row_i]
                sv["icon"].set(icon)
                sv["title"].set(title)
                sv["meta"].set(f"{company}  •  {when}" if company else when)
                sv["score"].set(f"{score}%" if score else "")
                sv["reason"].set(reason)
                sv["badge"].set(f"  {decision}  ")
                sv["match"].set(f"  Match {score}%{sk_txt}")
                # Update bar color and width
                try:
                    new_bar_col = SUCCESS if score >= 70 else WARNING if score >= 45 else DANGER
                    new_fill_w  = max(2, int(score / 100 * 80))
                    sv["bar_fill"].configure(width=new_fill_w, fg_color=new_bar_col)
                except Exception:
                    pass
                continue   # skip widget creation below

            outer = ctk.CTkFrame(self._job_scroll, fg_color=BG_CARD,
                                  corner_radius=8, cursor="hand2")
            outer.pack(fill="x", pady=2)
            outer.grid_columnconfigure(0, weight=1)

            # Summary row
            summ = ctk.CTkFrame(outer, fg_color="transparent")
            summ.grid(row=0, column=0, sticky="ew", padx=10, pady=6)
            summ.grid_columnconfigure(1, weight=1)

            _sv_icon = ctk.StringVar(value=icon)
            ctk.CTkLabel(summ, textvariable=_sv_icon, font=F("body_b"),
                         text_color=s_col, width=20).grid(row=0,column=0,rowspan=2)
            info = ctk.CTkFrame(summ, fg_color="transparent")
            info.grid(row=0, column=1, sticky="ew", padx=(8,0))
            info.grid_columnconfigure(0, weight=1)
            _sv_title  = ctk.StringVar(value=title)
            _sv_meta   = ctk.StringVar(value=f"{company}  •  {when}" if company else when)
            ctk.CTkLabel(info, textvariable=_sv_title, font=F("label_b"),
                         text_color=FG, anchor="w").grid(row=0,column=0,sticky="w")
            ctk.CTkLabel(info, textvariable=_sv_meta,
                         font=F("small"), text_color=MUTED, anchor="w"
                         ).grid(row=1,column=0,sticky="w")

            rgt = ctk.CTkFrame(summ, fg_color="transparent")
            rgt.grid(row=0, column=2, rowspan=2, padx=(8,0))
            _sv_score = ctk.StringVar(value=f"{score}%" if score else "")
            _sv_score = ctk.StringVar(value=f"{score}%" if score else "")
            ctk.CTkLabel(rgt, textvariable=_sv_score, font=F("label_b"),
                         text_color=bar_col).pack(anchor="e")
            bar_bg = ctk.CTkFrame(rgt, width=80, height=5,
                                   fg_color=BG_FIELD, corner_radius=2)
            bar_bg.pack(pady=(3,0)); bar_bg.pack_propagate(False)
            fill_w = max(2, int(score/100*80))
            _bar_fill = ctk.CTkFrame(bar_bg, width=fill_w, height=5,
                                      fg_color=bar_col, corner_radius=2)
            _bar_fill.place(x=0,y=0)
            # Chevron
            chev_var = ctk.StringVar(value="▾")
            chev_lbl = ctk.CTkLabel(rgt, textvariable=chev_var,
                                     font=F("small"), text_color=MUTED)
            chev_lbl.pack(anchor="e")

            # Detail panel (collapsed by default, expands inside the card)
            det = ctk.CTkFrame(outer, fg_color="transparent")
            # NOT packed yet

            ctk.CTkFrame(det, height=1, fg_color=BG_HOVER
                          ).pack(fill="x", padx=4)
            det_inner = ctk.CTkFrame(det, fg_color="transparent")
            det_inner.pack(fill="x", padx=12, pady=8)

            # Badge
            badge_row = ctk.CTkFrame(det_inner, fg_color="transparent")
            badge_row.pack(anchor="w", pady=(0,6))
            _badge_txt, _badge_col = _badge(job, status, decision, s_col)
            _sv_badge  = ctk.StringVar(value=_badge_txt)
            _sv_badge_col = _badge_col
            _sv_match  = ctk.StringVar(value=f"  Match {score}%{sk_txt}")
            _sv_reason = ctk.StringVar(value=reason)
            ctk.CTkLabel(badge_row, textvariable=_sv_badge,
                         fg_color=_sv_badge_col, corner_radius=4,
                         font=F("small_b"), text_color=BG).pack(side="left")
            ctk.CTkLabel(badge_row, textvariable=_sv_match,
                         font=F("small"), text_color=FG_DIM).pack(side="left")
            ctk.CTkLabel(det_inner, textvariable=_sv_reason,
                         font=F("small"), text_color=FG_SOFT,
                         wraplength=380, justify="left", anchor="w"
                         ).pack(anchor="w")
            # Register StringVars for in-place updates
            self._job_svars.append({
                "icon":     _sv_icon,   "title":    _sv_title,
                "meta":     _sv_meta,   "score":     _sv_score,
                "badge":    _sv_badge,  "match":     _sv_match,
                "reason":   _sv_reason, "bar_fill":  _bar_fill,
                "bar_bg":   bar_bg,
            })

            def _toggle(ev=None, o=outer, d=det, cv=chev_var):
                if d.winfo_ismapped():
                    # collapse — must use grid_remove since placed with grid()
                    d.grid_remove()
                    cv.set("▾")
                    self._job_expanded = None
                else:
                    try:
                        if (self._job_expanded
                                and self._job_expanded is not d
                                and self._job_expanded.winfo_exists()):
                            self._job_expanded.grid_remove()
                    except Exception:
                        pass
                    d.grid(row=1, column=0, sticky="ew")
                    cv.set("▴")
                    self._job_expanded = d

            for w in (outer, summ, info, rgt, chev_lbl):
                w.bind("<Button-1>", _toggle)
            for ch in info.winfo_children() + rgt.winfo_children():
                ch.bind("<Button-1>", _toggle)

    def _refresh_review_btn(self):
        """Enable/disable review button — counts queued + previously applied jobs."""
        try:
            from db.tracker import get_reapply_candidates, get_jobs_ready_to_apply
            from datetime import datetime, timedelta

            # Queued jobs with resume ready — skipped during Phase 3, apply anytime
            queued = get_jobs_ready_to_apply()

            # Previously applied jobs from older runs (> 1 hour ago)
            cutoff = (datetime.now() - timedelta(hours=1)).strftime(
                "%Y-%m-%d %H:%M:%S")
            applied_old = get_reapply_candidates(session_start=cutoff)

            count = len(queued) + len(applied_old)
            if count > 0:
                self._review_btn.configure(
                    state="normal",
                    text="📋  Review Pending (%d)" % count)
            else:
                self._review_btn.configure(
                    state="disabled",
                    text="📋  Review Pending")
        except Exception:
            pass

    def _sched_stats(self):
        """
        Kicks off a background DB read — never blocks the main thread.
        The background thread posts ("stats_data", data) to _q;
        _poll picks it up and calls _apply_stats_data(data).
        """
        active = getattr(self, "_active_tab", 0)
        if self._live or active == 2:
            threading.Thread(target=self._bg_fetch_stats, daemon=True).start()
        interval = 2000 if self._live else 30000
        self._stats_after_id = self.after(interval, self._sched_stats)

    def _bg_fetch_stats(self):
        """Background thread: read DB, post result to queue."""
        try:
            # Counters filter by bot run start — Recent always shows last 12
            bot_since = getattr(self, "_bot_start", None)
            data = _read_stats(since=bot_since)
            self._q.put(("stats_data", data))
        except Exception:
            pass

    def _bg_fetch_history(self):
        """Background thread: read DB for history, post to queue."""
        try:
            filt = self._h_filter.get()
            rows = _read_history(filt)
            self._q.put(("history_data", rows))
        except Exception:
            pass

    # ── History tab ───────────────────────────────────────────────
    # ── History page size ─────────────────────────────────────────
    HIST_PAGE = 20

    def _build_history(self):
        f = self._tabs["history"]
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(0, weight=0)  # header
        f.grid_rowconfigure(1, weight=0)  # filter bar
        f.grid_rowconfigure(2, weight=1)  # scrollable list — gets all space
        f.grid_rowconfigure(3, weight=0)  # pagination bar

        # Header row
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 6))
        ctk.CTkLabel(hdr, text="Job History",
                     font=F("body_b"), text_color=FG).pack(side="left")
        ctk.CTkButton(hdr, text="↻  Refresh", width=100, height=30,
                      font=F("small"), fg_color=BG_CARD, hover_color=BG_HOVER,
                      command=self._load_history).pack(side="right")
        ctk.CTkButton(hdr, text="🗑  Clear All History", width=150, height=30,
                      font=F("small"), fg_color="#5C1010", hover_color=DANGER,
                      command=self._confirm_clear_history).pack(side="right", padx=(0,8))

        # Delete selected button (shown only when matched rows selected)
        self._h_del_btn = ctk.CTkButton(
            hdr, text="🗑  Delete selected", width=150, height=30,
            font=F("small"), fg_color=DANGER, hover_color="#C0392B",
            command=self._h_delete_selected)
        # Not packed until checkboxes are ticked

        # Filter row — scrollable so buttons never clip on narrow windows
        filt_outer = ctk.CTkFrame(f, fg_color="transparent")
        filt_outer.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 6))
        filt_outer.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(filt_outer, text="Filter:", font=F("small"),
                     text_color=FG_DIM).grid(row=0, column=0, padx=(0, 8))
        filt = ctk.CTkFrame(filt_outer, fg_color="transparent")
        filt.grid(row=0, column=1, sticky="ew")
        self._h_filter = ctk.StringVar(value="all")
        for txt, val in [("All","all"),("Applied","applied"),
                          ("Skipped","skipped"),("Failed","failed"),
                          ("Queued","matched"),("Stretch","stretch")]:  # stretch filter
            ctk.CTkRadioButton(
                filt, text=txt, variable=self._h_filter, value=val,
                font=F("small"), command=self._load_history,
            ).pack(side="left", padx=4)

        # Scrollable list — height gives a starting size; expands via grid weight
        self._h_scroll = ctk.CTkScrollableFrame(
            f, fg_color="transparent", corner_radius=0)
        self._h_scroll.grid(row=2, column=0, sticky="nsew", padx=20)
        self._h_scroll.grid_columnconfigure(0, weight=1)

        # Pagination bar
        pg = ctk.CTkFrame(f, fg_color="transparent")
        pg.grid(row=3, column=0, sticky="ew", padx=20, pady=6)
        self._h_prev_btn = ctk.CTkButton(
            pg, text="← Prev", width=80, height=28, font=F("small"),
            fg_color=BG_CARD, hover_color=BG_HOVER,
            command=self._h_prev_page)
        self._h_prev_btn.pack(side="left")
        self._h_page_lbl = ctk.CTkLabel(pg, text="", font=F("small"),
                                          text_color=FG_DIM)
        self._h_page_lbl.pack(side="left", padx=12)
        self._h_next_btn = ctk.CTkButton(
            pg, text="Next →", width=80, height=28, font=F("small"),
            fg_color=BG_CARD, hover_color=BG_HOVER,
            command=self._h_next_page)
        self._h_next_btn.pack(side="left")

        # State
        self._h_all_rows    = []
        self._h_page        = 0
        self._h_expanded    = None
        self._h_check_vars  = {}   # row_id -> BooleanVar (for deletion)
        self._h_del_visible = False

    def _load_history(self):
        """Full reload — only called on filter change, tab open, manual refresh."""
        threading.Thread(target=self._bg_fetch_history, daemon=True).start()

    def _apply_history_data(self, rows: list):
        """Full render from fresh DB data — rebuilds all widgets."""
        self._h_all_rows   = rows
        self._h_page       = 0
        self._h_expanded   = None
        self._h_check_vars = {}
        self._h_row_map    = {}   # id -> {svar_name: StringVar, ...}
        self._h_del_btn.pack_forget()
        self._h_del_visible = False
        self._h_render_page()

    def _h_prev_page(self):
        if self._h_page > 0:
            self._h_page -= 1
            self._h_expanded = None
            self._h_render_page()

    def _h_next_page(self):
        pages = max(1, (len(self._h_all_rows) + self.HIST_PAGE - 1)
                    // self.HIST_PAGE)
        if self._h_page < pages - 1:
            self._h_page += 1
            self._h_expanded = None
            self._h_render_page()

    # ── Real-time incremental update ─────────────────────────────

    def _do_push_latest(self, job: dict):
        """
        Main-thread handler for push_latest queue message.
        Decides whether to update an existing row in-place or prepend a new one.
        Never touches rows that aren't changing — zero flicker.
        """
        filt   = self._h_filter.get()
        job_id = job.get("id")
        status = job.get("status", "")

        # Filter check
        if filt != "all" and status != filt:
            # Job doesn't match current filter — still update data list
            # so switching filter later shows correct data
            for i, r in enumerate(self._h_all_rows):
                if r.get("id") == job_id:
                    self._h_all_rows[i] = job
                    return
            self._h_all_rows.insert(0, job)
            return

        # Does a row for this id already exist?
        if job_id in getattr(self, "_h_row_map", {}):
            # Update StringVars in-place — zero widget recreation
            self._h_update_row_svars(job_id, job)
            # Also update data list
            for i, r in enumerate(self._h_all_rows):
                if r.get("id") == job_id:
                    self._h_all_rows[i] = job
                    break
            return

        # New job — prepend to data list and build one new widget
        self._h_all_rows.insert(0, job)
        if getattr(self, "_active_tab", -1) == 3 and self._h_page == 0:
            self._h_prepend_row(job)
            total = len(self._h_all_rows)
            pages = max(1, (total + self.HIST_PAGE - 1) // self.HIST_PAGE)
            self._h_page_lbl.configure(
                text=f"Page 1 of {pages}  ({total} total)")

    def _h_update_row_svars(self, job_id: int, job: dict):
        """Update the StringVars of an existing row — no widget changes at all."""
        if not hasattr(self, "_h_row_map"):
            return
        svars = self._h_row_map.get(job_id)
        if not svars:
            return

        STATUS_ICON = {"applied":"\u2713","skipped":"\u2212",
                       "failed":"\u2715","matched":"\u25b6"}
        STATUS_COL  = {"applied":SUCCESS,"skipped":WARNING,
                       "failed":DANGER,  "matched":ACCENT}
        DECISION    = {"applied":"Applied","skipped":"Skipped",
                       "failed":"Failed", "matched":"Queued"}
        def _h_badge(row):
            if row.get("status") == "matched" and row.get("stretch"):
                return ("Stretch", STRETCH)
            st = row.get("status","")
            return (DECISION.get(st, st.capitalize()), STATUS_COL.get(st, MUTED))

        score    = job.get("match_score") or 0
        status   = job.get("status", "")
        title    = job.get("job_title") or "Unknown"
        company  = job.get("company") or ""
        overlap  = job.get("skill_overlap") or 0
        when     = (job.get("applied_at") or job.get("logged_at") or "")[:16]
        reason   = job.get("ai_reason") or (
            "Queued \u2014 resume being generated..." if status == "matched"
            else "No reason recorded.")
        decision = DECISION.get(status, status.capitalize())
        sk_txt   = f"  |  Overlap: {overlap}%" if overlap else ""

        # Update every StringVar — Tk propagates changes to widgets automatically
        svars["icon"].set(STATUS_ICON.get(status, "\u2022"))
        svars["title"].set(title)
        svars["meta"].set(f"{company}  \u2022  {when}" if company else when)
        svars["score"].set(f"{score}%" if score else "")
        svars["badge"].set(f"  {decision}  ")
        svars["match"].set(f"  Match {score}%{sk_txt}")
        svars["reason"].set(reason)

        # Update icon + bar colours via widget configure
        # (StringVar can't change text_color — need direct configure)
        try:
            s_col   = STATUS_COL.get(status, MUTED)
            bar_col = SUCCESS if score >= 70 else WARNING if score >= 45 else DANGER
            if "icon_lbl" in svars:
                svars["icon_lbl"].configure(text_color=s_col)
            if "score_lbl" in svars:
                svars["score_lbl"].configure(text_color=bar_col)
            if "badge_lbl" in svars:
                svars["badge_lbl"].configure(fg_color=s_col)
            if "bar_fill" in svars:
                w = max(2, int(score / 100 * 70))
                svars["bar_fill"].configure(width=w, fg_color=bar_col)
        except Exception:
            pass

    def _h_prepend_row(self, job: dict):
        """Build one new row widget and insert it at the top of the scroll frame."""
        widget = self._h_make_row(job)
        if not widget:
            return
        children = self._h_scroll.winfo_children()
        if children:
            widget.pack(fill="x", pady=2, before=children[0])
        else:
            widget.pack(fill="x", pady=2)

    def _h_render_page(self):
        """Full page render — only called on initial load, filter change, page nav."""
        for w in self._h_scroll.winfo_children():
            w.destroy()
        self._h_expanded = None
        if not hasattr(self, "_h_row_map"):
            self._h_row_map = {}
        self._h_row_map.clear()

        rows  = self._h_all_rows
        total = len(rows)
        if not total:
            msg = ("No queued jobs — run the bot to scan and queue new jobs."
                   if self._h_filter.get() == "matched"
                   else "No records match this filter.")
            ctk.CTkLabel(self._h_scroll, text=msg,
                         font=F("body"), text_color=MUTED,
                         wraplength=400, justify="center").pack(pady=40)
            self._h_page_lbl.configure(text="")
            self._h_prev_btn.configure(state="disabled")
            self._h_next_btn.configure(state="disabled")
            return

        PAGE  = self.HIST_PAGE
        pages = max(1, (total + PAGE - 1) // PAGE)
        pg    = max(0, min(self._h_page, pages - 1))
        self._h_page = pg
        page_rows = rows[pg * PAGE : (pg + 1) * PAGE]

        self._h_page_lbl.configure(
            text=f"Page {pg+1} of {pages}  ({total} total)")
        self._h_prev_btn.configure(state="normal" if pg > 0 else "disabled")
        self._h_next_btn.configure(
            state="normal" if pg < pages - 1 else "disabled")

        for job in page_rows:
            w = self._h_make_row(job)
            if w:
                w.pack(fill="x", pady=2)

    def _h_make_row(self, job: dict):
        """
        Build one accordion row. Stores StringVars and widget refs into
        self._h_row_map[job_id] so the row can be updated in-place later
        without any widget recreation.
        """
        STATUS_ICON = {"applied":"\u2713","skipped":"\u2212",
                       "failed":"\u2715","matched":"\u25b6"}
        STATUS_COL  = {"applied":SUCCESS,"skipped":WARNING,
                       "failed":DANGER,  "matched":ACCENT}
        DECISION    = {"applied":"Applied","skipped":"Skipped",
                       "failed":"Failed", "matched":"Queued"}
        def _h_badge(row):
            if row.get("status") == "matched" and row.get("stretch"):
                return ("Stretch", STRETCH)
            st = row.get("status","")
            return (DECISION.get(st, st.capitalize()), STATUS_COL.get(st, MUTED))

        score     = job.get("match_score") or 0
        status    = job.get("status","")
        title     = job.get("job_title") or "Unknown"
        company   = job.get("company") or ""
        reason    = job.get("ai_reason") or (
            "Queued \u2014 resume being generated..." if status == "matched"
            else "No reason recorded.")
        overlap   = job.get("skill_overlap") or 0
        when      = (job.get("applied_at") or job.get("logged_at") or "")[:16]
        row_id    = job.get("id")
        decision  = DECISION.get(status, status.capitalize())
        s_col     = STATUS_COL.get(status, MUTED)
        bar_col   = SUCCESS if score >= 70 else WARNING if score >= 45 else DANGER
        icon      = STATUS_ICON.get(status, "\u2022")
        sk_txt    = f"  |  Overlap: {overlap}%" if overlap else ""
        is_queued = status == "matched"

        # ── StringVars — all changing fields ─────────────────────
        sv_icon   = ctk.StringVar(value=icon)
        sv_title  = ctk.StringVar(value=title)
        sv_meta   = ctk.StringVar(value=f"{company}  \u2022  {when}" if company else when)
        sv_score  = ctk.StringVar(value=f"{score}%" if score else "")
        sv_badge  = ctk.StringVar(value=f"  {decision}  ")
        sv_match  = ctk.StringVar(value=f"  Match {score}%{sk_txt}")
        sv_reason = ctk.StringVar(value=reason)
        sv_chev   = ctk.StringVar(value="\u25be")

        # Outer card
        outer = ctk.CTkFrame(self._h_scroll, fg_color=BG_CARD, corner_radius=8)
        outer.grid_columnconfigure(0, weight=1)

        # Summary row — fixed 4 columns
        summ = ctk.CTkFrame(outer, fg_color="transparent", cursor="hand2")
        summ.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
        summ.grid_columnconfigure(0, minsize=24)
        summ.grid_columnconfigure(1, minsize=28)
        summ.grid_columnconfigure(2, weight=1)
        summ.grid_columnconfigure(3, minsize=90)

        # Col 0: checkbox or spacer
        if is_queued and row_id is not None:
            chk_var = ctk.BooleanVar(value=False)
            self._h_check_vars[row_id] = chk_var
            ctk.CTkCheckBox(summ, text="", variable=chk_var, width=20,
                             checkbox_width=18, checkbox_height=18,
                             command=self._h_check_changed
                             ).grid(row=0, column=0, rowspan=2, sticky="w")
        else:
            ctk.CTkFrame(summ, width=20, height=1,
                          fg_color="transparent").grid(row=0, column=0, rowspan=2)

        # Col 1: status icon — stores widget ref for colour updates
        icon_lbl = ctk.CTkLabel(summ, textvariable=sv_icon, font=F("icon"),
                                  text_color=s_col, width=24, anchor="center")
        icon_lbl.grid(row=0, column=1, rowspan=2, sticky="w")

        # Col 2: title + meta
        info = ctk.CTkFrame(summ, fg_color="transparent")
        info.grid(row=0, column=2, sticky="ew", padx=(6, 0), rowspan=2)
        info.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(info, textvariable=sv_title, font=F("label_b"),
                     text_color=FG, anchor="w"
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(info, textvariable=sv_meta, font=F("tiny"),
                     text_color=MUTED, anchor="w"
                     ).grid(row=1, column=0, sticky="w")

        # Col 3: score + bar + chevron
        rgt = ctk.CTkFrame(summ, fg_color="transparent")
        rgt.grid(row=0, column=3, rowspan=2, padx=(8, 0), sticky="e")
        score_lbl = ctk.CTkLabel(rgt, textvariable=sv_score, font=F("label_b"),
                                   text_color=bar_col)
        score_lbl.pack(anchor="e")
        bar_bg = ctk.CTkFrame(rgt, width=70, height=5,
                               fg_color=BG_FIELD, corner_radius=2)
        bar_bg.pack(pady=(2, 0)); bar_bg.pack_propagate(False)
        bar_fill = ctk.CTkFrame(bar_bg,
                                 width=max(2, int(score / 100 * 70)),
                                 height=5, fg_color=bar_col, corner_radius=2)
        bar_fill.place(x=0, y=0)
        ctk.CTkLabel(rgt, textvariable=sv_chev, font=F("small"),
                     text_color=MUTED).pack(anchor="e")

        # Detail panel
        det = ctk.CTkFrame(outer, fg_color="transparent")
        ctk.CTkFrame(det, height=1, fg_color=BG_HOVER
                     ).pack(fill="x", padx=8, pady=(0, 4))
        det_inner = ctk.CTkFrame(det, fg_color="transparent")
        det_inner.pack(fill="x", padx=14, pady=(0, 10))
        badge_row = ctk.CTkFrame(det_inner, fg_color="transparent")
        badge_row.pack(anchor="w", pady=(0, 6))
        badge_lbl = ctk.CTkLabel(badge_row, textvariable=sv_badge,
                                   fg_color=s_col, corner_radius=4,
                                   font=F("small"), text_color=BG)
        badge_lbl.pack(side="left")
        ctk.CTkLabel(badge_row, textvariable=sv_match,
                      font=F("small"), text_color=FG_DIM).pack(side="left")
        ctk.CTkLabel(det_inner, textvariable=sv_reason, font=F("small"),
                      text_color=FG_SOFT, wraplength=500,
                      justify="left", anchor="w").pack(anchor="w")

        # Generate Resume button — shown for matched jobs without a resume
        if is_queued:
            gen_row = ctk.CTkFrame(det_inner, fg_color="transparent")
            gen_row.pack(anchor="w", pady=(8,0))
            ctk.CTkButton(
                gen_row,
                text="Generate Resume",
                width=140, height=28,
                font=F("small"),
                fg_color=ACCENT, hover_color=ACCENT_HV,
                command=lambda rid=row_id, t=title, c=company: (
                    self._manual_generate_resume(rid, t, c))
            ).pack(side="left")
            ctk.CTkLabel(gen_row,
                text="  Resume not yet generated for this job",
                font=F("small"), text_color=MUTED).pack(side="left")

        # Toggle accordion
        def _toggle(ev=None, d=det, cv=sv_chev):
            if d.winfo_ismapped():
                d.grid_forget(); cv.set("\u25be")
            else:
                try:
                    if (self._h_expanded is not None
                            and self._h_expanded is not d
                            and self._h_expanded.winfo_exists()):
                        self._h_expanded.grid_forget()
                except Exception:
                    pass
                d.grid(row=1, column=0, sticky="ew")
                cv.set("\u25b4")
                self._h_expanded = d

        for w in (outer, summ, info, rgt):
            w.bind("<Button-1>", _toggle)
        for ch in list(info.winfo_children()) + list(rgt.winfo_children()):
            ch.bind("<Button-1>", _toggle)

        # ── Register in row map for in-place updates ──────────────
        if row_id is not None:
            if not hasattr(self, "_h_row_map"):
                self._h_row_map = {}
            self._h_row_map[row_id] = {
                "icon":      sv_icon,   "title":  sv_title,
                "meta":      sv_meta,   "score":  sv_score,
                "badge":     sv_badge,  "match":  sv_match,
                "reason":    sv_reason,
                "icon_lbl":  icon_lbl,  "score_lbl": score_lbl,
                "badge_lbl": badge_lbl, "bar_fill":  bar_fill,
            }
        return outer

    def _h_check_changed(self):
        """Show/hide delete button based on whether any queued rows are checked."""
        any_checked = any(v.get() for v in self._h_check_vars.values())
        if any_checked and not self._h_del_visible:
            self._h_del_btn.pack(side="right", padx=(0, 8))
            self._h_del_visible = True
        elif not any_checked and self._h_del_visible:
            self._h_del_btn.pack_forget()
            self._h_del_visible = False

    def _confirm_clear_history(self):
        """Show confirmation dialog before wiping all job history."""
        import customtkinter as ctk

        popup = ctk.CTkToplevel(self)
        popup.title("Clear All History")
        popup.geometry("440x260")
        popup.resizable(False, False)
        popup.grab_set()

        ctk.CTkLabel(popup,
            text="Clear All Job History?",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#FF6B6B").pack(pady=(24,8))

        ctk.CTkLabel(popup,
            text="This permanently deletes ALL job records from the database.\n"
                 "Applied jobs, resumes queued, history — everything gone.\n\n"
                 "The generated DOCX/PDF files on disk are NOT deleted.",
            font=ctk.CTkFont(size=12),
            text_color="#AAAAAA",
            justify="center").pack(pady=(0,20))

        btn_row = ctk.CTkFrame(popup, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(0,20))

        ctk.CTkButton(btn_row, text="Cancel",
            height=38, fg_color="#3A3A3A", hover_color="#4A4A4A",
            font=ctk.CTkFont(size=12),
            command=popup.destroy).pack(side="left", expand=True, fill="x", padx=(0,8))

        def _confirm():
            popup.destroy()
            self._do_clear_history()

        ctk.CTkButton(btn_row, text="Yes, Clear Everything",
            height=38, fg_color=DANGER, hover_color="#C0392B",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_confirm).pack(side="left", expand=True, fill="x")

    def _do_clear_history(self):
        """Wipe all rows from applications DB and refresh UI."""
        try:
            import db.tracker as tracker
            deleted = tracker.clear_all_history()
            self._set_status("History cleared — %d records deleted" % deleted)
            self._load_history()
            self._force_stats_refresh()
        except Exception as e:
            messagebox.showerror("Clear Failed",
                "Could not clear history:\n%s" % e)

    def _h_delete_selected(self):
        """Delete checked queued jobs from DB and filesystem."""
        to_delete = [rid for rid, var in self._h_check_vars.items() if var.get()]
        if not to_delete:
            return
        import tracker as _tr
        _tr.delete_jobs(to_delete)
        self._h_check_vars  = {}
        self._h_del_visible = False
        self._h_del_btn.pack_forget()
        self._load_history()
        self._stats_last_hash = None
        self._refresh_stats()

    def _show_action_bar(self, kind: str):
        for w in self._action_bar.winfo_children():
            w.destroy()

        configs = {
            "dsq": ("Did you apply to this job?",
                    "Review the job in the browser, then respond.",
                    [("Applied — Finish" if getattr(self,"_is_last_job",False)
                      else "Applied — Next job","d",SUCCESS),
                     ("Skip this one","s",BG_CARD),
                     ("Quit applying","q",DANGER)]),
            "nf": ("How long to skip?", "",
                   [("Ask me next run","n",BG_CARD),
                    ("Never show again","f",DANGER)]),
            "yn_apply": ("Resumes are ready!",
                         "Tailored resumes generated. Start applying now?",
                         [("Yes, start applying","y",SUCCESS),
                          ("Not now — I'll review later","n",BG_CARD)]),

        }
        headline, sub, buttons = configs.get(kind, ("","",""))
        if headline:
            self._attention_hl.configure(text=headline)
            self._attention_sub.configure(text=sub)
            self._attention_card.place(relx=0.5, rely=0.65, anchor="center",
                                        relwidth=0.85)

        btn_row = ctk.CTkFrame(self._action_bar, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=10)
        for txt, val, col in buttons:
            ctk.CTkButton(btn_row, text=txt,
                          fg_color=col,
                          hover_color=SUCCESS if col==SUCCESS else DANGER if col==DANGER else BG_HOVER,
                          command=lambda v=val, k=kind: self._answer(v, k),
                          height=36, font=F("label")
                          ).pack(side="left", padx=(0,8))

        self._action_bar.grid(row=2, column=0, sticky="ew",
                               padx=20, pady=(0,8))
        self._nav(0)

    def _manual_generate_resume(self, job_id: int, title: str, company: str):
        """
        Manually trigger resume generation for a specific previously matched job.
        Uses the same BotRunner as normal runs — ensures API key, logging,
        and output all work identically.
        """
        if not messagebox.askyesno(
            "Generate Resume",
            "Generate a tailored resume for:\n\n"
            "%s @ %s\n\n"
            "Uses your current profile and the stored job description." % (title, company)):
            return

        api_key = self._api_var.get().strip() if hasattr(self, "_api_var") else ""
        if not api_key:
            messagebox.showerror("No API Key",
                "Enter your Anthropic API key in Settings first.")
            return

        # Use --phase2-only --job-ids N  — same subprocess mechanism as normal run
        # BotRunner sets ANTHROPIC_API_KEY, captures stdout to GUI and bot.log
        args = ["--phase2-only",
                "--gui",
                "--job-ids", str(job_id)]

        self._set_status("Generating resume: %s @ %s..." % (title[:40], company[:30]))
        self._set_phase("Phase 2 — Generating resume...")

        def _on_done(code):
            if code == 0:
                self.after(0, lambda: self._set_status(
                    "Resume ready: %s @ %s" % (title[:40], company[:30])))
                self.after(0, self._load_history)
                self.after(0, self._force_stats_refresh)
            else:
                self.after(0, lambda: messagebox.showerror(
                    "Generation Failed",
                    "Resume generation failed for %s @ %s.\n"
                    "Check the log file for details."
                    % (title, company)))

        runner = BotRunner(api_key, args, self._on_bot_line, _on_done)
        runner.start()
        log_msg = "Manual resume generation started: %s @ %s (id=%d)" % (title, company, job_id)
        self._run_sub_lbl.configure(text=log_msg) if hasattr(self, "_run_sub_lbl") else None

    def _show_metric_collection_popup(self, missing: list,
                                       on_continue=None, on_skip=None):
        """Before bot run: collect real metrics from user. Saves to user_metrics.json."""
        import customtkinter as ctk
        import json as _json, os as _os

        popup = ctk.CTkToplevel(self)
        popup.title("Add Real Numbers - Improve Resume Quality")
        popup.geometry("680x600")
        popup.resizable(False, True)
        popup.grab_set()

        ctk.CTkLabel(popup,
            text="Your profile is missing real numbers",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#FFD580").pack(pady=(20,4))

        desc = (
            "Without real metrics, Claude estimates numbers like 20% improvement. "
            "If a recruiter asks in an interview, you won't be able to back it up. "
            "Fill in what you remember — rough numbers are fine."
        )
        ctk.CTkLabel(popup, text=desc,
            font=ctk.CTkFont(size=12), text_color="#AAAAAA",
            wraplength=620, justify="center").pack(pady=(0,12))

        scroll = ctk.CTkScrollableFrame(popup, height=380, fg_color="#1A1A1A")
        scroll.pack(fill="x", padx=20)

        questions = [
            ("scale",  "Scale / Volume",     "e.g. 10,000 records, 5M transactions"),
            ("speed",  "Speed / Time saved", "e.g. cut from 2 min to 30 sec, 3x faster"),
            ("impact", "Impact / Result",    "e.g. 100% success rate, zero downtime"),
            ("team",   "Team / Scope",        "e.g. 9-person team, 3 microservices"),
        ]
        entries = {}

        for job in missing:
            company = job.get("company","")
            title   = job.get("title","")
            jf = ctk.CTkFrame(scroll, fg_color="#242424", corner_radius=8)
            jf.pack(fill="x", padx=4, pady=(8,2))
            ctk.CTkLabel(jf, text="%s  @  %s" % (title, company),
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#FFFFFF").pack(anchor="w", padx=12, pady=(10,2))
            sample = job.get("sample","")[:70]
            ctk.CTkLabel(jf, text="Sample: %s..." % sample,
                font=ctk.CTkFont(size=11), text_color="#666666").pack(
                anchor="w", padx=12, pady=(0,6))
            entries[company] = {}
            for key, label, placeholder in questions:
                row = ctk.CTkFrame(jf, fg_color="transparent")
                row.pack(fill="x", padx=12, pady=2)
                ctk.CTkLabel(row, text=label + ":",
                    font=ctk.CTkFont(size=11), text_color="#AAAAAA",
                    width=130, anchor="w").pack(side="left")
                e = ctk.CTkEntry(row, placeholder_text=placeholder,
                    font=ctk.CTkFont(size=11), height=30)
                e.pack(side="left", fill="x", expand=True, padx=(4,0))
                entries[company][key] = e

        def _save_and_go():
            user_metrics = {}
            for company, fields in entries.items():
                parts = ["%s: %s" % (lbl, fields[k].get().strip())
                         for k, lbl, _ in questions if fields[k].get().strip()]
                if parts:
                    user_metrics[company] = ". ".join(parts)
            try:
                from core.settings import get_resume_data_path as _grp3
                xml = _grp3()
                out = _os.path.join(_os.path.dirname(xml),
                                    "user_metrics.json") if xml else "user_metrics.json"
                with open(out, "w") as f:
                    _json.dump(user_metrics, f, indent=2)
            except Exception:
                pass
            popup.destroy()
            if on_continue: on_continue()

        def _skip():
            popup.destroy()
            if on_skip: on_skip()

        btn_row = ctk.CTkFrame(popup, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=16)
        ctk.CTkButton(btn_row, text="Skip - Use AI estimates",
            height=38, fg_color="#3A3A3A", hover_color="#4A4A4A",
            font=ctk.CTkFont(size=12),
            command=_skip).pack(side="left", expand=True, fill="x", padx=(0,8))
        ctk.CTkButton(btn_row, text="Save Numbers & Start Run",
            height=38, fg_color=ACCENT, hover_color=ACCENT_HV,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_save_and_go).pack(side="left", expand=True, fill="x")

    def _show_metric_warning(self, companies: str, count: int):
        """
        Show a non-blocking banner warning that some jobs lack metrics.
        Claude needs real numbers to avoid fabricating them.
        """
        msg = (
            "Tip: %d job(s) (%s) have no measurable metrics in their bullets.\n"
            "Add numbers to your resume XML (scale, % improvement, team size) so\n"
            "Claude uses real data instead of estimating. Real > estimated always."
            % (count, companies)
        )
        try:
            import customtkinter as ctk
            banner = ctk.CTkFrame(self, fg_color="#7C4F00", corner_radius=6)
            banner.pack(fill="x", padx=16, pady=(4,0))
            ctk.CTkLabel(banner, text="⚠  " + msg,
                         font=ctk.CTkFont(size=11),
                         text_color="#FFD580",
                         wraplength=700,
                         justify="left").pack(padx=12, pady=6, anchor="w")
            ctk.CTkButton(banner, text="Dismiss", width=70, height=24,
                          fg_color="#A0620A",
                          command=banner.destroy).pack(padx=12, pady=(0,6), anchor="e")
        except Exception:
            pass

    def _hide_action_bar(self):
        self._action_bar.grid_forget()
        self._attention_card.place_forget()
        self._is_last_job = False

    def _answer(self, val: str, kind: str = ""):
        if self._runner: self._runner.send(val)
        self._hide_action_bar()
        # "Not now" on apply prompt → go to Stats so user sees Review button
        if val == "n" and kind == "yn_apply":
            self.after(300, lambda: (
                self._nav(4),           # switch to Stats tab (index 4)
                self._refresh_review_btn()
            ))

    # ── Run controls ───────────────────────────────────────────────
    def _start(self):
        selected = [r for v,r in self._role_vars if v.get()]
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

    def _launch_bot(self, selected: list):
        """Actually start the bot subprocess after metric check."""
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
        if self._runner:
            # Send a line to stdin so main.py's readline() unblocks
            # and closes the browser before the process is terminated
            self._runner.send("stop")
            self.after(500, self._force_stop)   # give 500ms then hard-terminate
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

    def _on_bot_done(self, code: int):
        self._q.put(("done", code))

    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "line":           self._handle_line(data)
                elif kind == "done":         self._handle_done(data)
                elif kind == "s3_status":    self._s3_status.configure(text=data)
                elif kind == "roles_ready":  self._populate_roles(data)
                elif kind == "roles_error":
                    self._s3_err.configure(text=f"Error: {data}")
                    self._show_step(1)
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

        if _ERROR_RE.search(line) and not s.startswith("[WARN]"):
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

    def _h_push_latest(self):
        """Fetch latest job row on a background thread and push to history."""
        threading.Thread(target=self._bg_push_latest, daemon=True).start()

    def _bg_push_latest(self):
        try:
            db = _db_path()
            if not os.path.exists(db): return
            with sqlite3.connect(db, timeout=3) as c:
                c.execute("PRAGMA journal_mode=WAL")
                c.row_factory = sqlite3.Row
                row = c.execute(
                    "SELECT id,job_title,company,status,match_score,"
                    "skill_overlap,ai_reason,applied_at,logged_at "
                    "FROM applications ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
            if not row: return
            job = dict(row)
            self._q.put(("push_latest", job))
        except Exception:
            pass

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
        if self._err_count == 1:
            self._nav(1)

    def _handle_done(self, code: int):
        # If we're mid-restart, the old runner exiting is expected — ignore it
        if getattr(self, "_restarting", False):
            return
        self._live = False
        self._live_dot.configure(text_color=MUTED)
        self._stop_btn.pack_forget()
        self._hide_action_bar()
        self._act_strip.grid_forget()
        self._act_strip_visible = False
        ok = code == 0
        self._set_phase("Run complete" if ok else "Ended with errors")
        self._set_status(f"Finished (exit {code}). {self._err_count} issue(s).")
        self._stats_last_hash = None
        self._refresh_stats()
        self._show_step(3)
        if self._err_count == 0: self._nav(2)
        else: self._nav(1)

    # ── Helpers ────────────────────────────────────────────────────
    def _set_status(self, t: str):
        self._status_var.set(t)

    def _set_phase(self, t: str):
        self._phase_lbl.configure(text=t)

    # ── Settings tab ─────────────────────────────────────────────
    def _build_settings(self):
        f = self._tabs["settings"]
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
        entry = path_entry or getattr(self, "_chrome_path_entry", None)
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
        Resolve resume_data.xml path.

        When running as a PyInstaller exe, __file__ points inside the
        read-only _internal/ temp folder. User data must go somewhere
        writable and persistent instead.

        Priority order:
        1. local_settings.json → resume_data_path  (explicit saved path)
        2. Exe sibling data/ folder                 (next to the .exe)
        3. ~/resuto/                     (user home fallback)
        4. ~/.resume_automation/                    (legacy fallback)
        """
        # Detect if running as a PyInstaller bundle
        frozen = getattr(sys, "frozen", False)

        # ── 1. Check local_settings.json first ──────────────────
        try:
            if frozen:
                # Settings file lives next to the .exe
                settings_p = Path(sys.executable).parent / "local_settings.json"
            else:
                settings_p = _settings_file()

            if settings_p.exists():
                import json as _j
                stored = _j.loads(
                    settings_p.read_text(encoding="utf-8")
                ).get("resume_data_path", "")
                if stored and Path(stored).is_file():
                    return stored
        except Exception:
            pass

        # ── 2. Preferred writable location ───────────────────────
        if frozen:
            # Next to the .exe: dist/resuto/data/resume_data.xml
            preferred = Path(sys.executable).parent / "data" / "resume_data.xml"
        else:
            preferred = Path(__file__).parent.parent / "data" / "resume_data.xml"

        if preferred.exists():
            return str(preferred)

        # ── 3. Home directory fallbacks ──────────────────────────
        home_new = Path.home() / "resuto" / "resume_data.xml"
        home_old = Path.home() / ".resume_automation" / "resume_data.xml"

        if home_new.exists():
            return str(home_new)
        if home_old.exists():
            return str(home_old)

        # ── Nothing found — return preferred and ensure folder exists ─
        try:
            preferred.parent.mkdir(parents=True, exist_ok=True)
            return str(preferred)
        except OSError:
            home_new.parent.mkdir(parents=True, exist_ok=True)
            return str(home_new)

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
            log_warn("Could not save visa status: %s" % e)

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
            log_warn("Could not update XML work_authorization: %s" % e)

        # 3. Update config at runtime so blocking phrases adjust immediately
        try:
            import core.config as _cfg
            needs_no_sponsorship_check = code in ("US Citizen", "Green Card", "H-1B")
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
        try:
            from core.settings import get_settings
            d = get_settings()
            return str(Path(d.get("output_dir","output")) / "job_prefs.json")
        except Exception:
            return str(Path(__file__).parent.parent / "frontend" / "job_prefs.json")

    def _load_job_prefs(self) -> dict:
        try:
            p = Path(self._prefs_path())
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        # Sensible defaults — generic, not CPT-specific
        return {
            "experience_levels": ["entry","associate","mid_senior"],
            "job_types":         ["full_time","contract"],
            "workplace":         ["on_site","remote","hybrid"],
            "easy_apply_only":   True,
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
        self._update_stats()

# ══════════════════════════════════════════════════════════════════
# IntakeWindow — modal Toplevel that walks the user through resume
# intake questions one by one, then generates resume_data.xml.
# ══════════════════════════════════════════════════════════════════

class IntakeWindow(ctk.CTkToplevel):
    """
    Resume intake as a natural chat conversation.
    Claude reads the resume, asks follow-up questions one by one
    in a friendly conversational tone, then builds resume_data.xml.

    Flow:
      1. Extract text from PDF/DOCX
      2. Claude sends an opening message + first question
      3. User replies in the chat box — feels like texting
      4. Claude follows up naturally based on each answer
      5. When enough info is gathered, Claude builds the XML
    """

    BOT_NAME  = "Alex"
    MAX_TURNS = 60   # longer conversation — thoroughness over speed

    def _system_prompt(self) -> str:
        return f"""You are {self.BOT_NAME}, a resume coach and career advisor having a real conversation with someone to help them build the strongest possible resume from their existing experience.

You have already read their resume. Your job is NOT to run through a checklist of questions — it is to have a genuine back-and-forth conversation, understand their work deeply, and help them articulate their experience better than they currently do on paper.

━━━ WHO YOU ARE ━━━
You are warm, direct, and a little witty — like a smart friend who happens to know a lot about hiring. You:
- Explain WHY something matters for a resume when the person seems unsure
- Use plain conversational language, never corporate speak
- Occasionally make a light joke or use an emoji — natural, not forced
- React genuinely to what people tell you ("Oh that's actually really impressive — most people don't think to mention that")
- If someone gives a vague answer, you explain WHY specifics help and ask again in a different way — never just repeat the question
- If someone doesn't understand what you're asking, you step back and explain it differently with an example

━━━ YOUR GOAL ━━━
Build a complete, enhanced picture of this person's professional experience so their resume becomes as strong as possible. This means going deeper than what's already written.

For EVERY job they've had, you want to understand:
- What the team/project/product actually was — was "CKYC" a team name? An internal tool? A client-facing app? Don't assume
- The real scale: how many users, records, requests, transactions? Even rough numbers help
- The specific problems THEY solved — not just what the company did
- What changed because of their work — faster, cheaper, more reliable, fewer errors?
- What they built vs. what they maintained vs. what they improved
- Technologies they actually used hands-on, not just ones they know in theory

For PROJECTS, you want:
- What problem it actually solved (not just what it does)
- Who used it and whether it was used in production or just a demo
- Any measurable outcomes — accuracy, time saved, scale

For SKILLS:
- What they use regularly vs. what they've tried once
- Anything they're proud of that isn't listed

━━━ HOW TO HAVE THE CONVERSATION ━━━
- One topic at a time — don't bombard them with multiple questions
- Acknowledge and react to each answer before moving on
- If an answer reveals something interesting, dig into it before changing topic
- Don't follow a script — go where the conversation naturally takes you
- If they mention something in passing that sounds important, catch it: "Wait — you said it processed 60K images. Was that in production? Because that's actually a strong number worth highlighting"
- Match their energy — if they're being casual and chatty, be casual back; if they're being brief, don't lecture them

━━━ WHAT NOT TO DO ━━━
- Do NOT ask about target job roles, visa status, salary, or availability
- Do NOT ask about things already clearly stated in the resume
- Do NOT rush — thoroughness matters more than speed
- Do NOT repeat a question in the same words if they didn't understand it
- Do NOT move on if an answer is too vague to be useful — gently push

━━━ OPENING MESSAGE (very first reply only) ━━━
Your opening message should:
1. Greet them by name (from the resume)
2. Say something genuinely specific and encouraging about their background — not generic praise
3. Set honest expectations: this conversation will take around 30 minutes (maybe more if they have a lot to cover), and it's worth it — a rushed resume shows, a thorough one gets interviews
4. Start with one warm opening question to get the conversation going

━━━ WHEN YOU'RE DONE ━━━
Only signal you're ready to generate when you genuinely feel you have a thorough understanding of their work — not just surface-level info. This usually takes 20-40 exchanges for a professional with 2+ years of experience.

When you're satisfied, wrap up warmly and end your message with the exact token [[READY]] on its own — the system uses this to trigger profile generation. Do NOT mention or explain this token.

Good wrap-up: "Alright, I think I've got a really solid picture of everything you've done — this is going to make a strong resume. Give me a moment to put it all together! [[READY]]"

━━━ RESPONSE LENGTH ━━━
Keep responses conversational — 2-5 sentences is usually right. Longer if you're explaining something. Never bullet-point your messages; this is a chat, not a report."""

    def __init__(self, parent, resume_path: str, api_key: str,
                 xml_out: str, on_done=None):
        super().__init__(parent)
        self.title("Resume Chat")
        self.geometry("760x600")
        self.resizable(True, True)
        self.minsize(620, 520)
        self.grab_set()

        self._resume_path = resume_path
        self._api_key     = api_key
        self._xml_out     = xml_out
        self._on_done     = on_done
        self._resume_text = ""
        self._history     = []   # [{role, content}] for Claude API
        self._turn        = 0
        self._done        = False

        self._build()
        self._start_extraction()

    # ── UI ────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=52)
        hdr.grid(row=0, column=0, sticky="ew"); hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text=f"💬  Chat with {self.BOT_NAME}",
                     font=F("heading"), text_color=FG).pack(side="left", padx=20)
        self._status_lbl = ctk.CTkLabel(hdr, text="Reading your resume...",
                                         font=F("small"), text_color=FG_DIM)
        self._status_lbl.pack(side="right", padx=16)

        # Chat area (scrollable)
        self._chat_scroll = ctk.CTkScrollableFrame(
            self, fg_color=BG, corner_radius=0)
        self._chat_scroll.grid(row=1, column=0, sticky="nsew")
        self._chat_scroll.grid_columnconfigure(0, weight=1)

        # Input area
        inp = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=90)
        inp.grid(row=2, column=0, sticky="ew"); inp.grid_propagate(False)
        inp.grid_columnconfigure(0, weight=1)

        self._input = ctk.CTkTextbox(inp, height=52, font=F("body"),
                                      fg_color=BG_FIELD, text_color=FG,
                                      corner_radius=8)
        self._input.grid(row=0, column=0, sticky="ew", padx=(14, 8), pady=18)
        self._input.bind("<Return>",       self._on_enter)
        self._input.bind("<Shift-Return>", lambda e: None)  # allow newline

        self._send_btn = ctk.CTkButton(
            inp, text="Send  ↵", width=100, height=52,
            font=F("label_b"), corner_radius=8,
            command=self._send, state="disabled")
        self._send_btn.grid(row=0, column=1, padx=(0, 14), pady=18)

    def _on_enter(self, event):
        # Enter sends, Shift+Enter inserts newline
        if not (event.state & 0x1):   # Shift not held
            self._send()
            return "break"

    # ── Chat rendering ────────────────────────────────────────────

    def _add_bot_message(self, text: str):
        """Render a bot message bubble on the left."""
        row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(6, 2))
        row.grid_columnconfigure(1, weight=1)

        # Avatar
        ctk.CTkLabel(row, text="🤖", font=F("body"),
                     width=32).grid(row=0, column=0, sticky="nw", padx=(0, 8))

        bubble = ctk.CTkFrame(row, fg_color=BG_CARD, corner_radius=12)
        bubble.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(bubble, text=text, font=F("body"),
                     text_color=FG, wraplength=460,
                     justify="left", anchor="w"
                     ).pack(padx=14, pady=10)
        self._scroll_to_bottom()

    def _add_user_message(self, text: str):
        """Render a user message bubble on the right."""
        row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(2, 6))
        row.grid_columnconfigure(0, weight=1)

        bubble = ctk.CTkFrame(row, fg_color=ACCENT, corner_radius=12)
        bubble.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(bubble, text=text, font=F("body"),
                     text_color="white", wraplength=420,
                     justify="left", anchor="w"
                     ).pack(padx=14, pady=10)
        self._scroll_to_bottom()

    def _add_typing_indicator(self) -> ctk.CTkFrame:
        """Show '...' while bot is thinking. Returns the frame to remove later."""
        row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(4, 2), anchor="w")
        ctk.CTkLabel(row, text="🤖", font=F("body"), width=32).pack(side="left")
        bubble = ctk.CTkFrame(row, fg_color=BG_CARD, corner_radius=12)
        bubble.pack(side="left")
        ctk.CTkLabel(bubble, text="  ...  ", font=F("body"),
                     text_color=MUTED).pack(padx=14, pady=10)
        self._scroll_to_bottom()
        return row

    def _remove_typing(self, indicator):
        try: indicator.destroy()
        except Exception: pass

    def _scroll_to_bottom(self):
        self.after(50, lambda: self._chat_scroll._parent_canvas.yview_moveto(1.0))

    # ── Extraction ────────────────────────────────────────────────

    def _start_extraction(self):
        self._status_lbl.configure(text="Reading your resume...")
        threading.Thread(target=self._extract_text, daemon=True).start()

    def _extract_text(self):
        try:
            try:
                from api.intake import read_resume_file
            except ModuleNotFoundError:
                from api.intake import read_resume_file
            text = read_resume_file(self._resume_path)
            if not text or not text.strip():
                self.after(0, lambda: self._show_error(
                    "Couldn't extract text from your file.\n\n"
                    "Make sure it's a readable PDF or DOCX — "
                    "scanned image-only PDFs won't work."))
                return
            self._resume_text = text
            self.after(0, self._show_choice)
        except Exception as e:
            _e = str(e); self.after(0, lambda _e=_e: self._show_error(f"Could not read file:\n{_e}"))

    # ── Choice screen ────────────────────────────────────────────

    def _show_choice(self):
        """
        Show two options after PDF extraction:
          Use as-is   → Claude builds XML directly from PDF (no chat)
          Enhance     → Chat with Alex for a richer XML
        """
        self.title("How would you like to proceed?")

        # Build choice screen — overlay on the existing window
        self._choice_frame = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self._choice_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        inner = ctk.CTkFrame(self._choice_frame, fg_color=BG_CARD,
                              corner_radius=14)
        inner.place(relx=0.5, rely=0.5, anchor="center",
                    relwidth=0.88, relheight=0.85)
        inner.grid_columnconfigure((0,1), weight=1, uniform="col")
        inner.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(inner, text="✅  Resume Uploaded",
                     font=F("heading"), text_color=SUCCESS).grid(
                     row=0, column=0, columnspan=2, pady=(28,4))
        ctk.CTkLabel(inner,
                     text="How would you like to build your profile?",
                     font=F("body"), text_color=FG_DIM).grid(
                     row=1, column=0, columnspan=2, pady=(0,20))

        # ── Option A: Use as-is ───────────────────────────────────
        card_a = ctk.CTkFrame(inner, fg_color=BG_HOVER, corner_radius=10)
        card_a.grid(row=2, column=0, sticky="nsew", padx=(20,8), pady=(0,20))
        card_a.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card_a, text="⚡  Use as-is",
                     font=F("body_b"), text_color=FG).pack(pady=(20,6))
        ctk.CTkLabel(card_a,
                     text="Claude reads your PDF and builds your profile directly.\n\nFast — takes about 30 seconds.\nGood if your resume is already well written.",
                     font=F("small"), text_color=FG_DIM,
                     wraplength=200, justify="center").pack(padx=16, pady=(0,16), fill="x")
        ctk.CTkButton(card_a, text="Use as-is  →",
                      height=38, font=F("body_b"),
                      fg_color=ACCENT, hover_color=ACCENT_HV,
                      command=self._use_as_is).pack(
                      fill="x", padx=16, pady=(0,20))

        # ── Option B: Enhance with Alex ───────────────────────────
        card_b = ctk.CTkFrame(inner, fg_color=BG_HOVER, corner_radius=10)
        card_b.grid(row=2, column=1, sticky="nsew", padx=(8,20), pady=(0,20))
        card_b.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card_b, text="💬  Enhance with Alex",
                     font=F("body_b"), text_color=FG).pack(pady=(20,6))
        ctk.CTkLabel(card_b,
                     text="Chat with Alex to add context your PDF doesn't capture.\n\nTakes 20-30 minutes.\nBest for stronger bullets.",
                     font=F("small"), text_color=FG_DIM,
                     wraplength=200, justify="center").pack(padx=16, pady=(0,16), fill="x")
        ctk.CTkButton(card_b, text="Chat with Alex  →",
                      height=38, font=F("body_b"),
                      fg_color=BG_CARD, hover_color=BG_HOVER,
                      command=self._enhance_with_alex).pack(
                      fill="x", padx=16, pady=(0,20))

    def _use_as_is(self):
        """Build XML directly from PDF using Claude — no conversation."""
        self._choice_frame.destroy()
        self.title("Building your profile...")
        self._status_lbl.configure(text="Building profile from resume...")
        threading.Thread(target=self._build_xml_direct, daemon=True).start()

    def _enhance_with_alex(self):
        """Remove the choice screen and start the chat flow."""
        self._choice_frame.destroy()
        self.title("Resume Chat")
        self._start_chat()

    def _build_xml_direct(self):
        """
        Claude reads the full resume text and builds resume_data.xml
        in one shot — no conversation needed.
        Uses PROMPT_INTAKE_XML from prompts.db.
        """
        try:
            import anthropic as _ant
            from api.prompts import (get_prompt, PROMPT_INTAKE_XML,
                                     PROMPT_INTAKE_XML_DIRECT,
                                     seed_generic_prompts)
            from core.config import AI_MODEL

            # Ensure prompts are seeded — seed if missing
            seed_generic_prompts()

            client = _ant.Anthropic(api_key=self._api_key)

            self.after(0, lambda: self._status_lbl.configure(
                text="Claude is analysing your resume..."))

            # Use the direct prompt — no Q&A context expected
            prompt = get_prompt(PROMPT_INTAKE_XML_DIRECT) or get_prompt(PROMPT_INTAKE_XML)
            if not prompt:
                raise ValueError(
                    "intake_xml_direct prompt missing. Run: python scripts/update_prompts.py")
            user_msg = (
                "Here is the full resume PDF text:\n\n"
                "---\n" + self._resume_text + "\n---\n\n"
                "Build the complete resume_data.xml from this resume text only."
            )

            msg = client.messages.create(
                model=AI_MODEL,
                max_tokens=4000,
                timeout=120.0,
                system=prompt,
                messages=[{"role": "user", "content": user_msg}]
            )
            xml_text = msg.content[0].text.strip()

            # Strip markdown fences if present
            if xml_text.startswith("```"):
                lines = xml_text.split("\n")
                xml_text = "\n".join(
                    l for l in lines
                    if not l.strip().startswith("```"))

            # Validate it looks like XML
            if not xml_text.startswith("<?xml"):
                idx = xml_text.find("<?xml")
                if idx >= 0:
                    xml_text = xml_text[idx:]
                else:
                    raise ValueError(
                        "Claude did not return valid XML.\n"
                        "Try the Enhance with Alex option instead.")

            # Save XML
            import os
            os.makedirs(os.path.dirname(self._xml_out), exist_ok=True)
            with open(self._xml_out, "w", encoding="utf-8") as f:
                f.write(xml_text)

            self.after(0, lambda: self._status_lbl.configure(
                text="Profile saved — regenerating personalised prompts..."))

            # Regenerate prompts immediately with Claude
            self._regenerate_prompts(
                xml_path=self._xml_out,
                api_key=self._api_key)

            self.after(0, self._on_direct_done)

        except Exception as e:
            _e = str(e)
            self.after(0, lambda _e=_e: self._show_error(
                f"Could not build profile:\n{_e}\n\n"
                f"Try the Enhance with Alex option instead."))

    def _regenerate_prompts(self, xml_path: str = None, api_key: str = None):
        """
        Regenerate personalised prompts immediately from the new XML.
        Runs synchronously — call from a background thread.
        """
        try:
            import anthropic as _ant
            from api.prompts import (setup_all_prompts, delete_prompt,
                                     PROMPT_RELEVANCE_CHECK, PROMPT_RESUME_TAILOR,
                                     get_profile_hash, store_profile_hash,
                                     seed_generic_prompts)
            from core.profile import load_profile_from_xml

            # Seed generic prompts first (intake_xml_direct etc.)
            seed_generic_prompts()

            # Load profile from XML
            path    = xml_path or self._xml_out
            profile = load_profile_from_xml(path)
            if not profile:
                print("[WARN] Profile empty — skipping prompt regeneration")
                return

            # Get API key
            key = api_key or getattr(self, "_api_key", "") or self.master._api_var.get().strip()
            if not key or not key.startswith("sk-ant-"):
                # No key available — just delete old prompts so they
                # regenerate on next bot run
                delete_prompt(PROMPT_RELEVANCE_CHECK)
                delete_prompt(PROMPT_RESUME_TAILOR)
                print("[OK] Prompts cleared — will regenerate on next bot run")
                return

            client = _ant.Anthropic(api_key=key)

            # Delete old personal prompts then regenerate fresh
            delete_prompt(PROMPT_RELEVANCE_CHECK)
            delete_prompt(PROMPT_RESUME_TAILOR)

            result = setup_all_prompts(profile, client, overwrite=False)
            store_profile_hash(get_profile_hash(profile))

            print("[OK] Personalised prompts regenerated (%d created)"
                  % result.get("created", 0))

        except Exception as e:
            print("[WARN] Prompt regeneration failed: %s" % e)
            # Non-fatal — prompts will regenerate on next bot run

    def _on_direct_done(self):
        """Show success screen after direct XML build."""
        self._status_lbl.configure(text="Profile ready!")
        # Show a simple done message
        done_frame = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        done_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        inner = ctk.CTkFrame(done_frame, fg_color=BG_CARD, corner_radius=14)
        inner.place(relx=0.5, rely=0.5, anchor="center",
                    relwidth=0.72, relheight=0.55)
        ctk.CTkLabel(inner, text="✅", font=("Segoe UI", 40)).pack(pady=(28,4))
        ctk.CTkLabel(inner, text="Profile built successfully!",
                     font=F("heading"), text_color=SUCCESS).pack()
        ctk.CTkLabel(inner,
                     text="Your resume_data.xml has been created. Personalised prompts have been reset. You are ready to run the bot.",
                     font=F("small"), text_color=FG_DIM,
                     wraplength=340, justify="center").pack(pady=(10,20))
        ctk.CTkButton(inner, text="Close", height=38,
                      font=F("body_b"),
                      command=self._finish).pack(padx=40, pady=(0,24))

    def _finish(self):
        if self._on_done:
            self._on_done()
        self.destroy()

    # ── Chat engine ───────────────────────────────────────────────

    def _start_chat(self):
        """Send the resume to Claude and get the opening message."""
        self._status_lbl.configure(text="Alex is reading your resume...")
        self._send_btn.configure(state="disabled")

        # Build the initial user message (resume context)
        # Add it to history so subsequent turns have proper alternating structure
        initial_msg = (
            f"Here is my resume:\n\n---\n{self._resume_text}\n---\n\n"
            "Please start our conversation."
        )
        self._history.append({"role": "user", "content": initial_msg})

        indicator = self._add_typing_indicator()
        threading.Thread(
            target=self._call_claude,
            args=(indicator,),
            daemon=True).start()

    def _send(self):
        text = self._input.get("1.0", "end").strip()
        if not text or self._done:
            return
        self._input.delete("1.0", "end")
        self._send_btn.configure(state="disabled")
        self._add_user_message(text)
        self._history.append({"role": "user", "content": text})
        self._turn += 1
        indicator = self._add_typing_indicator()
        threading.Thread(
            target=self._call_claude,
            args=(indicator,),
            daemon=True).start()

    def _call_claude(self, indicator):
        """Background thread: call Claude with full conversation history."""
        try:
            import anthropic as _ant
            try:
                from api.prompts import seed_generic_prompts
                from core.config import AI_MODEL
            except ModuleNotFoundError:
                from api.prompts import seed_generic_prompts
                from core.config import AI_MODEL
            seed_generic_prompts()

            client = _ant.Anthropic(api_key=self._api_key)

            # history always starts with user (resume context) — valid for API
            resp = client.messages.create(
                model=AI_MODEL,
                max_tokens=600,
                system=self._system_prompt(),
                messages=self._history)

            reply = resp.content[0].text.strip()
            ready = "[[READY]]" in reply
            reply_clean = reply.replace("[[READY]]", "").strip()

            self.after(0, lambda: self._on_bot_reply(indicator, reply_clean, ready))

        except Exception as e:
            err = str(e)
            self.after(0, lambda ind=indicator, e=err: self._on_bot_error(ind, e))

    def _on_bot_reply(self, indicator, reply: str, ready: bool):
        self._remove_typing(indicator)
        self._add_bot_message(reply)
        self._history.append({"role": "assistant", "content": reply})
        self._status_lbl.configure(text=f"Turn {self._turn + 1}")

        if ready or self._turn >= self.MAX_TURNS:
            self._status_lbl.configure(text="Building your profile...")
            self._send_btn.configure(state="disabled")
            self._done = True
            self.after(800, self._generate_xml)
        else:
            self._send_btn.configure(state="normal")
            self._input.focus_set()

    def _on_bot_error(self, indicator, err: str):
        self._remove_typing(indicator)
        self._add_bot_message(
            f"Something went wrong 😬\n\n`{err}`\n\n"
            "Close this window and try again.")
        self._status_lbl.configure(text="Error — see message above")

    def _generate_xml(self):
        self._add_bot_message(
            "Perfect — I've got everything I need. Now I'm going to build your "
            "profile from our whole conversation. This usually takes about a minute "
            "so sit tight... ☕")
        threading.Thread(target=self._build_xml, daemon=True).start()

    def _build_xml(self):
        try:
            import anthropic as _ant
            try:
                from api.prompts import get_prompt, seed_generic_prompts, PROMPT_INTAKE_XML
                from core.config import AI_MODEL
            except ModuleNotFoundError:
                from api.prompts import get_prompt, seed_generic_prompts, PROMPT_INTAKE_XML
                from core.config import AI_MODEL
            seed_generic_prompts()

            client = _ant.Anthropic(api_key=self._api_key)

            # Full conversation transcript for the XML builder
            convo = "\n".join(
                f"{'User' if m['role']=='user' else 'Alex'}: {m['content']}"
                for m in self._history)

            resp = client.messages.create(
                model=AI_MODEL,
                max_tokens=8000,
                system=get_prompt(PROMPT_INTAKE_XML),
                messages=[{"role": "user", "content":
                    f"Resume:\n---\n{self._resume_text}\n---\n\n"
                    f"Conversation:\n---\n{convo}\n---\n\n"
                    "Build the complete resume_data.xml now."}])

            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = "\n".join(raw.split("\n")[:-1])
            xml_str = raw.strip()

            import xml.etree.ElementTree as ET
            ET.fromstring(xml_str)   # validate
            from pathlib import Path
            Path(self._xml_out).parent.mkdir(parents=True, exist_ok=True)
            Path(self._xml_out).write_text(xml_str, encoding="utf-8")

            # Check for missing metrics before completing
            missing = self._check_resume_metrics(xml_str)
            if missing:
                self.after(0, lambda m=missing: self._show_metric_popup(
                    m,
                    on_continue=self._complete_alex_save,
                    on_edit=self._abort_and_edit))
                return

            self._complete_alex_save()
        except Exception as e:
            err2=str(e); self.after(0, lambda err2=err2: self._on_xml_error(err2))

    def _on_success(self):
        self._status_lbl.configure(text="Profile saved ✓")
        self._add_bot_message(
            "Done! 🎉 Your profile has been saved.\n\n"
            "You can close this window — head back to Settings "
            "to view your profile or run the bot whenever you're ready.")
        finish_btn = ctk.CTkButton(
            self._chat_scroll, text="Close  ✓",
            fg_color=SUCCESS, hover_color="#16A34A",
            font=F("label_b"), height=40, width=160,
            command=self._finish)
        finish_btn.pack(pady=16)
        self._scroll_to_bottom()

    def _on_xml_error(self, err: str):
        self._status_lbl.configure(text="Error")
        self._add_bot_message(
            f"Ugh, something went wrong building the XML 😬\n\n{err}\n\n"
            "Try closing and uploading your resume again.")

    def _finish(self):
        if self._on_done:
            self._on_done()
        self.destroy()

    def _show_error(self, msg: str):
        self._status_lbl.configure(text="Error")
        self._add_bot_message(f"Oops! {msg}")


class ProfileViewWindow(ctk.CTkToplevel):
    """Displays the current resume profile parsed from resume_data.xml."""

    def __init__(self, parent, xml_path: str):
        super().__init__(parent)
        self.title("Resume Profile")
        self.geometry("700x620")
        self.resizable(True, True)
        self.minsize(560, 400)
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=52)
        hdr.grid(row=0, column=0, sticky="ew"); hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="Resume Profile",
                     font=F("heading"), text_color=FG
                     ).pack(side="left", padx=20, pady=10)
        ctk.CTkButton(hdr, text="Close", width=80, height=32,
                      font=F("small"), fg_color=BG_FIELD,
                      hover_color=BG_HOVER,
                      command=self.destroy
                      ).pack(side="right", padx=16, pady=10)

        # Scrollable content
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        try:
            import xml.etree.ElementTree as ET
            root = ET.parse(xml_path).getroot()
            self._render_profile(scroll, root)
        except Exception as e:
            ctk.CTkLabel(scroll, text=f"Could not read profile: {e}",
                         font=F("body"), text_color=DANGER
                         ).pack(padx=24, pady=40)

    def _section(self, parent, title: str):
        ctk.CTkLabel(parent, text=title, font=F("label_b"),
                     text_color=ACCENT, anchor="w"
                     ).pack(fill="x", padx=24, pady=(18, 4))
        ctk.CTkFrame(parent, height=1, fg_color=BG_HOVER
                     ).pack(fill="x", padx=24, pady=(0, 6))

    def _row(self, parent, label: str, value: str):
        if not (value or "").strip(): return
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=2)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=label, font=F("small_b"),
                     text_color=MUTED, width=130, anchor="w"
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row, text=value, font=F("small"),
                     text_color=FG_SOFT, anchor="w", wraplength=450,
                     justify="left"
                     ).grid(row=0, column=1, sticky="w")

    def _render_profile(self, parent, root):
        # Personal info
        self._section(parent, "Personal Info")
        for tag, label in [("name","Name"),("email","Email"),
                            ("phone","Phone"),("location","Location"),
                            ("linkedin","LinkedIn"),("github","GitHub")]:
            self._row(parent, label, root.findtext(f".//{tag}") or "")

        # Summary
        summary = root.findtext(".//summary") or ""
        if summary:
            self._section(parent, "Summary")
            ctk.CTkLabel(parent, text=summary, font=F("small"),
                         text_color=FG_SOFT, wraplength=620,
                         justify="left", anchor="w"
                         ).pack(fill="x", padx=24, pady=(0, 4))

        # Skills
        skills = root.find(".//skills")
        if skills is not None:
            self._section(parent, "Skills")
            for cat in skills:
                # Category name is in the 'name' attribute, not the tag itself
                cat_name = (cat.attrib.get("name","")
                             or cat.attrib.get("type","")
                             or cat.tag).replace("_"," ").title()
                # Items are <skill> children; fall back to direct text children
                items = [c.text for c in cat
                          if c.text and c.tag in ("skill","item","technology")]
                if not items:
                    items = [c.text for c in cat if c.text]
                if items:
                    self._row(parent, cat_name, ", ".join(items))

        # Experience
        exp = root.find(".//experience")
        if exp is not None:
            self._section(parent, "Experience")
            for job in exp.findall("job"):
                title   = job.findtext("title","")
                company = job.findtext("company","")
                dates   = (job.findtext("dates","")
                            or job.findtext("duration","")
                            or job.findtext("period",""))
                header  = "  @  ".join(filter(None, [title, company]))
                if dates: header += f"  ({dates})"
                ctk.CTkLabel(parent, text=header,
                              font=F("small_b"), text_color=FG, anchor="w"
                              ).pack(fill="x", padx=24, pady=(6, 2))
                bullets_el = job.find("bullets")
                bullet_els = (bullets_el.findall("bullet")
                               if bullets_el is not None else []) or \
                              job.findall("bullet") or \
                              job.findall(".//bullet")
                for b in bullet_els:
                    if b.text and b.text.strip():
                        ctk.CTkLabel(parent, text=f"  • {b.text.strip()}",
                                      font=F("small"), text_color=FG_SOFT,
                                      anchor="w", wraplength=600, justify="left"
                                      ).pack(fill="x", padx=30)

        # Education — stacked layout so long degree names don't overlap
        edu = root.find(".//education")
        if edu is not None:
            self._section(parent, "Education")
            for deg in edu.findall("degree"):
                school = deg.findtext("school","")
                title  = (deg.findtext("name","")
                           or deg.findtext("degree","")
                           or deg.findtext("title",""))
                dates  = (deg.findtext("year","")
                           or deg.findtext("dates","")
                           or deg.findtext("graduation",""))
                # Stacked: school name on line 1, degree + dates on line 2
                # Using _row for the school→degree mapping causes overlap
                # when both strings are long — use two labels instead
                card = ctk.CTkFrame(parent, fg_color="transparent")
                card.pack(fill="x", padx=24, pady=(4, 2))
                ctk.CTkLabel(card, text=school, font=F("small_b"),
                              text_color=FG, anchor="w"
                              ).pack(anchor="w")
                if title or dates:
                    sub = "  •  ".join(filter(None, [title, dates]))
                    ctk.CTkLabel(card, text=sub, font=F("small"),
                                  text_color=FG_DIM, anchor="w",
                                  wraplength=600, justify="left"
                                  ).pack(anchor="w")

        # Projects
        projs = root.find(".//projects")
        if projs is not None:
            self._section(parent, "Projects")
            for p in projs.findall("project"):
                name = p.findtext("name","")
                # Description may be <description>, <summary>, or <tech>
                desc  = (p.findtext("description","")
                          or p.findtext("summary",""))
                tech  = p.findtext("tech","")
                ctk.CTkLabel(parent, text=name, font=F("small_b"),
                              text_color=FG, anchor="w"
                              ).pack(fill="x", padx=24, pady=(6, 0))
                if tech:
                    ctk.CTkLabel(parent, text=tech, font=F("tiny"),
                                  text_color=ACCENT, anchor="w"
                                  ).pack(fill="x", padx=26, pady=(1, 0))
                if desc:
                    ctk.CTkLabel(parent, text=desc, font=F("small"),
                                  text_color=FG_SOFT, anchor="w",
                                  wraplength=600, justify="left"
                                  ).pack(fill="x", padx=30)
                # Bullets inside projects
                bullets_el = p.find("bullets")
                bullet_els = (bullets_el.findall("bullet")
                               if bullets_el is not None else []) or \
                              p.findall("bullet")
                for b in bullet_els:
                    if b.text and b.text.strip():
                        ctk.CTkLabel(parent, text=f"  • {b.text.strip()}",
                                      font=F("small"), text_color=FG_SOFT,
                                      anchor="w", wraplength=600, justify="left"
                                      ).pack(fill="x", padx=30)

        # Spacer at bottom
        ctk.CTkFrame(parent, height=24, fg_color="transparent").pack()


# ── Entry point ────────────────────────────────────────────────────
class ReviewWindow(ctk.CTkToplevel):
    """
    Checklist of previously-applied jobs.
    User reads each reason, ticks the ones to re-apply,
    then clicks Start Review to open them in the browser.
    """

    def __init__(self, parent, candidates: list, api_key: str,
                 runner=None, on_done=None):
        super().__init__(parent)
        self.title("Review Old Applications")
        self.geometry("720x580")
        self.minsize(580, 400)
        self.resizable(True, True)
        self.grab_set()
        self._candidates = candidates
        self._api_key    = api_key
        self._runner     = runner   # existing bot subprocess — stopped before review
        self._on_done    = on_done
        self._checks     = []
        self._q          = queue.Queue()
        self._build()
        self._poll()

    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "status":
                    self._status_lbl.configure(text=data, text_color=FG_DIM)
                elif kind == "done":
                    self._status_lbl.configure(
                        text="Review complete. Close this window.",
                        text_color=SUCCESS)
                    self._start_btn.configure(state="disabled")
        except Exception:
            pass
        if self.winfo_exists():
            self.after(100, self._poll)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(16,8))
        ctk.CTkLabel(hdr, text="Select jobs to re-apply",
                     font=F("body_b"), text_color=FG).pack(side="left")
        ctk.CTkButton(hdr, text="Select All", width=90, height=28,
                      font=F("small"), fg_color=BG_FIELD,
                      hover_color=BG_HOVER,
                      command=self._select_all).pack(side="right")
        ctk.CTkButton(hdr, text="Clear All", width=90, height=28,
                      font=F("small"), fg_color=BG_FIELD,
                      hover_color=BG_HOVER,
                      command=self._clear_all).pack(side="right", padx=(0,8))

        # Scrollable job list
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG)
        scroll.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0,8))
        scroll.grid_columnconfigure(0, weight=1)

        self._checks = []
        for job in self._candidates:
            var  = ctk.BooleanVar(value=False)
            card = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=10)
            card.pack(fill="x", pady=(0,8))
            card.grid_columnconfigure(1, weight=1)

            # Checkbox
            ctk.CTkCheckBox(card, text="", variable=var,
                            width=24).grid(row=0, column=0,
                            rowspan=2, padx=(12,8), pady=12, sticky="n")

            # Job info
            title   = job.get("job_title","") or "Unknown role"
            company = job.get("company","")   or "Unknown company"
            reason  = job.get("ai_reason","") or "No reason recorded"
            score   = job.get("match_score",0) or 0
            days    = job.get("days_ago",-1)
            rtype   = job.get("_review_type","reapply")

            if rtype == "queued":
                when = "Queued — resume ready, not yet applied"
                badge_col = ACCENT
                badge_txt = "Queued"
            else:
                when = ("Applied %d day(s) ago" % days) if days >= 0 else "Previously applied"
                badge_col = SUCCESS
                badge_txt = "Re-apply"

            hdr_row = ctk.CTkFrame(card, fg_color="transparent")
            hdr_row.grid(row=0, column=1, sticky="ew", padx=(0,12), pady=(12,2))
            hdr_row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(hdr_row,
                         text="%s @ %s" % (title, company),
                         font=F("label_b"), text_color=FG,
                         anchor="w").grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(hdr_row, text="  %s  " % badge_txt,
                         fg_color=badge_col, corner_radius=4,
                         font=F("tiny"), text_color=BG
                         ).grid(row=0, column=1, padx=(8,0))
            ctk.CTkLabel(card,
                         text="%s  ·  Match %d%%  |  %s" % (when, score, reason),
                         font=F("small"), text_color=FG_DIM,
                         wraplength=500, justify="left",
                         anchor="w").grid(row=1, column=1, sticky="w",
                                          padx=(0,12), pady=(0,12))

            self._checks.append((var, job))

        # Footer
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=20, pady=(0,16))
        foot.grid_columnconfigure(0, weight=1)

        self._status_lbl = ctk.CTkLabel(foot, text="",
                                         font=F("small"), text_color=FG_DIM)
        self._status_lbl.grid(row=0, column=0, sticky="w")

        self._start_btn = ctk.CTkButton(
            foot, text="▶  Start Review",
            height=40, font=F("body_b"),
            fg_color=ACCENT, hover_color=ACCENT_HV,
            command=self._start_review)
        self._start_btn.grid(row=0, column=1, sticky="e")

    def _select_all(self):
        for var, _ in self._checks:
            var.set(True)

    def _clear_all(self):
        for var, _ in self._checks:
            var.set(False)

    def _start_review(self):
        selected = [job for var, job in self._checks if var.get()]
        if not selected:
            messagebox.showwarning("Nothing selected",
                "Tick at least one job to review.")
            return
        self._start_btn.configure(state="disabled")
        self._status_lbl.configure(
            text="Opening browser for %d job(s)..." % len(selected))
        threading.Thread(
            target=self._do_review,
            args=(selected,), daemon=True).start()

    def _do_review(self, jobs: list):
        """
        Open browser for selected jobs.
        Stops any existing bot subprocess first so Chrome profile is free,
        then uses a queue to feed answers from GUI buttons instead of stdin.
        """
        import queue as _queue
        self._input_q = _queue.Queue()

        # Stop existing bot subprocess so it releases the Chrome profile lock.
        # Without this, Chrome opens a new tab in the existing window instead
        # of a fresh LinkedIn session.
        if self._runner and self._runner.running():
            try:
                self._runner.send("stop")
                import time as _t; _t.sleep(1.5)   # wait for clean shutdown
                self._runner.stop()
                import time as _t; _t.sleep(0.5)
            except Exception:
                pass

        # Switch checklist to "applying" mode — show action buttons
        self.after(0, lambda: self._show_apply_ui(jobs))

        try:
            import asyncio
            import backend.browser as _browser
            from playwright.async_api import async_playwright
            from backend.browser import create_logged_in_context, guided_apply_session

            # Wire GUI input queue into browser module
            _browser._GUI_INPUT_QUEUE = self._input_q

            queued_jobs  = [j for j in jobs if j.get("_review_type") == "queued"]
            reapply_jobs = [j for j in jobs if j.get("_review_type") != "queued"]

            async def _run():
                async with async_playwright() as pw:
                    browser, context, _ = await create_logged_in_context(pw)
                    applied = skipped = 0
                    if queued_jobs:
                        r = await guided_apply_session(
                            context, queued_jobs, reapply=False)
                        applied += r.get("applied", 0)
                        skipped += r.get("skipped", 0)
                    if reapply_jobs:
                        r = await guided_apply_session(
                            context, reapply_jobs, reapply=True)
                        applied += r.get("applied", 0)
                        skipped += r.get("skipped", 0)
                    try:
                        await browser.close()
                    except Exception:
                        pass
                    return {"applied": applied, "skipped": skipped}

            result = asyncio.run(_run())
            _browser._GUI_INPUT_QUEUE = None   # reset
            self._q.put(("done",
                "Applied: %d  Skipped: %d" % (
                    result.get("applied", 0), result.get("skipped", 0))))
            if self._on_done:
                self.after(500, self._on_done)

        except Exception as e:
            import backend.browser as _browser
            _browser._GUI_INPUT_QUEUE = None
            self._q.put(("status", "Error: %s" % e))
            self.after(0, lambda: self._start_btn.configure(state="normal"))

    def _show_apply_ui(self, jobs: list):
        """Replace checklist with apply action buttons."""
        for w in self.winfo_children():
            w.destroy()
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        self.grid_columnconfigure(0, weight=1)

        info = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        info.grid(row=0, column=0, sticky="nsew", padx=20, pady=(20,8))
        info.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(info,
                     text="Browser is open — apply to each job then confirm below.",
                     font=F("label_b"), text_color=FG,
                     wraplength=500).pack(pady=(20, 4))
        self._apply_status = ctk.CTkLabel(info, text="Waiting for browser...",
                                           font=F("small"), text_color=FG_DIM)
        self._apply_status.pack(pady=(0, 20))

        # d/s/q buttons — feed answers into the queue
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", padx=20, pady=(0,20))
        btn_row.grid_columnconfigure((0,1,2), weight=1)

        remaining = [0]   # mutable counter for last-job detection
        remaining[0] = len(jobs)

        def _send(val):
            remaining[0] -= 1
            if remaining[0] <= 0 or val == "q":
                self._apply_status.configure(
                    text="Session complete — you can close this window.")
                for w in btn_row.winfo_children():
                    w.configure(state="disabled")
            self._input_q.put(val)

        ctk.CTkButton(btn_row, text="✓  Applied",
                      fg_color=SUCCESS, hover_color="#16A34A",
                      height=44, font=F("body_b"),
                      command=lambda: _send("d")
                      ).grid(row=0, column=0, padx=(0,8), sticky="ew")
        ctk.CTkButton(btn_row, text="—  Skip",
                      fg_color=BG_FIELD, hover_color=BG_HOVER,
                      height=44, font=F("body_b"),
                      command=lambda: _send("s")
                      ).grid(row=0, column=1, padx=(0,8), sticky="ew")
        ctk.CTkButton(btn_row, text="✕  Quit",
                      fg_color=DANGER, hover_color="#B91C1C",
                      height=44, font=F("body_b"),
                      command=lambda: _send("q")
                      ).grid(row=0, column=2, sticky="ew")


class RegistrationWindow(ctk.CTkToplevel):
    """
    First-time registration gate.
    Fully scrollable, reactive layout.
    All fields validated before submission.
    Thread-safe via queue.
    """

    # Phone regex: accepts +1 (555) 000-0000, +15550000000, 555-000-0000 etc.
    _PHONE_RE = re.compile(
        r"^\+?1?\s*[\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}$")
    _EMAIL_RE = re.compile(
        r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Access Request")
        self.geometry("520x620")
        self.minsize(400, 500)
        self.resizable(True, True)
        self.grab_set()
        self.approved = False
        self._q       = queue.Queue()
        self._build()
        self._poll()

    @staticmethod
    def run_gate(parent) -> bool:
        """
        Show registration window and block until done.
        Returns True ONLY if explicitly approved — False on any error or close.
        """
        try:
            win = RegistrationWindow(parent)
            parent.wait_window(win)
            return win.approved is True   # strict check — must be exactly True
        except Exception:
            return False   # any error → deny access, never bypass

    # ── Queue poll — main thread only ────────────────────────────
    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "status":
                    self._status_lbl.configure(text=data, text_color=FG_DIM)
                elif kind == "error":
                    self._status_lbl.configure(text=data, text_color=DANGER)
                    self._submit_btn.configure(state="normal")
                elif kind == "timer":
                    self._timer_lbl.configure(text=data)
                elif kind == "approved":
                    self._on_approved()
                elif kind == "denied":
                    self._on_denied()
                elif kind == "timeout":
                    self._on_timeout()
        except Exception:
            pass
        if self.winfo_exists():
            self.after(50, self._poll)

    # ── UI build ──────────────────────────────────────────────────
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Outer scrollable container so nothing gets clipped
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # Card inside scroll
        card = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=14)
        card.pack(fill="x", padx=20, pady=20)
        card.grid_columnconfigure(0, weight=1)

        # ── Header ────────────────────────────────────────────────
        ctk.CTkLabel(card, text="⚡",
                     font=(_FONT_FAMILY, 40)).pack(pady=(28, 4))
        ctk.CTkLabel(card, text="Resuto",
                     font=F("heading"), text_color=FG).pack()
        ctk.CTkLabel(card,
                     text="Fill in your details to request access. The admin will approve or deny within 5 minutes.",
                     justify="center", wraplength=440).pack(pady=(6, 22))

        # ── Field builder helper ───────────────────────────────────
        def field(label_text, placeholder, optional=False):
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=24, pady=(0, 12))
            row.grid_columnconfigure(0, weight=1)
            lbl_txt = label_text + ("  (optional)" if optional else "  *")
            ctk.CTkLabel(row, text=lbl_txt, font=F("small"),
                         text_color=FG_DIM if optional else FG,
                         anchor="w").grid(row=0, column=0, sticky="w")
            var = ctk.StringVar()
            entry = ctk.CTkEntry(row, textvariable=var,
                                 placeholder_text=placeholder,
                                 height=38, font=F("body"))
            entry.grid(row=1, column=0, sticky="ew", pady=(3, 0))
            err = ctk.CTkLabel(row, text="", font=F("tiny"),
                               text_color=DANGER, anchor="w")
            err.grid(row=2, column=0, sticky="w")
            return var, entry, err

        self._first_var,  self._first_e,  self._first_err  = field("First Name",   "e.g. John")
        self._middle_var, self._middle_e, self._middle_err = field("Middle Name",  "e.g. Michael", optional=True)
        self._last_var,   self._last_e,   self._last_err   = field("Last Name",    "e.g. Doe")
        self._email_var,  self._email_e,  self._email_err  = field("Email Address","you@example.com")
        self._phone_var,  self._phone_e,  self._phone_err  = field("Phone Number", "+1 (555) 000-0000")

        # ── Error summary ─────────────────────────────────────────
        self._err_lbl = ctk.CTkLabel(card, text="", font=F("small"),
                                      text_color=DANGER, wraplength=440,
                                      justify="center")
        self._err_lbl.pack(pady=(4, 0))

        # ── Submit button ─────────────────────────────────────────
        self._submit_btn = ctk.CTkButton(
            card, text="Request Access  →",
            height=44, font=F("body_b"),
            corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_HV,
            command=self._submit)
        self._submit_btn.pack(fill="x", padx=24, pady=(14, 0))

        # ── Status + timer ────────────────────────────────────────
        self._status_lbl = ctk.CTkLabel(
            card, text="", font=F("small"),
            text_color=FG_DIM, wraplength=440, justify="center")
        self._status_lbl.pack(pady=(12, 0))

        self._timer_lbl = ctk.CTkLabel(
            card, text="", font=F("tiny"), text_color=MUTED)
        self._timer_lbl.pack(pady=(4, 28))

        # Bind Enter key to submit
        self.bind("<Return>", lambda e: self._submit())

    # ── Validation ────────────────────────────────────────────────
    def _validate(self) -> bool:
        ok = True

        # First name
        first = self._first_var.get().strip()
        if not first:
            self._first_err.configure(text="First name is required.")
            ok = False
        elif len(first) < 2:
            self._first_err.configure(text="Must be at least 2 characters.")
            ok = False
        else:
            self._first_err.configure(text="")

        # Middle name (optional — only validate if filled)
        middle = self._middle_var.get().strip()
        if middle and len(middle) < 2:
            self._middle_err.configure(text="Must be at least 2 characters.")
            ok = False
        else:
            self._middle_err.configure(text="")

        # Last name
        last = self._last_var.get().strip()
        if not last:
            self._last_err.configure(text="Last name is required.")
            ok = False
        elif len(last) < 2:
            self._last_err.configure(text="Must be at least 2 characters.")
            ok = False
        else:
            self._last_err.configure(text="")

        # Email
        email = self._email_var.get().strip()
        if not email:
            self._email_err.configure(text="Email address is required.")
            ok = False
        elif not self._EMAIL_RE.match(email):
            self._email_err.configure(text="Enter a valid email address.")
            ok = False
        else:
            self._email_err.configure(text="")

        # Phone
        phone = self._phone_var.get().strip()
        if not phone:
            self._phone_err.configure(text="Phone number is required.")
            ok = False
        elif not self._PHONE_RE.match(re.sub(r"\s+", " ", phone)):
            self._phone_err.configure(
                text="Enter a valid US phone number e.g. +1 (555) 000-0000")
            ok = False
        else:
            self._phone_err.configure(text="")

        return ok

    # ── Submit ────────────────────────────────────────────────────
    def _submit(self):
        self._err_lbl.configure(text="")
        if not self._validate():
            return

        first  = self._first_var.get().strip()
        middle = self._middle_var.get().strip()
        last   = self._last_var.get().strip()
        email  = self._email_var.get().strip()
        phone  = self._phone_var.get().strip()
        name   = " ".join(filter(None, [first, middle, last]))

        self._submit_btn.configure(state="disabled")
        self._status_lbl.configure(text="Sending request to admin...")
        threading.Thread(
            target=self._do_registration,
            args=(name, email, phone),
            daemon=True).start()

    def _do_registration(self, name: str, email: str, phone: str):
        """Background thread — only touches self._q, never widgets directly."""
        try:
            from core.license import (
                _machine_id, send_approval_request,
                poll_for_decision, store_decision, notify_admin_expired)
            mid = _machine_id()

            msg_id = send_approval_request(name, phone, mid, email=email)
            if not msg_id:
                self._q.put(("error",
                    "Could not send access request.\n\n"
                    "Please check your internet connection and try again.\n"
                    "If the problem persists, contact the developer."))
                return

            self._q.put(("status",
                "Request sent! Waiting for admin approval. You have 5 minutes."))

            def _prog(remaining):
                m, s = divmod(remaining, 60)
                self._q.put(("timer", "Time remaining: %d:%02d" % (m, s)))

            decision = poll_for_decision(mid, progress_cb=_prog)
            store_decision(name, phone, mid, decision, email=email)
            if decision == "timeout":
                notify_admin_expired(mid)
            self._q.put((decision if decision in ("approved", "denied")
                         else "timeout", None))

        except Exception as e:
            self._q.put(("error", "Error: " + str(e)))

    # ── Outcome handlers ──────────────────────────────────────────
    def _check_for_updates_after_launch(self):
        """Check for updates in background after app is running."""
        try:
            from core.updater import check_in_background
            def _on_found(info):
                self.after(0, lambda: self._show_update_dialog(info))
            check_in_background(_on_found)
        except Exception:
            pass

    def _show_update_dialog(self, info: dict):
        """Show update available dialog."""
        version  = info.get("version", "?")
        notes    = info.get("release_notes", "")
        dl_url   = info.get("download_url", "")

        popup = ctk.CTkToplevel(self)
        popup.title("Update Available")
        popup.geometry("460x300")
        popup.resizable(False, False)
        popup.grab_set()

        ctk.CTkLabel(popup,
            text="v%s is available" % version,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=ACCENT).pack(pady=(20,6))

        ctk.CTkLabel(popup,
            text="You have v%s installed." % self._get_current_version(),
            font=ctk.CTkFont(size=12),
            text_color=FG_DIM).pack()

        if notes:
            tb = ctk.CTkTextbox(popup, height=80, font=ctk.CTkFont(size=11))
            tb.pack(fill="x", padx=20, pady=10)
            tb.insert("1.0", notes)
            tb.configure(state="disabled")

        prog_lbl = ctk.CTkLabel(popup, text="", font=ctk.CTkFont(size=11),
                                  text_color=FG_DIM)
        prog_lbl.pack()

        prog = ctk.CTkProgressBar(popup, width=400)
        prog.set(0)

        btn_row = ctk.CTkFrame(popup, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(10,16))

        ctk.CTkButton(btn_row, text="Later", width=100,
            fg_color=BG_FIELD, hover_color=BG_HOVER,
            command=popup.destroy).pack(side="left")

        def _do_update():
            update_btn.configure(state="disabled", text="Downloading...")
            prog.pack(pady=(0,8))
            prog_lbl.pack()

            def _progress(pct):
                self.after(0, lambda: prog.set(pct/100))
                self.after(0, lambda: prog_lbl.configure(
                    text="Downloading... %d%%" % pct))

            def _worker():
                from core.updater import download_and_install
                ok = download_and_install(dl_url, version, _progress)
                if ok:
                    self.after(0, lambda: (
                        prog_lbl.configure(text="Installing... app will restart."),
                        self.after(2000, self.quit)))
                else:
                    self.after(0, lambda: prog_lbl.configure(
                        text="Download failed. Try again later.",
                        text_color=DANGER))
                    self.after(0, lambda: update_btn.configure(
                        state="normal", text="Update Now"))

            import threading
            threading.Thread(target=_worker, daemon=True).start()

        update_btn = ctk.CTkButton(btn_row, text="Update Now",
            width=140, fg_color=ACCENT, hover_color=ACCENT_HV,
            command=_do_update)
        update_btn.pack(side="right")

    def _get_current_version(self) -> str:
        try:
            from core.config import APP_VERSION
            return APP_VERSION
        except Exception:
            return "?"

    def _on_approved(self):
        self._status_lbl.configure(
            text="✅ Access approved! Starting the app...",
            text_color=SUCCESS)
        self._timer_lbl.configure(text="")
        self.approved = True
        self.after(1200, self.destroy)

    def _on_denied(self):
        self._status_lbl.configure(
            text="❌ Access denied. Contact the administrator "
                 "if you believe this is a mistake.",
            text_color=DANGER)
        self._timer_lbl.configure(text="")
        self.after(4000, self.destroy)

    def _on_timeout(self):
        self._status_lbl.configure(
            text="⏰ No response from admin within 5 minutes. "
                 "Please try again later.",
            text_color=WARNING)
        self._timer_lbl.configure(text="")
        self._submit_btn.configure(state="normal", text="Try Again")

if __name__ == "__main__":
    # ── Bot mode — launched by the GUI as a subprocess ────────────
    # When running as a PyInstaller exe, the GUI spawns itself with
    # --bot-mode to run the orchestrator instead of opening another window.
    if "--bot-mode" in sys.argv:
        sys.argv.remove("--bot-mode")
        import asyncio
        # Add project root to path so orchestrator imports work
        _bot_root = Path(sys._MEIPASS) if getattr(sys, "frozen", False)                     else Path(__file__).parent.parent
        sys.path.insert(0, str(_bot_root))
        from backend.orchestrator import main as _bot_main
        try:
            asyncio.run(_bot_main(gui_args=type("A", (), {
                "gui":        True,
                "location":   next((sys.argv[sys.argv.index("--location")+1]
                               for _ in [0] if "--location" in sys.argv), ""),
                "max_jobs":   int(next((sys.argv[sys.argv.index("--max-jobs")+1]
                               for _ in [0] if "--max-jobs" in sys.argv), 5)),
                "mode":       next((sys.argv[sys.argv.index("--mode")+1]
                               for _ in [0] if "--mode" in sys.argv), "easy_apply"),
                "roles":      sys.argv[sys.argv.index("--roles")+1:]
                               if "--roles" in sys.argv else [],
                "clear_runs": "--clear-runs" in sys.argv,
            })()))
        except (KeyboardInterrupt, EOFError):
            pass
        sys.exit(0)

    # ── Normal GUI mode ───────────────────────────────────────────
    log = Path(__file__).parent.parent / "app_error.log"
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
                          font=(_FONT_FAMILY, 13, "bold")).pack(pady=(16,4))
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