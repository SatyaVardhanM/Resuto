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


def _resuto_documents_dir() -> str:
    """
    Returns Documents\Resuto\ — the user-visible home for all Resuto data.
    Always accessible, never hidden, works on all Windows accounts.
    Falls back to home dir if Documents is unavailable.
    """
    import pathlib
    docs = pathlib.Path.home() / "Documents"
    if not docs.exists():
        docs = pathlib.Path.home()
    base = docs / "Resuto"
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


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
    """
    Returns path to local_settings.json.
    Stored in Documents\\Resuto\\ so users can find it and it
    survives app reinstalls without losing settings.
    Falls back to exe dir if Documents unavailable.
    """
    docs_path = os.path.join(_resuto_documents_dir(), "local_settings.json")

    # Migrate: if old settings exist next to exe, move them to Documents
    old_path = os.path.join(_exe_dir(), "local_settings.json")
    if os.path.exists(old_path) and not os.path.exists(docs_path):
        try:
            import shutil
            shutil.move(old_path, docs_path)
            print("[Setup] Migrated local_settings.json to Documents\\Resuto\\")
        except Exception:
            return old_path   # fallback if move fails

    return docs_path


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


def first_run_setup():
    """
    On first run, copy resume_data.example.xml to Documents/Resuto/
    so users can find and edit it easily.
    """
    import shutil, pathlib, sys as _sys

    docs_dir    = pathlib.Path(_resuto_documents_dir())
    xml_dest    = docs_dir / "resume_data.xml"
    example_dst = docs_dir / "resume_data.example.xml"

    if xml_dest.exists():
        return

    if getattr(_sys, "frozen", False):
        exe_dir = pathlib.Path(_sys.executable).parent
    else:
        exe_dir = pathlib.Path(__file__).parent.parent

    example_src = exe_dir / "data" / "resume_data.example.xml"
    if example_src.exists():
        shutil.copy(example_src, example_dst)
        print(f"[Setup] Example resume copied to: {example_dst}")
    else:
        print(f"[Setup] Documents/Resuto/ created at: {docs_dir}")



def get_settings(force_prompt: bool = False) -> dict:
    settings = _load()

    # GUI / bot-mode: never prompt — fill defaults silently
    gui_mode = (
        os.getenv("BOT_GUI_MODE") == "1"
        or getattr(sys, "frozen", False)   # exe always silent
    )
    needs_prompt = (not gui_mode) and (force_prompt or "output_dir" not in settings)

    if needs_prompt:
        import sys as _sys
        is_interactive = hasattr(_sys.stdin, "isatty") and _sys.stdin.isatty()
        if not is_interactive:
            needs_prompt = False   # subprocess/pipe — never prompt

    if needs_prompt:
        print("\n" + "=" * 55)
        print("[TOOL]  First-time setup -- local paths")
        print("=" * 55)
        default_out = os.path.join(_exe_dir(), "output")
        default_xml = _default_resume_path()

        try:
            out = input("   Output dir [Enter = '%s']: " % default_out).strip()
            settings["output_dir"] = out or default_out
            rd = input("   Resume XML [Enter = '%s']: " % default_xml).strip()
            settings["resume_data_path"] = rd or default_xml
            _save(settings)
            print("   [OK] Saved.")
        except EOFError:
            pass   # non-interactive — use defaults
        print("=" * 55 + "\n")
    else:
        changed = False
        if "output_dir" not in settings:
            settings["output_dir"] = os.path.join(_resuto_documents_dir(), "output")
            changed = True
        if "chrome_profile_path" not in settings or not settings.get("chrome_profile_path"):
            settings["chrome_profile_path"] = os.path.join(
                _resuto_documents_dir(), "BotChromeProfile")
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

    # Default: Documents\Resuto\resume_data.xml
    docs_xml = os.path.join(_resuto_documents_dir(), "resume_data.xml")
    if os.path.isfile(docs_xml):
        return docs_xml

    # Fallback: next to exe / project root
    default = _default_resume_path()
    if os.path.isfile(default):
        return default

    return docs_xml   # return Documents path as default even if not yet created


def out_path(*parts: str) -> str:
    full   = os.path.join(get_output_dir(), *parts)
    parent = full if not os.path.splitext(full)[1] else os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return full