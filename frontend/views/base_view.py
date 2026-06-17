"""
frontend/views/base_view.py
───────────────────────────
BaseView — shared foundation for all view classes.

Each view (RunView, HistoryView, etc.) inherits from this.
Stores a reference to the App instance so views can call
app-level helpers (nav, status bar, error log) without
importing the App class directly (avoids circular imports).
"""
import customtkinter as ctk
from frontend.constants import (
    BG, BG_CARD, BG_FIELD, BG_HOVER,
    ACCENT, ACCENT_HV, DANGER, SUCCESS, WARNING, MUTED,
    FG, FG_SOFT, FG_DIM, F,
)


class BaseView(ctk.CTkFrame):
    """
    Base class for all tab/view frames.

    Constructor params:
        parent  — the parent widget (usually the tab container frame)
        app     — reference to the top-level App instance
                  Views use self.app to call cross-view helpers.
    """

    def __init__(self, parent, app, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(parent, **kwargs)
        self.app = app   # reference to App — never circular, only called at runtime

    # ── Navigation ────────────────────────────────────────────────

    def nav_to(self, idx: int):
        """Switch the visible tab. 0=run 1=errors 2=stats 3=history 4=settings."""
        self.app._nav(idx)

    # ── Status bar helpers ────────────────────────────────────────

    def set_status(self, text: str):
        """Update the bottom status label."""
        self.app._set_status(text)

    def set_phase(self, text: str):
        """Update the phase/step label in the run header."""
        self.app._set_phase(text)

    # ── Error log ─────────────────────────────────────────────────

    def append_error(self, line: str):
        """Append a line to the Errors tab log."""
        self.app._append_error(line)

    # ── Shared widget factory ─────────────────────────────────────

    def card(self, parent=None, **kw) -> ctk.CTkFrame:
        """Return a styled card frame (BG_CARD, rounded corners)."""
        parent = parent or self
        return ctk.CTkFrame(parent, fg_color=BG_CARD,
                             corner_radius=10, **kw)

    def section_label(self, parent, text: str):
        """Render a section header label used in Settings / Run tabs."""
        lbl = ctk.CTkLabel(
            parent, text=text,
            font=F("label_b"), text_color=FG_DIM, anchor="w",
        )
        lbl.pack(fill="x", padx=20, pady=(14, 4))
        return lbl

    def separator(self, parent):
        """Thin horizontal divider."""
        return ctk.CTkFrame(parent, height=1, fg_color=BG_FIELD)