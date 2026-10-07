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


def _get_log_file() -> str:
    """Returns log file path, creating directory if needed."""
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir / "bot.log")


# Lazy LOG_FILE - evaluated on first access
class _LazyLogFile:
    """Proxy so LOG_FILE works as a string but directory is created lazily."""
    def __str__(self):       return _get_log_file()
    def __fspath__(self):    return _get_log_file()
    def __add__(self, o):    return _get_log_file() + o
    def __radd__(self, o):   return o + _get_log_file()
    def __repr__(self):      return repr(_get_log_file())
    def __eq__(self, o):     return _get_log_file() == o


LOG_FILE = _LazyLogFile()

# ── Secret redaction ──────────────────────────────────────────────
# Anything that looks like a key or token is masked before it reaches
# bot.log, the console pipe or the GUI, so logs are safe to share.
import re as _re
_SECRET_PATTERNS = [
    (_re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"),               "sk-ant-***"),
    (_re.compile(r"\bbot\d{6,12}:[A-Za-z0-9_\-]{30,}"),       "bot***"),
    (_re.compile(r"\b\d{6,12}:AA[A-Za-z0-9_\-]{30,}"),         "***:***"),
    (_re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{20,}"),     "gh_***"),
    (_re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),            "github_pat_***"),
    (_re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),       r"\1***"),
    (_re.compile(r"(?i)(x-api-key['\"]?\s*[:=]\s*['\"]?)[^\s'\",]+"), r"\1***"),
    (_re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", _re.S),
     "-----PRIVATE KEY REDACTED-----"),
    (_re.compile(r'("private_key"\s*:\s*")[^"]+'),               r"\1***"),
]


def redact(text) -> str:
    """Mask API keys, tokens and private keys in any string."""
    s = str(text)
    for rx, rep in _SECRET_PATTERNS:
        s = rx.sub(rep, s)
    return s


class _RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))

# ── Set up logger ─────────────────────────────────────────────────
_logger = logging.getLogger(APP_NAME)
_logger.setLevel(logging.DEBUG)

if not _logger.handlers:
    # Rotating file — 5 MB max, keep 2 backups
    _fh = RotatingFileHandler(
        _get_log_file(), maxBytes=5 * 1024 * 1024, backupCount=2,
        encoding="utf-8")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(_RedactingFormatter(
        "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(_fh)

    # Also print to console (captured by GUI subprocess pipe)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setLevel(logging.INFO)
    _sh.setFormatter(_RedactingFormatter(
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