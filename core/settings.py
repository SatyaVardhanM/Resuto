# core/settings.py
import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""
Handles machine-specific settings that should NOT be committed to git.
Works correctly both from source and inside a PyInstaller bundle.
"""
import os
import sys
import json


def _exe_dir() -> str:
    """
    Root directory for user-writable files.

    Why platform matters:
    - Windows exe  : lives in AppData\\Local\\Resuto\\ (writable)
                     so we write files right next to it.
    - Mac .app     : the .app bundle is READ-ONLY by design (macOS security).
                     Writing next to the exe would fail with PermissionError.
                     So on Mac we use ~/Library/Application Support/Resuto/
                     which is always writable and is the macOS standard location.
    - Source mode  : project root folder (for development)
    """
    if getattr(sys, "frozen", False):
        import platform as _plat
        if _plat.system() == "Darwin":
            # Mac — use writable Application Support folder
            support = os.path.join(
                os.path.expanduser("~"),
                "Library", "Application Support", "Resuto")
            os.makedirs(support, exist_ok=True)
            return support
        # Windows / Linux — folder containing the exe is writable
        return os.path.dirname(sys.executable)
    # Source mode
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _settings_file() -> str:
    return os.path.join(_exe_dir(), "local_settings.json")


def _default_resume_path() -> str:
    """Default XML path — data/ folder next to exe or project root."""
    return os.path.join(_exe_dir(), "data", "resume_data.xml")


def _load() -> dict:
    p = _settings_file()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(settings: dict) -> None:
    try:
        with open(_settings_file(), "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print("   [WARN] Could not save local_settings.json: %s" % e)


def get_settings(force_prompt: bool = False) -> dict:
    settings = _load()

    # GUI / bot-mode: never prompt — fill defaults silently
    gui_mode = (
        os.getenv("BOT_GUI_MODE") == "1"
        or getattr(sys, "frozen", False)   # exe always silent
    )
    needs_prompt = (not gui_mode) and (force_prompt or "output_dir" not in settings)

    if needs_prompt:
        print("\n" + "=" * 55)
        print("[TOOL]  First-time setup -- local paths")
        print("=" * 55)
        default_out = os.path.join(_exe_dir(), "output")
        default_xml = _default_resume_path()

        out = input("   Output dir [Enter = '%s']: " % default_out).strip()
        settings["output_dir"] = out or default_out

        rd = input("   Resume XML [Enter = '%s']: " % default_xml).strip()
        settings["resume_data_path"] = rd or default_xml

        _save(settings)
        print("   [OK] Saved.")
        print("=" * 55 + "\n")
    else:
        changed = False
        if "output_dir" not in settings:
            settings["output_dir"] = os.path.join(_exe_dir(), "output")
            changed = True
        if "resume_data_path" not in settings:
            settings["resume_data_path"] = _default_resume_path()
            changed = True
        if changed:
            _save(settings)

    return settings


def get_output_dir() -> str:
    """
    Returns the resume/output directory.

    Priority:
      1. Explicit "output_dir" in local_settings.json (user override)
      2. Sibling of chrome_profile_path — so changing browser profile
         location automatically moves all output to the same base dir:
           chrome_profile_path = D:\\Resuto\\BotChromeProfile
           output_dir          = D:\\Resuto\\output
      3. Default: <exe_dir>/output
    """
    settings = get_settings()

    # Priority 1 — explicit user override
    explicit = settings.get("output_dir", "").strip()
    if explicit and os.path.isabs(explicit):
        return explicit

    # Priority 2 — derive from chrome_profile_path parent
    chrome = settings.get("chrome_profile_path", "").strip()
    if chrome and os.path.isabs(chrome):
        base = os.path.dirname(chrome)   # parent of BotChromeProfile/
        return os.path.join(base, "output")

    # Priority 3 — default next to exe
    return os.path.join(_exe_dir(), "output")


def get_resume_data_path() -> str:
    """
    Return the path to resume_data.xml.
    Checks in order:
    1. Stored path in local_settings.json — if it's a real file
    2. data/ folder next to exe/project root
    3. Home folder fallback
    """
    stored = get_settings().get("resume_data_path", "")
    if stored and os.path.isfile(stored):
        return stored

    # Default next to exe / project root
    default = _default_resume_path()
    if os.path.isfile(default):
        return default

    return default


def out_path(*parts: str) -> str:
    full   = os.path.join(get_output_dir(), *parts)
    parent = full if not os.path.splitext(full)[1] else os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return full