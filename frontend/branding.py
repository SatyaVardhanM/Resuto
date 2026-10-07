"""
frontend/branding.py — Resuto logo helpers (Resuto by Zetene).

The logo follows the theme everywhere:
  dark mode  -> dark-tile logo   (data/logo_sidebar_dark.png, data/icon_dark.ico)
  light mode -> indigo logo      (data/logo_sidebar.png,      data/icon.ico)

- set_app_id()         Windows: give the taskbar button Resuto's own identity.
- apply_window_icon()  Title-bar / taskbar icon for a window, matching the theme.
- refresh_window_icons()  Re-apply after the theme changes (all open windows).
- logo_image()         CTkImage that swaps light/dark automatically.

Every helper fails quietly: a missing logo file must never stop the app.
"""
import sys
from pathlib import Path

_windows = []   # windows whose icon follows the theme


def _data_file(name: str):
    """Find data/<name> next to resuto.exe (built app) or the project root (dev)."""
    for base in (Path(sys.executable).parent, Path(__file__).resolve().parent.parent):
        p = base / "data" / name
        if p.exists():
            return str(p)
    return None


def _dark() -> bool:
    try:
        import customtkinter as ctk
        return ctk.get_appearance_mode().lower() == "dark"
    except Exception:
        return True


def set_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Zetene.Resuto")
    except Exception:
        pass


def _apply(win) -> None:
    dark = _dark()
    ico = _data_file("icon_dark.ico" if dark else "icon.ico") or _data_file("icon.ico")
    png = (_data_file("logo_sidebar_dark.png" if dark else "logo_sidebar.png")
           or _data_file("icon.png"))
    try:
        if sys.platform == "win32" and ico:
            win.iconbitmap(ico)
        elif png:
            import tkinter as tk
            img = tk.PhotoImage(file=png)
            win.iconphoto(False, img)
            win._resuto_icon_ref = img   # keep a reference so Tk doesn't drop it
    except Exception:
        pass


def apply_window_icon(win, delay_ms: int = 0) -> None:
    """Set the window icon now (and again after delay_ms) and keep it in
    sync with the theme. CTkToplevel swaps in its own icon ~200 ms after
    it is created, so toplevel windows pass delay_ms=250."""
    if win not in _windows:
        _windows.append(win)
    _apply(win)
    if delay_ms:
        try:
            win.after(delay_ms, lambda: _apply(win))
        except Exception:
            pass


def refresh_window_icons() -> None:
    """Call after the appearance mode changes."""
    for w in list(_windows):
        try:
            if w.winfo_exists():
                _apply(w)
            else:
                _windows.remove(w)
        except Exception:
            try:
                _windows.remove(w)
            except ValueError:
                pass


def logo_image(size: int = 34):
    """The logo as a CTkImage (light: indigo, dark: dark tile), or None."""
    light = _data_file("logo_sidebar.png")
    dark = _data_file("logo_sidebar_dark.png") or light
    if not light:
        return None
    try:
        from PIL import Image
        import customtkinter as ctk
        return ctk.CTkImage(light_image=Image.open(light), dark_image=Image.open(dark),
                            size=(size, size))
    except Exception:
        return None


def install_toplevel_icons() -> None:
    """Every CTkToplevel (dialogs, pop-ups) gets the theme-matched icon."""
    try:
        import customtkinter as ctk
        if getattr(ctk.CTkToplevel, "_resuto_icons", False):
            return
        _orig = ctk.CTkToplevel.__init__

        def _init(self, *a, **kw):
            _orig(self, *a, **kw)
            apply_window_icon(self, delay_ms=250)

        ctk.CTkToplevel.__init__ = _init
        ctk.CTkToplevel._resuto_icons = True
    except Exception:
        pass
