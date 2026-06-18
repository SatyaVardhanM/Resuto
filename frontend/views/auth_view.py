"""
frontend/views/auth_view.py
───────────────────────────
RegistrationWindow — license gate shown on first launch.
Handles machine-ID registration, approval polling,
and API key entry before the main App window appears.
"""
import tkinter as tk
from tkinter import messagebox
import os
import sys
import threading

import customtkinter as ctk
import queue
import re
import time

from frontend.constants import (
    BG, BG_CARD, BG_FIELD, BG_HOVER,
    ACCENT, ACCENT_HV, DANGER, SUCCESS, WARNING, MUTED,
    FG, FG_DIM, F, _FONT_FAMILY,
)


class RegistrationWindow(ctk.CTkToplevel):
    """
    First-time registration gate.
    Fully scrollable, reactive layout.
    All fields validated before submission.
    Thread-safe via queue.
    """

    # Phone regex: accepts +1 (555) 000-0000, +15550000000, 555-000-0000 etc.
    _PHONE_RE = re.compile(
        r"^\+?1?\s*[\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}$")
    _EMAIL_RE = re.compile(
        r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Access Request")
        self.geometry("520x620")
        self.minsize(400, 500)
        self.resizable(True, True)
        self.grab_set()
        self.approved = False
        self._q       = queue.Queue()
        self._build()
        self._poll()

    @staticmethod
    def run_gate(parent) -> bool:
        """
        Show registration window and block until done.
        Returns True ONLY if explicitly approved — False on any error or close.
        """
        try:
            win = RegistrationWindow(parent)
            parent.wait_window(win)
            return win.approved is True   # strict check — must be exactly True
        except Exception:
            return False   # any error → deny access, never bypass

    # ── Queue poll — main thread only ────────────────────────────
    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "status":
                    self._status_lbl.configure(text=data, text_color=FG_DIM)
                elif kind == "error":
                    self._status_lbl.configure(text=data, text_color=DANGER)
                    self._submit_btn.configure(state="normal")
                elif kind == "timer":
                    self._timer_lbl.configure(text=data)
                elif kind == "approved":
                    self._on_approved()
                elif kind == "denied":
                    self._on_denied()
                elif kind == "timeout":
                    self._on_timeout()
        except Exception:
            pass
        if self.winfo_exists():
            self.after(50, self._poll)

    # ── UI build ──────────────────────────────────────────────────
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Outer scrollable container so nothing gets clipped
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # Card inside scroll
        card = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=14)
        card.pack(fill="x", padx=20, pady=20)
        card.grid_columnconfigure(0, weight=1)

        # ── Header ────────────────────────────────────────────────
        ctk.CTkLabel(card, text="⚡",
                     font=(_FONT_FAMILY, 40)).pack(pady=(28, 4))
        ctk.CTkLabel(card, text="Resuto",
                     font=F("heading"), text_color=FG).pack()
        ctk.CTkLabel(card,
                     text="Fill in your details to request access. The admin will approve or deny within 5 minutes.",
                     justify="center", wraplength=440).pack(pady=(6, 22))

        # ── Field builder helper ───────────────────────────────────
        def field(label_text, placeholder, optional=False):
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=24, pady=(0, 12))
            row.grid_columnconfigure(0, weight=1)
            lbl_txt = label_text + ("  (optional)" if optional else "  *")
            ctk.CTkLabel(row, text=lbl_txt, font=F("small"),
                         text_color=FG_DIM if optional else FG,
                         anchor="w").grid(row=0, column=0, sticky="w")
            var = ctk.StringVar()
            entry = ctk.CTkEntry(row, textvariable=var,
                                 placeholder_text=placeholder,
                                 height=38, font=F("body"))
            entry.grid(row=1, column=0, sticky="ew", pady=(3, 0))
            err = ctk.CTkLabel(row, text="", font=F("tiny"),
                               text_color=DANGER, anchor="w")
            err.grid(row=2, column=0, sticky="w")
            return var, entry, err

        self._first_var,  self._first_e,  self._first_err  = field("First Name",   "e.g. John")
        self._middle_var, self._middle_e, self._middle_err = field("Middle Name",  "e.g. Michael", optional=True)
        self._last_var,   self._last_e,   self._last_err   = field("Last Name",    "e.g. Doe")
        self._email_var,  self._email_e,  self._email_err  = field("Email Address","you@example.com")
        self._phone_var,  self._phone_e,  self._phone_err  = field("Phone Number", "+1 (555) 000-0000")

        # ── Error summary ─────────────────────────────────────────
        self._err_lbl = ctk.CTkLabel(card, text="", font=F("small"),
                                      text_color=DANGER, wraplength=440,
                                      justify="center")
        self._err_lbl.pack(pady=(4, 0))

        # ── Submit button ─────────────────────────────────────────
        self._submit_btn = ctk.CTkButton(
            card, text="Request Access  →",
            height=44, font=F("body_b"),
            corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_HV,
            command=self._submit)
        self._submit_btn.pack(fill="x", padx=24, pady=(14, 0))

        # ── Status + timer ────────────────────────────────────────
        self._status_lbl = ctk.CTkLabel(
            card, text="", font=F("small"),
            text_color=FG_DIM, wraplength=440, justify="center")
        self._status_lbl.pack(pady=(12, 0))

        self._timer_lbl = ctk.CTkLabel(
            card, text="", font=F("tiny"), text_color=MUTED)
        self._timer_lbl.pack(pady=(4, 28))

        # Bind Enter key to submit
        self.bind("<Return>", lambda e: self._submit())

    # ── Validation ────────────────────────────────────────────────
    def _validate(self) -> bool:
        ok = True

        # First name
        first = self._first_var.get().strip()
        if not first:
            self._first_err.configure(text="First name is required.")
            ok = False
        elif len(first) < 2:
            self._first_err.configure(text="Must be at least 2 characters.")
            ok = False
        else:
            self._first_err.configure(text="")

        # Middle name (optional — only validate if filled)
        middle = self._middle_var.get().strip()
        if middle and len(middle) < 2:
            self._middle_err.configure(text="Must be at least 2 characters.")
            ok = False
        else:
            self._middle_err.configure(text="")

        # Last name
        last = self._last_var.get().strip()
        if not last:
            self._last_err.configure(text="Last name is required.")
            ok = False
        elif len(last) < 2:
            self._last_err.configure(text="Must be at least 2 characters.")
            ok = False
        else:
            self._last_err.configure(text="")

        # Email
        email = self._email_var.get().strip()
        if not email:
            self._email_err.configure(text="Email address is required.")
            ok = False
        elif not self._EMAIL_RE.match(email):
            self._email_err.configure(text="Enter a valid email address.")
            ok = False
        else:
            self._email_err.configure(text="")

        # Phone
        phone = self._phone_var.get().strip()
        if not phone:
            self._phone_err.configure(text="Phone number is required.")
            ok = False
        elif not self._PHONE_RE.match(re.sub(r"\s+", " ", phone)):
            self._phone_err.configure(
                text="Enter a valid US phone number e.g. +1 (555) 000-0000")
            ok = False
        else:
            self._phone_err.configure(text="")

        return ok

    # ── Submit ────────────────────────────────────────────────────
    def _submit(self):
        self._err_lbl.configure(text="")
        if not self._validate():
            return

        first  = self._first_var.get().strip()
        middle = self._middle_var.get().strip()
        last   = self._last_var.get().strip()
        email  = self._email_var.get().strip()
        phone  = self._phone_var.get().strip()
        name   = " ".join(filter(None, [first, middle, last]))

        self._submit_btn.configure(state="disabled")
        self._status_lbl.configure(text="Sending request to admin...")
        threading.Thread(
            target=self._do_registration,
            args=(name, email, phone),
            daemon=True).start()

    def _do_registration(self, name: str, email: str, phone: str):
        """Background thread — only touches self._q, never widgets directly."""
        try:
            from core.license import (
                _machine_id, send_approval_request,
                poll_for_decision, store_decision, notify_admin_expired,
                _fetch_from_sheet)
            mid = _machine_id()

            # Pre-check: does this machine already have an entry in the sheet?
            existing = _fetch_from_sheet(mid)
            if existing:
                status = existing.get("status", "").lower()
                if status == "approved":
                    # Already approved — just re-store the decision locally
                    self._q.put(("status", "Machine already approved. Restoring access..."))
                    store_decision(
                        existing.get("name", name),
                        existing.get("phone", phone),
                        mid,
                        "approved",
                        email=existing.get("email", email)
                    )
                    self._q.put(("approved", None))
                    return
                elif status == "pending":
                    self._q.put(("error",
                        "Your access request is still pending approval.\n\n"
                        "Please wait for the admin to approve your previous request.\n"
                        "Do not submit again."))
                    return
                elif status == "revoked":
                    self._q.put(("denied", None))
                    return

            msg_id = send_approval_request(name, phone, mid, email=email)
            if not msg_id:
                self._q.put(("error",
                    "Could not send access request.\n\n"
                    "Please check your internet connection and try again.\n"
                    "If the problem persists, contact the developer."))
                return

            self._q.put(("status",
                "Request sent! Waiting for admin approval. You have 5 minutes."))

            def _prog(remaining):
                m, s = divmod(remaining, 60)
                self._q.put(("timer", "Time remaining: %d:%02d" % (m, s)))

            decision = poll_for_decision(mid, progress_cb=_prog)
            store_decision(name, phone, mid, decision, email=email)
            if decision == "timeout":
                notify_admin_expired(mid)
            self._q.put((decision if decision in ("approved", "denied")
                         else "timeout", None))

        except Exception as e:
            self._q.put(("error", "Error: " + str(e)))

    # ── Outcome handlers ──────────────────────────────────────────
    def _check_for_updates_after_launch(self):
        """Check for updates in background after app is running."""
        try:
            from core.updater import check_in_background
            def _on_found(info):
                self.after(0, lambda: self._show_update_dialog(info))
            check_in_background(_on_found)
        except Exception:
            pass

    def _show_update_dialog(self, info: dict):
        """Show update available dialog."""
        version  = info.get("version", "?")
        notes    = info.get("release_notes", "")
        dl_url   = info.get("download_url", "")

        popup = ctk.CTkToplevel(self)
        popup.title("Update Available")
        popup.geometry("460x300")
        popup.resizable(False, False)
        popup.grab_set()

        ctk.CTkLabel(popup,
            text="v%s is available" % version,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=ACCENT).pack(pady=(20,6))

        ctk.CTkLabel(popup,
            text="You have v%s installed." % self._get_current_version(),
            font=ctk.CTkFont(size=12),
            text_color=FG_DIM).pack()

        if notes:
            tb = ctk.CTkTextbox(popup, height=80, font=ctk.CTkFont(size=11))
            tb.pack(fill="x", padx=20, pady=10)
            tb.insert("1.0", notes)
            tb.configure(state="disabled")

        prog_lbl = ctk.CTkLabel(popup, text="", font=ctk.CTkFont(size=11),
                                  text_color=FG_DIM)
        prog_lbl.pack()

        prog = ctk.CTkProgressBar(popup, width=400)
        prog.set(0)

        btn_row = ctk.CTkFrame(popup, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(10,16))

        ctk.CTkButton(btn_row, text="Later", width=100,
            fg_color=BG_FIELD, hover_color=BG_HOVER,
            command=popup.destroy).pack(side="left")

        def _do_update():
            update_btn.configure(state="disabled", text="Downloading...")
            prog.pack(pady=(0,8))
            prog_lbl.pack()

            def _progress(pct):
                self.after(0, lambda: prog.set(pct/100))
                self.after(0, lambda: prog_lbl.configure(
                    text="Downloading... %d%%" % pct))

            def _worker():
                from core.updater import download_and_install
                ok = download_and_install(dl_url, version, _progress)
                if ok:
                    self.after(0, lambda: (
                        prog_lbl.configure(text="Installing... app will restart."),
                        self.after(2000, self.quit)))
                else:
                    self.after(0, lambda: prog_lbl.configure(
                        text="Download failed. Try again later.",
                        text_color=DANGER))
                    self.after(0, lambda: update_btn.configure(
                        state="normal", text="Update Now"))

            import threading
            threading.Thread(target=_worker, daemon=True).start()

        update_btn = ctk.CTkButton(btn_row, text="Update Now",
            width=140, fg_color=ACCENT, hover_color=ACCENT_HV,
            command=_do_update)
        update_btn.pack(side="right")

    def _get_current_version(self) -> str:
        try:
            from core.config import APP_VERSION
            return APP_VERSION
        except Exception:
            return "?"

    def _on_approved(self):
        self._status_lbl.configure(
            text="✅ Access approved! Starting the app...",
            text_color=SUCCESS)
        self._timer_lbl.configure(text="")
        self.approved = True
        self.after(1200, self.destroy)

    def _on_denied(self):
        self._status_lbl.configure(
            text="❌ Access denied. Contact the administrator "
                 "if you believe this is a mistake.",
            text_color=DANGER)
        self._timer_lbl.configure(text="")
        self.after(4000, self.destroy)

    def _on_timeout(self):
        self._status_lbl.configure(
            text="⏰ No response from admin within 5 minutes. "
                 "Please try again later.",
            text_color=WARNING)
        self._timer_lbl.configure(text="")
        self._submit_btn.configure(state="normal", text="Try Again")