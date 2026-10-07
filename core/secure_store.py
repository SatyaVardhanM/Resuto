"""
core/secure_store.py — keep the Anthropic API key out of plain-text files.

On Windows the key is encrypted with DPAPI (CryptProtectData), the same
mechanism Windows uses for saved Wi-Fi and browser passwords. The result can
only be decrypted by the same Windows user account on the same PC, so copying
local_settings.json to another machine or account gives nothing usable.

Settings layout:
    "api_key_protected": "dpapi:<base64>"   (Windows)
    "api_key":           "<plain>"          (other OSes / legacy files only)

No third-party dependency: DPAPI is called through ctypes.
"""
import base64
import json
import os
import sys

PREFIX   = "dpapi:"
_ENTROPY = b"Zetene.Resuto.api_key.v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


def available() -> bool:
    return sys.platform == "win32"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return DATA_BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32  = ctypes.WinDLL("crypt32",  use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype  = ctypes.c_void_p

    blob_in,  _keep1 = _blob(data)
    blob_ent, _keep2 = _blob(_ENTROPY)
    blob_out = DATA_BLOB()

    if protect:
        fn = crypt32.CryptProtectData
        fn.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR,
                       ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD,
                       ctypes.POINTER(DATA_BLOB)]
        desc = "Resuto API key"
    else:
        fn = crypt32.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                       ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD,
                       ctypes.POINTER(DATA_BLOB)]
        desc = None
    fn.restype = wintypes.BOOL

    ok = fn(ctypes.byref(blob_in), desc, ctypes.byref(blob_ent), None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def protect(secret: str) -> str:
    """Encrypt for the current Windows user. Raises if unavailable."""
    if not available():
        raise RuntimeError("DPAPI is only available on Windows")
    return PREFIX + base64.b64encode(_dpapi(secret.encode("utf-8"), True)).decode("ascii")


def unprotect(token: str) -> str:
    if not token or not token.startswith(PREFIX):
        raise ValueError("not a protected value")
    raw = base64.b64decode(token[len(PREFIX):])
    return _dpapi(raw, False).decode("utf-8")


# ── API key helpers used by the UI ────────────────────────────────
def load_api_key() -> str:
    """Return the saved key ('' if none). Migrates a plain-text key in place."""
    from core.settings import load_all, update
    data  = load_all()
    token = data.get("api_key_protected") or ""
    if token:
        try:
            return unprotect(token)
        except Exception:
            # Different user/PC or corrupted — drop it, user re-enters key
            update(api_key_protected=None)
            return ""
    plain = str(data.get("api_key") or "")
    if plain and available():
        try:
            update(api_key_protected=protect(plain), api_key=None)
            scrub_legacy_files()
        except Exception:
            pass
    return plain


def save_api_key(key: str) -> bool:
    """Persist the key. Returns False if it could not be stored safely."""
    from core.settings import update
    if not key:
        clear_api_key()
        return True
    if available():
        try:
            update(api_key_protected=protect(key), api_key=None)
            return True
        except Exception:
            return False          # never fall back to plain text on Windows
    update(api_key=key)            # macOS/Linux: file is chmod 600
    return True


def clear_api_key() -> None:
    from core.settings import update
    update(api_key=None, api_key_protected=None)
    scrub_legacy_files()


def scrub_legacy_files() -> None:
    """Remove plain-text keys left in old *.migrated settings copies."""
    try:
        from core.settings import _legacy_settings_files
    except Exception:
        return
    for base in _legacy_settings_files():
        for path in (base, base + ".migrated"):
            try:
                if not os.path.exists(path):
                    continue
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict) and "api_key" in d:
                    d.pop("api_key", None)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(d, f, indent=2)
            except Exception:
                pass
