"""
core/logger.py — Central logging for the entire app.

Log file location:
  Windows : %APPDATA%\\Resuto\bot.log
  Mac     : ~/Library/Logs/Resuto/bot.log
  Linux   : ~/.local/share/Resuto/bot.log
  Frozen  : next to the .exe → bot.log

Usage (anywhere in the codebase):
    from core.logger import log, log_error, log_warn, LOG_FILE

    log("Phase 1 started")
    log_warn("Claude API slow — attempt 2")
    log_error("Relevance check failed", exc=e)

The log file rolls over at 5 MB (keeps last 2 files).
All timestamps are local time.
"""

import sys as _sys, os as _os
if not getattr(_sys, "frozen", False):
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import sys
import logging
import platform
from pathlib import Path
from logging.handlers import RotatingFileHandler

APP_NAME = "Resuto"

# ── Log file path ─────────────────────────────────────────────────
def _log_dir() -> Path:
    system = platform.system()

    if system == "Windows":
        # Documents\Resuto\logs -- always visible and user-accessible
        docs = Path.home() / "Documents"
        if not docs.exists():
            docs = Path.home()
        base = docs / APP_NAME / "logs"
    elif system == "Darwin":
        base = Path.home() / "Library" / "Logs" / APP_NAME
    else:
        base = Path.home() / ".local" / "share" / APP_NAME / "logs"

    try:
        base.mkdir(parents=True, exist_ok=True)
        return base
    except OSError:
        import tempfile
        fallback = Path(tempfile.gettempdir()) / APP_NAME / "logs"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


LOG_FILE = str(_log_dir() / "bot.log")

# ── Set up logger ─────────────────────────────────────────────────
_logger = logging.getLogger(APP_NAME)
_logger.setLevel(logging.DEBUG)

if not _logger.handlers:
    # Rotating file — 5 MB max, keep 2 backups
    _fh = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=2,
        encoding="utf-8")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(_fh)

    # Also print to console (captured by GUI subprocess pipe)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setLevel(logging.INFO)
    _sh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S"))
    _logger.addHandler(_sh)

# ── Public helpers ────────────────────────────────────────────────
def log(msg: str, *args):
    """Log an INFO message."""
    _logger.info(msg, *args)

def log_warn(msg: str, *args):
    """Log a WARNING message."""
    _logger.warning(msg, *args)

def log_error(msg: str, *args, exc: Exception = None):
    """Log an ERROR message, optionally with a full traceback."""
    if exc:
        _logger.exception(msg, *args)
    else:
        _logger.error(msg, *args)

def log_debug(msg: str, *args):
    """Log a DEBUG message (file only, not console)."""
    _logger.debug(msg, *args)

def log_section(title: str):
    """Log a visual separator — useful between phases."""
    _logger.info("=" * 55)
    _logger.info("  %s", title)
    _logger.info("=" * 55)