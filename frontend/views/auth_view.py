"""
frontend/views/auth_view.py
───────────────────────────
Access gate — always shows Login / Register choice on startup.

Register → Full Name + Email + API Key → wait for admin approval
Login    → Email + API Key
           - Match + approved → update machine info → open app
           - Match + pending  → wait for admin approval
           - Rejected/revoked → blocked
           - Key changed      → Update Key form (needs admin re-approval)
           - Not found        → prompt to register
"""

import os
import re
import queue
import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from frontend.branding import apply_window_icon, logo_image

from frontend.constants import (
    BG, BG_CARD, BG_FIELD, BG_HOVER, LINE, ACCENT_TXT,
    ACCENT, ACCENT_HV, DANGER, SUCCESS, WARNING, MUTED,
    FG, FG_DIM, F, _FONT_FAMILY,
)


# ── Shared widget helpers ─────────────────────────────────────────
def _entry(parent, placeholder="", show=""):
    return ctk.CTkEntry(
        parent, placeholder_text=placeholder,
        fg_color=BG_FIELD, border_color=LINE, border_width=1,
        text_color=FG, placeholder_text_color=MUTED,
        font=F("small"), show=show,
        corner_radius=8, height=38,
    )


def _btn(parent, text, command, color=None, hover=None, **kw):
    return ctk.CTkButton(
        parent, text=text, command=command,
        fg_color=color or ACCENT, hover_color=hover or ACCENT_HV,
        text_color=(FG if color in (BG_HOVER, BG_FIELD, BG_CARD) else "#FFFFFF"),
        font=F("small_b"), corner_radius=10, height=40, **kw
    )


def _label(parent, text, size="small", color=None, **kw):
    return ctk.CTkLabel(
        parent, text=text,
        font=F(size), text_color=color or FG, **kw
    )


def _divider(parent):
    ctk.CTkFrame(parent, height=1, fg_color=BG_HOVER).pack(
        fill="x", padx=24, pady=8)


# ══════════════════════════════════════════════════════════════════
#  AccessWindow — single window with Login / Register tabs
# ══════════════════════════════════════════════════════════════════
class AccessWindow(ctk.CTkToplevel):
    """
    Shows two buttons: Login / Register.
    Switching shows the relevant form inline — no separate windows.
    """

    def __init__(self, master, on_success):
        super().__init__(master)
        self._on_success = on_success
        self._q: queue.Queue = queue.Queue()
        self._mode = None           # "login" | "register" | "update_key"

        self.title("Resuto — Sign in")
        apply_window_icon(self, delay_ms=250)
        self.geometry("460x620")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.lift()
        self.focus_force()
        # The main window is hidden during sign-in, so keep this one in front
        self.attributes("-topmost", True)
        self.after(400, lambda: self.attributes("-topmost", False))
        self.after(50, self._safe_grab)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()
        self.after(50, self._poll)

    # ── Main layout ───────────────────────────────────────────────
    def _build(self):
        # ── Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=30, pady=(28, 0))
        _logo = logo_image(48)
        if _logo is not None:
            ctk.CTkLabel(hdr, text="", image=_logo).pack(pady=(0, 8))
        _label(hdr, "Welcome to Resuto", "title").pack()
        _label(hdr, "Smart resume tailoring", "small", MUTED).pack(pady=(4, 0))

        # ── Log in / Create account switch (segmented look)
        choice = ctk.CTkFrame(self, fg_color=BG_FIELD, corner_radius=10,
                              border_width=1, border_color=LINE)
        choice.pack(fill="x", padx=30, pady=(18, 14))
        choice.columnconfigure(0, weight=1)
        choice.columnconfigure(1, weight=1)

        self._login_tab_btn = ctk.CTkButton(
            choice, text="Log in",
            command=self._show_login,
            fg_color=BG_CARD, hover_color=BG_HOVER, text_color=FG,
            font=F("small_b"), corner_radius=8, height=32,
        )
        self._login_tab_btn.grid(row=0, column=0, sticky="ew", padx=(4, 2), pady=4)

        self._reg_tab_btn = ctk.CTkButton(
            choice, text="Create account",
            command=self._show_register,
            fg_color="transparent", hover_color=BG_HOVER, text_color=FG_DIM,
            font=F("small_b"), corner_radius=8, height=32,
        )
        self._reg_tab_btn.grid(row=0, column=1, sticky="ew", padx=(2, 4), pady=4)

        # ── Card area — forms swap in here
        self._card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=16,
                                  border_width=1, border_color=LINE)
        self._card.pack(fill="both", expand=True, padx=30, pady=(0, 24))

        # ── Status label (shared)
        self._status = _label(self._card, "", "small", MUTED)

        # Build both forms (hidden initially)
        self._build_login_form()
        self._build_register_form()
        self._build_update_key_form()

        # Default view
        self._show_login()

    # ── Login form ────────────────────────────────────────────────
    def _build_login_form(self):
        self._login_frame = ctk.CTkFrame(self._card, fg_color="transparent")

        _label(self._login_frame, "Welcome back", "body").pack(pady=(20, 4))
        _label(self._login_frame, "Sign in with your email and API key",
               "small", MUTED).pack(pady=(0, 16))

        self._l_email = _entry(self._login_frame, "Email Address")
        self._l_email.pack(fill="x", padx=20, pady=5)

        self._l_key = _entry(self._login_frame, "Anthropic API Key (sk-ant-...)", show="•")
        self._l_key.pack(fill="x", padx=20, pady=5)

        self._login_btn = _btn(self._login_frame, "Login", self._do_login)
        self._login_btn.pack(fill="x", padx=20, pady=(10, 6))

        _label(self._login_frame,
               "New to Resuto? Choose Create account above.",
               "tiny", MUTED).pack(pady=(4, 16))

    # ── Register form ─────────────────────────────────────────────
    def _build_register_form(self):
        self._register_frame = ctk.CTkFrame(self._card, fg_color="transparent")

        _label(self._register_frame, "Create your account", "body").pack(pady=(20, 4))
        _label(self._register_frame,
               "Enter your details to get started",
               "small", MUTED).pack(pady=(0, 16))

        self._r_name  = _entry(self._register_frame, "Full Name")
        self._r_name.pack(fill="x", padx=20, pady=5)

        self._r_email = _entry(self._register_frame, "Email Address")
        self._r_email.pack(fill="x", padx=20, pady=5)

        self._r_key   = _entry(self._register_frame,
                               "Anthropic API Key (sk-ant-...)", show="•")
        self._r_key.pack(fill="x", padx=20, pady=5)

        self._reg_btn = _btn(self._register_frame, "Create Account", self._do_register)
        self._reg_btn.pack(fill="x", padx=20, pady=(10, 6))

        _label(self._register_frame,
               "Already registered? Choose Log in above.",
               "tiny", MUTED).pack(pady=(4, 16))

    # ── Update API key form ───────────────────────────────────────
    def _build_update_key_form(self):
        self._update_frame = ctk.CTkFrame(self._card, fg_color="transparent")

        _label(self._update_frame, "Change API Key", "body").pack(pady=(20, 4))
        _label(self._update_frame,
               "Changed your Anthropic key? Enter your email and\n"
               "new key. The change needs approval before you sign in.",
               "small", MUTED, justify="center").pack(pady=(0, 16))

        self._u_email = _entry(self._update_frame, "Email Address")
        self._u_email.pack(fill="x", padx=20, pady=5)

        self._u_key = _entry(self._update_frame,
                             "New Anthropic API Key (sk-ant-...)", show="•")
        self._u_key.pack(fill="x", padx=20, pady=5)

        btn_row = ctk.CTkFrame(self._update_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(10, 6))
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)

        self._back_btn = _btn(btn_row, "← Back", self._show_login,
             color=BG_HOVER, hover=BG_FIELD)
        self._back_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self._update_btn = _btn(btn_row, "Submit for approval", self._do_update_key,
             color=WARNING, hover=("#92400E", "#C47000"))
        self._update_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))

    # ── Show / hide form panels ───────────────────────────────────
    def _show_login(self):
        self._mode = "login"
        self._register_frame.pack_forget()
        self._update_frame.pack_forget()
        self._status.pack_forget()
        self._login_frame.pack(fill="both", expand=True)
        self._status.pack(pady=(0, 8))
        self._set_status("")
        self._login_tab_btn.configure(fg_color=BG_CARD, text_color=FG)
        self._reg_tab_btn.configure(fg_color="transparent", text_color=FG_DIM)

    def _show_register(self):
        self._mode = "register"
        self._login_frame.pack_forget()
        self._update_frame.pack_forget()
        self._status.pack_forget()
        self._register_frame.pack(fill="both", expand=True)
        self._status.pack(pady=(0, 8))
        self._set_status("")
        self._reg_tab_btn.configure(fg_color=BG_CARD, text_color=FG)
        self._login_tab_btn.configure(fg_color="transparent", text_color=FG_DIM)

    def _show_update_key(self, prefill_email=""):
        self._mode = "update_key"
        self._login_frame.pack_forget()
        self._register_frame.pack_forget()
        self._status.pack_forget()
        self._update_frame.pack(fill="both", expand=True)
        self._status.pack(pady=(0, 8))
        if prefill_email:
            self._u_email.delete(0, "end")
            self._u_email.insert(0, prefill_email)
        self._set_status("")

    # ── Action: Login ─────────────────────────────────────────────
    def _do_login(self):
        email   = self._l_email.get().strip()
        api_key = self._l_key.get().strip()

        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            self._set_status("Enter a valid email address.", DANGER); return
        if not api_key:
            self._set_status("Enter your API key.", DANGER); return

        self._set_status("Verifying credentials...", MUTED)
        self._set_buttons_state("disabled")
        threading.Thread(
            target=self._bg_login, args=(email, api_key), daemon=True
        ).start()

    def _bg_login(self, email, api_key):
        try:
            from core.license import login_user
            result = login_user(email, api_key)
            if result == "pending":
                self._wait_for_approval(email, api_key)
                return
            self._q.put(("login", result, email, api_key))
        except Exception as e:
            self._q.put(("error", str(e)[:100], "", ""))

    def _wait_for_approval(self, email, api_key, reason="registration"):
        """Runs on a worker thread: show waiting UI, block until decided."""
        from core.license import poll_approval
        self._q.put(("show_waiting", reason, "", ""))
        approval = poll_approval(email)
        self._q.put(("approval", approval, email, api_key))

    # ── Action: Register ──────────────────────────────────────────
    def _do_register(self):
        name    = self._r_name.get().strip()
        email   = self._r_email.get().strip()
        api_key = self._r_key.get().strip()

        if not name:
            self._set_status("Enter your full name.", DANGER); return
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            self._set_status("Enter a valid email address.", DANGER); return
        if not api_key.startswith("sk-ant-"):
            self._set_status("API key must start with sk-ant-", DANGER); return

        self._set_status("Validating API key...", MUTED)
        self._set_buttons_state("disabled")
        threading.Thread(
            target=self._bg_register, args=(name, email, api_key), daemon=True
        ).start()

    def _bg_register(self, name, email, api_key):
        try:
            from core.license import register_user
            result = register_user(name, email, api_key)
            if result != "ok":
                self._q.put(("register", result, email, api_key))
                return
            # Show waiting UI and poll until admin approves/rejects
            self._wait_for_approval(email, api_key, "registration")
        except Exception as e:
            self._q.put(("error", str(e)[:100], "", ""))

    def _do_update_key(self):
        email   = self._u_email.get().strip()
        api_key = self._u_key.get().strip()

        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            self._set_status("Enter a valid email address.", DANGER); return
        if not api_key.startswith("sk-ant-"):
            self._set_status("API key must start with sk-ant-", DANGER); return

        self._set_status("Validating new API key...", MUTED)
        self._set_buttons_state("disabled")
        threading.Thread(
            target=self._bg_update_key, args=(email, api_key), daemon=True
        ).start()

    def _bg_update_key(self, email, api_key):
        try:
            from core.license import update_api_key
            result = update_api_key(email, api_key)
            if result == "pending":
                self._wait_for_approval(email, api_key, "key_change")
                return
            self._q.put(("update_key", result, email, api_key))
        except Exception as e:
            self._q.put(("error", str(e)[:100], "", ""))

    # ── Poll result queue (main thread) ──────────────────────────
    def _poll(self):
        try:
            msg = self._q.get_nowait()
            kind, result, email, api_key = msg

            self._set_buttons_state("normal")
            self.update_idletasks()   # force UI refresh before showing status

            if kind == "show_waiting":
                self._set_buttons_state("disabled")
                self.update_idletasks()
                self._waiting_reason = result
                self._dot_count = 0
                self._animate_dots()

            elif kind == "approval":
                self._stop_dots()
                if result == "approved":
                    self._grant(api_key)
                elif result == "rejected":
                    self._set_buttons_state("normal")
                    self._set_status("Your request was not approved.\n"
                                     "Contact support if you think this is a mistake.", DANGER)
                elif result == "revoked":
                    self._set_buttons_state("normal")
                    self._set_status("Access to this account has been revoked.", DANGER)
                elif result == "timeout":
                    self._set_buttons_state("normal")
                    self._set_status("Still waiting for approval.\n"
                                     "You can close Resuto and log in again later.", WARNING)
                else:
                    self._set_buttons_state("normal")
                    self._set_status("Could not check approval. Check connection.", DANGER)

            elif kind == "login":

                if result == "ok":
                    self._grant(api_key)
                elif result == "key_changed":
                    self._set_status(
                        "This API key doesn't match the account.\n"
                        "Changed keys? Submit the new one for approval.", WARNING)
                    # Switch to update key form pre-filled with email
                    self.after(1200, lambda: self._show_update_key(email))
                elif result == "not_registered":
                    self._set_status(
                        "No account found for this email.\n"
                        "Please register first.", DANGER)
                elif result == "rejected":
                    self._set_status("Your registration was not approved.", DANGER)
                elif result == "revoked":
                    self._set_status("Access to this account has been revoked.", DANGER)
                elif result == "creds_error":
                    self._set_status(
                        "Server credentials error.\n"
                        "Please contact support.", DANGER)
                elif result == "network_error":
                    self._set_status(
                        "Couldn't reach the server.\n"
                        "Check your internet and try again.", DANGER)
                elif result == "quota_error":
                    self._set_status(
                        "Server busy. Please try again in a moment.", WARNING)
                else:
                    self._set_status(
                        "Something went wrong. Please try again.", DANGER)

            elif kind == "register":
                if result == "already_exists":
                    self._set_status(
                        "This email is already registered.\n"
                        "Please use Login instead.", WARNING)
                elif result == "invalid_key":
                    self._set_status(
                        "API key is invalid. Please check and try again.", DANGER)
                elif result == "creds_error":
                    self._set_status(
                        "Server credentials error.\n"
                        "Please contact support or try again later.", DANGER)
                elif result == "network_error":
                    self._set_status(
                        "Connection timed out.\n"
                        "Check your internet and try again.", DANGER)
                elif result == "quota_error":
                    self._set_status(
                        "Server busy. Please try again in a moment.", WARNING)
                else:
                    self._set_status(
                        "Something went wrong.\n"
                        "Check your internet connection.", DANGER)

            elif kind == "update_key":
                if result == "revoked":
                    self._set_status("Access to this account has been revoked.", DANGER)
                elif result == "invalid_key":
                    self._set_status(
                        "New API key is invalid. Please check and try again.", DANGER)
                elif result == "not_registered":
                    self._set_status(
                        "No account found for this email.", DANGER)
                elif result == "creds_error":
                    self._set_status(
                        "Server credentials error.\n"
                        "Please contact support.", DANGER)
                elif result == "network_error":
                    self._set_status(
                        "Connection timed out.\n"
                        "Check your internet and try again.", DANGER)
                else:
                    self._set_status(
                        "Could not update. Check your connection.", DANGER)

            elif kind == "error":
                self._set_status(
                    f"Error: {result}", DANGER)
                self._set_buttons_state("normal")

        except queue.Empty:
            pass
        self.after(50, self._poll)

    # ── Grant access ──────────────────────────────────────────────
    def _grant(self, api_key: str):
        os.environ["ANTHROPIC_API_KEY"] = api_key
        self._stop_dots()
        self.after(0, lambda: self._set_status("Access granted!", SUCCESS))
        self.after(700, lambda: (self.destroy(), self._on_success(api_key)))

    # ── Helpers ───────────────────────────────────────────────────
    def _set_status(self, msg, color=None):
        self._status.configure(text=msg, text_color=color or MUTED)

    def _set_buttons_state(self, state):
        """Enable/disable only the action buttons — never touches status label."""
        for btn in [
            getattr(self, '_login_btn',  None),
            getattr(self, '_reg_btn',    None),
            getattr(self, '_update_btn', None),
            getattr(self, '_back_btn',   None),
        ]:
            if btn is not None:
                try:
                    btn.configure(state=state)
                except Exception:
                    pass

    def _show_waiting(self):
        """No longer called directly — handled via queue in _poll."""
        pass

    def _animate_dots(self):
        if not self.winfo_exists():
            return
        dots = "." * (self._dot_count % 4)
        if getattr(self, "_waiting_reason", "") == "key_change":
            msg = "Your new API key is waiting for approval" + dots
        else:
            msg = "Your account is waiting for approval" + dots
        msg += "\nYou can leave this open, or close Resuto and log in later."
        self._set_status(msg, WARNING)
        self._dot_count += 1
        self._dot_timer = self.after(600, self._animate_dots)

    def _stop_dots(self):
        if hasattr(self, "_dot_timer"):
            try:
                self.after_cancel(self._dot_timer)
            except Exception:
                pass

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            pass   # grab can fail if the window is not yet viewable

    def _on_close(self):
        self._stop_dots()
        self.master.destroy()



# ── Entry point called from app.py ───────────────────────────────
def run_access_gate(master, on_success):
    AccessWindow(master, on_success)