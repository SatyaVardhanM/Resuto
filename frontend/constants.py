"""
frontend/constants.py
─────────────────────
App identity, colour palette, and font system.
Imported by app.py and every view — never imports from other frontend modules.
"""
import sys
import json
from pathlib import Path

import customtkinter as ctk
import platform as _platform

# ── App identity ──────────────────────────────────────────────────
APP_TITLE  = "Resuto"
def _get_bot_script() -> Path:
    """
    Return the path to orchestrator.py.
    Works both from source and inside a PyInstaller bundle.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(sys.executable).parent

    p = base / "backend" / "orchestrator.py"

    # Fallback: try common alternative locations
    if not p.exists():
        for alt in [
            base / "main.py",
            base / "orchestrator.py",
            Path(sys.executable).parent / "orchestrator.py",
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
STRETCH   = "#F59E0B"   # amber — same as WARNING
MUTED     = "#6B7280"
FG        = "#F1F2F4"
FG_SOFT   = "#C8CBD2"
FG_DIM    = "#8B8FA8"


def _settings_file() -> Path:
    """Return local_settings.json path — works both from source and exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "local_settings.json"
    return Path(sys.executable).parent / "local_settings.json"  # frozen/source

def _load_settings() -> dict:
    """Load full local_settings.json."""
    try:
        p = Path(sys.executable).parent / "local_settings.json"
        if p.exists():
            import json as _j
            return _j.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_settings(data: dict) -> None:
    """Save full local_settings.json."""
    try:
        p = Path(sys.executable).parent / "local_settings.json"
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