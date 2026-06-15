"""
core/updater.py — Auto-update system.

Flow:
  1. On startup, check GitHub releases API for latest version
  2. Compare with APP_VERSION in config.py
  3. If newer version found → show update dialog in GUI
  4. User clicks "Update Now" → download installer → run silently → app exits
  5. Installer preserves all user data (local_settings, db, xml)
  6. App relaunches automatically

Version format: semantic versioning — "1.0.0", "1.2.3" etc.
"""

import os, sys, threading, subprocess, tempfile
from pathlib import Path

try:
    from core.logger import log, log_warn, log_error
except Exception:
    def log(m, *a): pass
    def log_warn(m, *a): pass
    def log_error(m, *a): pass


def _parse_version(v: str) -> tuple:
    """Convert "1.2.3" → (1, 2, 3) for comparison."""
    try:
        v = v.strip().lstrip("v")
        return tuple(int(x) for x in v.split(".")[:3])
    except Exception:
        return (0, 0, 0)


def _current_version() -> str:
    try:
        from core.config import APP_VERSION
        return APP_VERSION
    except Exception:
        return "0.0.0"


def check_for_update(timeout: int = 8) -> dict | None:
    """
    Check GitHub releases for a newer version.
    Returns dict with {version, download_url, release_notes} or None.
    Never raises — update failure is non-blocking.
    """
    try:
        from core.config import UPDATE_CHECK_URL, UPDATE_ENABLED
        if not UPDATE_ENABLED:
            return None
        if not UPDATE_CHECK_URL:
            return None

        import requests
        resp = requests.get(UPDATE_CHECK_URL,
                            timeout=timeout,
                            headers={"Accept": "application/vnd.github+json",
                                     "User-Agent": "Resuto"})
        if resp.status_code != 200:
            log_warn("Update check failed: HTTP %d" % resp.status_code)
            return None

        data         = resp.json()
        latest_tag   = data.get("tag_name", "0.0.0")
        latest_ver   = latest_tag.lstrip("v")
        current_ver  = _current_version()
        release_body = data.get("body", "")[:500]

        if _parse_version(latest_ver) <= _parse_version(current_ver):
            log("Updater: up to date (v%s)" % current_ver)
            return None

        # Find Windows installer in release assets
        download_url = None
        for asset in data.get("assets", []):
            name = asset.get("name", "").lower()
            if name.endswith(".exe") and "setup" in name:
                download_url = asset.get("browser_download_url")
                break

        if not download_url:
            log_warn("Updater: v%s available but no installer asset found" % latest_ver)
            return None

        log("Updater: new version v%s available (current: v%s)" % (
            latest_ver, current_ver))
        return {
            "version":       latest_ver,
            "download_url":  download_url,
            "release_notes": release_body.strip(),
            "tag":           latest_tag,
        }

    except Exception as e:
        log_warn("Updater: check failed — %s" % e)
        return None


def download_and_install(download_url: str,
                          version: str,
                          progress_cb=None) -> bool:
    """
    Download the installer to a temp file and run it silently.
    The installer preserves user data (local_settings, db, xml).
    The app will exit so the installer can replace the exe.
    Returns True if install started successfully.
    """
    try:
        import requests
        log("Updater: downloading v%s installer..." % version)

        tmp_dir  = Path(tempfile.gettempdir()) / "jab_update"
        tmp_dir.mkdir(exist_ok=True)
        tmp_path = tmp_dir / ("Resuto-v%s-Setup.exe" % version)

        resp = requests.get(download_url, stream=True, timeout=60)
        resp.raise_for_status()

        total     = int(resp.headers.get("content-length", 0))
        received  = 0
        chunk_sz  = 65536

        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_sz):
                if chunk:
                    f.write(chunk)
                    received += len(chunk)
                    if progress_cb and total:
                        pct = int(received / total * 100)
                        progress_cb(pct)

        log("Updater: download complete → %s" % tmp_path)

        # Launch installer silently — /VERYSILENT preserves user data
        # (installer.iss [Code] backs up and restores local_settings.json etc.)
        subprocess.Popen(
            [str(tmp_path), "/VERYSILENT", "/SUPPRESSMSGBOXES",
             "/NORESTART", "/LOG"],
            creationflags=subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32" else 0
        )
        log("Updater: installer launched — exiting app for update")
        return True

    except Exception as e:
        log_error("Updater: download/install failed — %s" % e)
        return False


def check_in_background(on_update_found):
    """
    Run update check in background thread.
    on_update_found(info_dict) called on main thread if update available.
    Safe to call on every startup — exits silently if no update.
    """
    def _worker():
        info = check_for_update()
        if info and on_update_found:
            on_update_found(info)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()