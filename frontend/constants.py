"""
frontend/constants.py
─────────────────────
App identity, colour palette, and font system.
Imported by app.py and every view — never imports from other frontend modules.
"""
import sys
import json
from pathlib import Path

import re

import customtkinter as ctk
import platform as _platform

# ── App identity ──────────────────────────────────────────────────
APP_TITLE  = "Resuto"
def _get_bot_script() -> Path:
    """
    Path to backend/orchestrator.py when running from source.
    In the compiled exe the .py files don't exist on disk, so BotRunner
    falls back to spawning the exe itself with --bot-mode.
    """
    project_root = Path(__file__).resolve().parent.parent
    return project_root / "backend" / "orchestrator.py"

BOT_SCRIPT = _get_bot_script()

# ── Palette ─────────────────────────────────────────────────────
# Every colour is a (light, dark) pair. CustomTkinter widgets accept the
# pair directly and switch automatically with the appearance mode.
# For plain tkinter (Canvas, Text tags) use C(colour) to get the hex
# for the current mode.
BG        = ("#F5F6FA", "#0F1014")   # window background
BG_SIDE   = ("#FFFFFF", "#121318")   # top bar / side menus
BG_CARD   = ("#FFFFFF", "#17181E")   # cards
BG_FIELD  = ("#F1F2F7", "#1D1F27")   # inputs, chips, secondary buttons
BG_HOVER  = ("#E9EBF2", "#24262F")
LINE      = ("#E4E6EE", "#262833")   # 1px borders
ACCENT    = "#5B6AF0"                # Resuto indigo (same in both modes)
ACCENT_HV = "#4A58D4"                # hover shade
ACCENT_SOFT = ("#EEF0FF", "#23264A") # selected nav pill / tags
ACCENT_TXT  = ("#3F4BC9", "#9AA6FF") # indigo text that reads on the background
DANGER    = ("#D92D20", "#E84545")
SUCCESS   = ("#15803D", "#22C55E")
WARNING   = ("#B45309", "#F59E0B")
STRETCH   = WARNING                  # amber — same as WARNING
QUEUED    = ("#3F4BC9", "#8C97FF")   # "queued" status
MUTED     = ("#6B7082", "#828899")   # 4.5:1+ on cards in both modes
FG        = ("#151720", "#EEF0F5")
FG_SOFT   = ("#3A3F4E", "#C8CBD2")
FG_DIM    = ("#555B6C", "#A9ADBD")


def is_dark() -> bool:
    try:
        return ctk.get_appearance_mode().lower() == "dark"
    except Exception:
        return True


def C(colour):
    """Resolve a (light, dark) pair to the hex for the current mode."""
    if isinstance(colour, (tuple, list)):
        return colour[1] if is_dark() else colour[0]
    return colour


def load_appearance() -> str:
    """Saved appearance: 'dark' (default), 'light' or 'system'."""
    try:
        from core.settings import load_all
        v = str(load_all().get("appearance", "dark") or "dark").lower()
        return v if v in ("dark", "light", "system") else "dark"
    except Exception:
        return "dark"


def save_appearance(mode: str) -> None:
    try:
        from core.settings import update
        update(appearance=mode)
    except Exception:
        pass


# ── Settings (single file: Documents\Resuto\local_settings.json) ──
# All reads/writes go through core.settings so the frontend, backend and
# core modules never disagree about where settings live.

def _settings_file() -> Path:
    from core.settings import _settings_file as _sf
    return Path(_sf())

def _load_settings() -> dict:
    try:
        from core.settings import load_all
        return load_all()
    except Exception:
        return {}

def _save_settings(data: dict) -> None:
    try:
        from core.settings import _save
        _save(data)
    except Exception:
        pass

def _load_api_key() -> str:
    """Load saved API key (decrypted with Windows DPAPI)."""
    try:
        from core.secure_store import load_api_key
        return load_api_key()
    except Exception:
        return ""

def _save_api_key(key: str) -> None:
    """Persist API key encrypted (only called when 'Remember API key' is on)."""
    try:
        from core.secure_store import save_api_key
        if not save_api_key(key):
            try:
                from core.logger import log_warn
                log_warn("Could not store API key securely; it will not be remembered")
            except Exception:
                pass
    except Exception:
        pass

def _clear_api_key() -> None:
    """Remove API key from settings (encrypted and any legacy plain copy)."""
    try:
        from core.secure_store import clear_api_key
        clear_api_key()
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
    """Read saved base font size."""
    try:
        return int(_load_settings().get("font_size", 14))
    except Exception:
        return 14


def _save_font_pref(size: int) -> None:
    """Persist base font size."""
    try:
        from core.settings import update
        update(font_size=int(size))
    except Exception:
        pass


def base_size() -> int:
    """Current base font size (read live — don't import _BASE_SIZE by value)."""
    return _BASE_SIZE


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
