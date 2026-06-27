"""
frontend/views/dialogs.py
─────────────────────────
Standalone popup windows:
  - IntakeWindow      — edit resume_data.xml
  - ProfileViewWindow — read-only profile viewer
  - ReviewWindow      — review / download generated resume
"""
import tkinter as tk
from tkinter import messagebox
import os
import re
import subprocess
import threading
from pathlib import Path

import customtkinter as ctk
import queue
import time

from frontend.constants import (
    BG, BG_CARD, BG_FIELD, BG_HOVER,
    ACCENT, ACCENT_HV, DANGER, SUCCESS, MUTED,
    FG, FG_SOFT, FG_DIM, F,
)


class IntakeWindow(ctk.CTkToplevel):
    """
    Resume intake as a natural chat conversation.
    Claude reads the resume, asks follow-up questions one by one
    in a friendly conversational tone, then builds resume_data.xml.

    Flow:
      1. Extract text from PDF/DOCX
      2. Claude sends an opening message + first question
      3. User replies in the chat box — feels like texting
      4. Claude follows up naturally based on each answer
      5. When enough info is gathered, Claude builds the XML
    """

    BOT_NAME  = "Alex"
    MAX_TURNS = 60   # longer conversation — thoroughness over speed

    def _system_prompt(self) -> str:
        return f"""You are {self.BOT_NAME}, a resume coach and career advisor having a real conversation with someone to help them build the strongest possible resume from their existing experience.

You have already read their resume. Your job is NOT to run through a checklist of questions — it is to have a genuine back-and-forth conversation, understand their work deeply, and help them articulate their experience better than they currently do on paper.

━━━ WHO YOU ARE ━━━
You are warm, direct, and a little witty — like a smart friend who happens to know a lot about hiring. You:
- Explain WHY something matters for a resume when the person seems unsure
- Use plain conversational language, never corporate speak
- Occasionally make a light joke or use an emoji — natural, not forced
- React genuinely to what people tell you ("Oh that's actually really impressive — most people don't think to mention that")
- If someone gives a vague answer, you explain WHY specifics help and ask again in a different way — never just repeat the question
- If someone doesn't understand what you're asking, you step back and explain it differently with an example

━━━ YOUR GOAL ━━━
Build a complete, enhanced picture of this person's professional experience so their resume becomes as strong as possible. This means going deeper than what's already written.

For EVERY job they've had, you want to understand:
- What the team/project/product actually was — was "CKYC" a team name? An internal tool? A client-facing app? Don't assume
- The real scale: how many users, records, requests, transactions? Even rough numbers help
- The specific problems THEY solved — not just what the company did
- What changed because of their work — faster, cheaper, more reliable, fewer errors?
- What they built vs. what they maintained vs. what they improved
- Technologies they actually used hands-on, not just ones they know in theory

For PROJECTS, you want:
- What problem it actually solved (not just what it does)
- Who used it and whether it was used in production or just a demo
- Any measurable outcomes — accuracy, time saved, scale

For SKILLS:
- What they use regularly vs. what they've tried once
- Anything they're proud of that isn't listed

━━━ HOW TO HAVE THE CONVERSATION ━━━
- One topic at a time — don't bombard them with multiple questions
- Acknowledge and react to each answer before moving on
- If an answer reveals something interesting, dig into it before changing topic
- Don't follow a script — go where the conversation naturally takes you
- If they mention something in passing that sounds important, catch it: "Wait — you said it processed 60K images. Was that in production? Because that's actually a strong number worth highlighting"
- Match their energy — if they're being casual and chatty, be casual back; if they're being brief, don't lecture them

━━━ WHAT NOT TO DO ━━━
- Do NOT ask about target job roles, visa status, salary, or availability
- Do NOT ask about things already clearly stated in the resume
- Do NOT rush — thoroughness matters more than speed
- Do NOT repeat a question in the same words if they didn't understand it
- Do NOT move on if an answer is too vague to be useful — gently push

━━━ OPENING MESSAGE (very first reply only) ━━━
Your opening message should:
1. Greet them by name (from the resume)
2. Say something genuinely specific and encouraging about their background — not generic praise
3. Set honest expectations: this conversation will take around 30 minutes (maybe more if they have a lot to cover), and it's worth it — a rushed resume shows, a thorough one gets interviews
4. Start with one warm opening question to get the conversation going

━━━ WHEN YOU'RE DONE ━━━
Only signal you're ready to generate when you genuinely feel you have a thorough understanding of their work — not just surface-level info. This usually takes 20-40 exchanges for a professional with 2+ years of experience.

When you're satisfied, wrap up warmly and end your message with the exact token [[READY]] on its own — the system uses this to trigger profile generation. Do NOT mention or explain this token.

Good wrap-up: "Alright, I think I've got a really solid picture of everything you've done — this is going to make a strong resume. Give me a moment to put it all together! [[READY]]"

━━━ RESPONSE LENGTH ━━━
Keep responses conversational — 2-5 sentences is usually right. Longer if you're explaining something. Never bullet-point your messages; this is a chat, not a report."""

    def __init__(self, parent, resume_path: str, api_key: str,
                 xml_out: str, on_done=None):
        super().__init__(parent)
        self.title("Resume Chat")
        self.geometry("760x600")
        self.resizable(True, True)
        self.minsize(620, 520)
        self.grab_set()

        self._resume_path = resume_path
        self._api_key     = api_key
        self._xml_out     = xml_out
        self._on_done     = on_done
        self._resume_text = ""
        self._history     = []   # [{role, content}] for Claude API
        self._turn        = 0
        self._done        = False

        self._build()
        self._start_extraction()

    # ── UI ────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=52)
        hdr.grid(row=0, column=0, sticky="ew"); hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text=f"💬  Chat with {self.BOT_NAME}",
                     font=F("heading"), text_color=FG).pack(side="left", padx=20)
        self._status_lbl = ctk.CTkLabel(hdr, text="Reading your resume...",
                                         font=F("small"), text_color=FG_DIM)
        self._status_lbl.pack(side="right", padx=16)

        # Chat area (scrollable)
        self._chat_scroll = ctk.CTkScrollableFrame(
            self, fg_color=BG, corner_radius=0)
        self._chat_scroll.grid(row=1, column=0, sticky="nsew")
        self._chat_scroll.grid_columnconfigure(0, weight=1)

        # Input area
        inp = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=90)
        inp.grid(row=2, column=0, sticky="ew"); inp.grid_propagate(False)
        inp.grid_columnconfigure(0, weight=1)

        self._input = ctk.CTkTextbox(inp, height=52, font=F("body"),
                                      fg_color=BG_FIELD, text_color=FG,
                                      corner_radius=8)
        self._input.grid(row=0, column=0, sticky="ew", padx=(14, 8), pady=18)
        self._input.bind("<Return>",       self._on_enter)
        self._input.bind("<Shift-Return>", lambda e: None)  # allow newline

        self._send_btn = ctk.CTkButton(
            inp, text="Send  ↵", width=100, height=52,
            font=F("label_b"), corner_radius=8,
            command=self._send, state="disabled")
        self._send_btn.grid(row=0, column=1, padx=(0, 14), pady=18)

    def _on_enter(self, event):
        # Enter sends, Shift+Enter inserts newline
        if not (event.state & 0x1):   # Shift not held
            self._send()
            return "break"

    # ── Chat rendering ────────────────────────────────────────────

    def _add_bot_message(self, text: str):
        """Render a bot message bubble on the left."""
        row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(6, 2))
        row.grid_columnconfigure(1, weight=1)

        # Avatar
        ctk.CTkLabel(row, text="🤖", font=F("body"),
                     width=32).grid(row=0, column=0, sticky="nw", padx=(0, 8))

        bubble = ctk.CTkFrame(row, fg_color=BG_CARD, corner_radius=12)
        bubble.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(bubble, text=text, font=F("body"),
                     text_color=FG, wraplength=460,
                     justify="left", anchor="w"
                     ).pack(padx=14, pady=10)
        self._scroll_to_bottom()

    def _add_user_message(self, text: str):
        """Render a user message bubble on the right."""
        row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(2, 6))
        row.grid_columnconfigure(0, weight=1)

        bubble = ctk.CTkFrame(row, fg_color=ACCENT, corner_radius=12)
        bubble.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(bubble, text=text, font=F("body"),
                     text_color="white", wraplength=420,
                     justify="left", anchor="w"
                     ).pack(padx=14, pady=10)
        self._scroll_to_bottom()

    def _add_typing_indicator(self) -> ctk.CTkFrame:
        """Show '...' while bot is thinking. Returns the frame to remove later."""
        row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(4, 2), anchor="w")
        ctk.CTkLabel(row, text="🤖", font=F("body"), width=32).pack(side="left")
        bubble = ctk.CTkFrame(row, fg_color=BG_CARD, corner_radius=12)
        bubble.pack(side="left")
        ctk.CTkLabel(bubble, text="  ...  ", font=F("body"),
                     text_color=MUTED).pack(padx=14, pady=10)
        self._scroll_to_bottom()
        return row

    def _remove_typing(self, indicator):
        try: indicator.destroy()
        except Exception: pass

    def _scroll_to_bottom(self):
        self.after(50, lambda: self._chat_scroll._parent_canvas.yview_moveto(1.0))

    # ── Extraction ────────────────────────────────────────────────

    def _start_extraction(self):
        self._status_lbl.configure(text="Reading your resume...")
        threading.Thread(target=self._extract_text, daemon=True).start()

    def _extract_text(self):
        try:
            from api.intake import read_resume_file, _PDF_ERROR_PREFIX
        except ImportError:
            from api.intake import read_resume_file
            _PDF_ERROR_PREFIX = "PDF_ERROR:"

        try:
            self.after(0, lambda: self._status_lbl.configure(
                text="Reading your resume — please wait..."))
            text = read_resume_file(self._resume_path)

            # Check for specific PDF errors
            if text and text.startswith(_PDF_ERROR_PREFIX):
                err_msg = text[len(_PDF_ERROR_PREFIX):].strip()
                self.after(0, lambda m=err_msg: self._show_error(m))
                return

            if not text or not text.strip():
                self.after(0, lambda: self._show_error(
                    "No text could be extracted from your file.\n\n"
                    "Common reasons:\n"
                    "  - Scanned/image-only PDF\n"
                    "  - Password-protected PDF\n"
                    "  - Corrupted file\n\n"
                    "Try converting to a text-based DOCX and upload that instead."))
                return

            self._resume_text = text
            self.after(0, self._show_choice)
        except Exception as e:
            _e = str(e)
            self.after(0, lambda _e=_e: self._show_error(
                f"Could not read file:\n{_e}"))

    # ── Choice screen ────────────────────────────────────────────

    def _show_choice(self):
        """
        Show two options after PDF extraction:
          Use as-is   → Claude builds XML directly from PDF (no chat)
          Enhance     → Chat with Alex for a richer XML
        """
        self.title("How would you like to proceed?")

        # Build choice screen — overlay on the existing window
        self._choice_frame = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self._choice_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        inner = ctk.CTkFrame(self._choice_frame, fg_color=BG_CARD,
                              corner_radius=14)
        inner.place(relx=0.5, rely=0.5, anchor="center",
                    relwidth=0.88, relheight=0.85)
        inner.grid_columnconfigure((0,1), weight=1, uniform="col")
        inner.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(inner, text="✅  Resume Uploaded",
                     font=F("heading"), text_color=SUCCESS).grid(
                     row=0, column=0, columnspan=2, pady=(28,4))
        ctk.CTkLabel(inner,
                     text="How would you like to build your profile?",
                     font=F("body"), text_color=FG_DIM).grid(
                     row=1, column=0, columnspan=2, pady=(0,20))

        # ── Option A: Use as-is ───────────────────────────────────
        card_a = ctk.CTkFrame(inner, fg_color=BG_HOVER, corner_radius=10)
        card_a.grid(row=2, column=0, sticky="nsew", padx=(20,8), pady=(0,20))
        card_a.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card_a, text="⚡  Use as-is",
                     font=F("body_b"), text_color=FG).pack(pady=(20,6))
        ctk.CTkLabel(card_a,
                     text="Claude reads your PDF and builds your profile directly.\n\nFast — takes about 30 seconds.\nGood if your resume is already well written.",
                     font=F("small"), text_color=FG_DIM,
                     wraplength=200, justify="center").pack(padx=16, pady=(0,16), fill="x")
        ctk.CTkButton(card_a, text="Use as-is  →",
                      height=38, font=F("body_b"),
                      fg_color=ACCENT, hover_color=ACCENT_HV,
                      command=self._use_as_is).pack(
                      fill="x", padx=16, pady=(0,20))

        # ── Option B: Enhance with Alex ───────────────────────────
        card_b = ctk.CTkFrame(inner, fg_color=BG_HOVER, corner_radius=10)
        card_b.grid(row=2, column=1, sticky="nsew", padx=(8,20), pady=(0,20))
        card_b.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card_b, text="💬  Enhance with Alex",
                     font=F("body_b"), text_color=FG).pack(pady=(20,6))
        ctk.CTkLabel(card_b,
                     text="Chat with Alex to add context your PDF doesn't capture.\n\nTakes 20-30 minutes.\nBest for stronger bullets.",
                     font=F("small"), text_color=FG_DIM,
                     wraplength=200, justify="center").pack(padx=16, pady=(0,16), fill="x")
        ctk.CTkButton(card_b, text="Chat with Alex  →",
                      height=38, font=F("body_b"),
                      fg_color=BG_CARD, hover_color=BG_HOVER,
                      command=self._enhance_with_alex).pack(
                      fill="x", padx=16, pady=(0,20))

    def _use_as_is(self):
        """Build XML directly from PDF using Claude — no conversation."""
        self._choice_frame.destroy()
        self.title("Building your profile...")
        self._status_lbl.configure(text="Building profile from resume...")
        threading.Thread(target=self._build_xml_direct, daemon=True).start()


    def _check_resume_metrics(self, xml_str: str) -> list:
        """Check XML for missing fields. Returns list of missing names."""
        import re as _re
        missing = []
        if not _re.search(r'<(?:years_experience|experience_years)[^>]*>[^<]{1,}', xml_str, _re.I):
            missing.append('years_experience')
        if not _re.search(r'<skill[s]?[^>]*>[^<]{2,}', xml_str, _re.I):
            missing.append('skills')
        return missing

    def _show_metric_popup(self, missing: list, on_continue=None, on_edit=None):
        """Show warning popup about missing metrics with Continue/Edit choice."""
        import tkinter.messagebox as _mb
        msg = (
            "Your profile is missing some fields:\n\n" +
            "\n".join(f"  \u2022 {f}" for f in missing) +
            "\n\nContinue saving anyway?"
        )
        if _mb.askyesno("Missing Fields", msg, icon="warning"):
            if on_continue: on_continue()
        else:
            if on_edit: on_edit()

    def _complete_alex_save(self):
        """Finalise save after Alex enhancement."""
        xml = getattr(self, "_xml_str", None)
        if xml:
            self._on_success(xml)
        else:
            self._show_error("Enhancement finished but XML is empty.")

    def _abort_and_edit(self):
        """Abort save and prompt user to edit their profile."""
        self._show_error(
            "Please fill in the missing fields in your resume_data.xml\n"
            "then re-upload the file."
        )

    def _enhance_with_alex(self):
        """Remove the choice screen and start the chat flow."""
        self._choice_frame.destroy()
        self.title("Resume Chat")
        self._start_chat()

    def _build_xml_direct(self):
        """
        Claude reads the full resume text and builds resume_data.xml
        in one shot — no conversation needed.
        Uses PROMPT_INTAKE_XML from prompts.db.
        """
        try:
            import anthropic as _ant
            from api.prompts import (get_prompt, PROMPT_INTAKE_XML,
                                     PROMPT_INTAKE_XML_DIRECT,
                                     seed_generic_prompts)
            from core.config import AI_MODEL

            # Ensure prompts are seeded — seed if missing
            seed_generic_prompts()

            client = _ant.Anthropic(api_key=self._api_key)

            self.after(0, lambda: self._status_lbl.configure(
                text="Claude is analysing your resume..."))

            # Use the direct prompt — no Q&A context expected
            prompt = get_prompt(PROMPT_INTAKE_XML_DIRECT) or get_prompt(PROMPT_INTAKE_XML)
            if not prompt:
                raise ValueError(
                    "intake_xml_direct prompt missing. Run: python scripts/update_prompts.py")
            user_msg = (
                "Here is the full resume PDF text:\n\n"
                "---\n" + self._resume_text + "\n---\n\n"
                "Build the complete resume_data.xml from this resume text only."
            )

            msg = client.messages.create(
                model=AI_MODEL,
                max_tokens=4000,
                timeout=120.0,
                system=prompt,
                messages=[{"role": "user", "content": user_msg}]
            )
            xml_text = msg.content[0].text.strip()

            # Strip markdown fences if present
            if xml_text.startswith("```"):
                lines = xml_text.split("\n")
                xml_text = "\n".join(
                    l for l in lines
                    if not l.strip().startswith("```"))

            # Validate it looks like XML
            if not xml_text.startswith("<?xml"):
                idx = xml_text.find("<?xml")
                if idx >= 0:
                    xml_text = xml_text[idx:]
                else:
                    raise ValueError(
                        "Claude did not return valid XML.\n"
                        "Try the Enhance with Alex option instead.")

            # Save XML
            import os
            os.makedirs(os.path.dirname(self._xml_out), exist_ok=True)
            with open(self._xml_out, "w", encoding="utf-8") as f:
                f.write(xml_text)

            self.after(0, lambda: self._status_lbl.configure(
                text="Profile saved — regenerating personalised prompts..."))

            # Regenerate prompts immediately with Claude
            self._regenerate_prompts(
                xml_path=self._xml_out,
                api_key=self._api_key)

            self.after(0, self._on_direct_done)

        except Exception as e:
            _e = str(e)
            self.after(0, lambda _e=_e: self._show_error(
                f"Could not build profile:\n{_e}\n\n"
                f"Try the Enhance with Alex option instead."))

    def _regenerate_prompts(self, xml_path: str = None, api_key: str = None):
        """
        Regenerate personalised prompts immediately from the new XML.
        Runs synchronously — call from a background thread.
        """
        try:
            import anthropic as _ant
            from api.prompts import (setup_all_prompts, delete_prompt,
                                     PROMPT_RELEVANCE_CHECK, PROMPT_RESUME_TAILOR,
                                     get_profile_hash, store_profile_hash,
                                     seed_generic_prompts)
            from core.profile import load_profile_from_xml

            # Seed generic prompts first (intake_xml_direct etc.)
            seed_generic_prompts()

            # Load profile from XML
            path    = xml_path or self._xml_out
            profile = load_profile_from_xml(path)
            if not profile:
                print("[WARN] Profile empty — skipping prompt regeneration")
                return

            # Get API key
            key = api_key or getattr(self, "_api_key", "") or self.master._api_var.get().strip()
            if not key or not key.startswith("sk-ant-"):
                # No key available — just delete old prompts so they
                # regenerate on next bot run
                delete_prompt(PROMPT_RELEVANCE_CHECK)
                delete_prompt(PROMPT_RESUME_TAILOR)
                print("[OK] Prompts cleared — will regenerate on next bot run")
                return

            client = _ant.Anthropic(api_key=key)

            # Delete old personal prompts then regenerate fresh
            delete_prompt(PROMPT_RELEVANCE_CHECK)
            delete_prompt(PROMPT_RESUME_TAILOR)

            result = setup_all_prompts(profile, client, overwrite=False)
            store_profile_hash(get_profile_hash(profile))

            print("[OK] Personalised prompts regenerated (%d created)"
                  % result.get("created", 0))

        except Exception as e:
            print("[WARN] Prompt regeneration failed: %s" % e)
            # Non-fatal — prompts will regenerate on next bot run

    def _on_direct_done(self):
        """Show success screen after direct XML build."""
        self._status_lbl.configure(text="Profile ready!")
        # Show a simple done message
        done_frame = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        done_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        inner = ctk.CTkFrame(done_frame, fg_color=BG_CARD, corner_radius=14)
        inner.place(relx=0.5, rely=0.5, anchor="center",
                    relwidth=0.72, relheight=0.55)
        ctk.CTkLabel(inner, text="✅", font=("Segoe UI", 40)).pack(pady=(28,4))
        ctk.CTkLabel(inner, text="Profile built successfully!",
                     font=F("heading"), text_color=SUCCESS).pack()
        ctk.CTkLabel(inner,
                     text="Your resume_data.xml has been created. Personalised prompts have been reset. You are ready to run the bot.",
                     font=F("small"), text_color=FG_DIM,
                     wraplength=340, justify="center").pack(pady=(10,20))
        ctk.CTkButton(inner, text="Close", height=38,
                      font=F("body_b"),
                      command=self._finish).pack(padx=40, pady=(0,24))

    def _finish(self):
        if self._on_done:
            self._on_done()
        self.destroy()

    # ── Chat engine ───────────────────────────────────────────────

    def _start_chat(self):
        """Send the resume to Claude and get the opening message."""
        self._status_lbl.configure(text="Alex is reading your resume...")
        self._send_btn.configure(state="disabled")

        # Build the initial user message (resume context)
        # Add it to history so subsequent turns have proper alternating structure
        initial_msg = (
            f"Here is my resume:\n\n---\n{self._resume_text}\n---\n\n"
            "Please start our conversation."
        )
        self._history.append({"role": "user", "content": initial_msg})

        indicator = self._add_typing_indicator()
        threading.Thread(
            target=self._call_claude,
            args=(indicator,),
            daemon=True).start()

    def _send(self):
        text = self._input.get("1.0", "end").strip()
        if not text or self._done:
            return
        self._input.delete("1.0", "end")
        self._send_btn.configure(state="disabled")
        self._add_user_message(text)
        self._history.append({"role": "user", "content": text})
        self._turn += 1
        indicator = self._add_typing_indicator()
        threading.Thread(
            target=self._call_claude,
            args=(indicator,),
            daemon=True).start()

    def _call_claude(self, indicator):
        """Background thread: call Claude with full conversation history."""
        try:
            import anthropic as _ant
            try:
                from api.prompts import seed_generic_prompts
                from core.config import AI_MODEL
            except ModuleNotFoundError:
                from api.prompts import seed_generic_prompts
                from core.config import AI_MODEL
            seed_generic_prompts()

            client = _ant.Anthropic(api_key=self._api_key)

            # history always starts with user (resume context) — valid for API
            resp = client.messages.create(
                model=AI_MODEL,
                max_tokens=600,
                system=self._system_prompt(),
                messages=self._history)

            reply = resp.content[0].text.strip()
            ready = "[[READY]]" in reply
            reply_clean = reply.replace("[[READY]]", "").strip()

            self.after(0, lambda: self._on_bot_reply(indicator, reply_clean, ready))

        except Exception as e:
            err = str(e)
            self.after(0, lambda ind=indicator, e=err: self._on_bot_error(ind, e))

    def _on_bot_reply(self, indicator, reply: str, ready: bool):
        self._remove_typing(indicator)
        self._add_bot_message(reply)
        self._history.append({"role": "assistant", "content": reply})
        self._status_lbl.configure(text=f"Turn {self._turn + 1}")

        if ready or self._turn >= self.MAX_TURNS:
            self._status_lbl.configure(text="Building your profile...")
            self._send_btn.configure(state="disabled")
            self._done = True
            self.after(800, self._generate_xml)
        else:
            self._send_btn.configure(state="normal")
            self._input.focus_set()

    def _on_bot_error(self, indicator, err: str):
        self._remove_typing(indicator)
        self._add_bot_message(
            f"Something went wrong 😬\n\n`{err}`\n\n"
            "Close this window and try again.")
        self._status_lbl.configure(text="Error — see message above")

    def _generate_xml(self):
        self._add_bot_message(
            "Perfect — I've got everything I need. Now I'm going to build your "
            "profile from our whole conversation. This usually takes about a minute "
            "so sit tight... ☕")
        threading.Thread(target=self._build_xml, daemon=True).start()

    def _build_xml(self):
        try:
            import anthropic as _ant
            try:
                from api.prompts import get_prompt, seed_generic_prompts, PROMPT_INTAKE_XML
                from core.config import AI_MODEL
            except ModuleNotFoundError:
                from api.prompts import get_prompt, seed_generic_prompts, PROMPT_INTAKE_XML
                from core.config import AI_MODEL
            seed_generic_prompts()

            client = _ant.Anthropic(api_key=self._api_key)

            # Full conversation transcript for the XML builder
            convo = "\n".join(
                f"{'User' if m['role']=='user' else 'Alex'}: {m['content']}"
                for m in self._history)

            resp = client.messages.create(
                model=AI_MODEL,
                max_tokens=8000,
                system=get_prompt(PROMPT_INTAKE_XML),
                messages=[{"role": "user", "content":
                    f"Resume:\n---\n{self._resume_text}\n---\n\n"
                    f"Conversation:\n---\n{convo}\n---\n\n"
                    "Build the complete resume_data.xml now."}])

            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = "\n".join(raw.split("\n")[:-1])
            xml_str = raw.strip()

            import xml.etree.ElementTree as ET
            from api.intake import sanitize_xml
            ET.fromstring(sanitize_xml(xml_str))   # validate
            from pathlib import Path
            Path(self._xml_out).parent.mkdir(parents=True, exist_ok=True)
            Path(self._xml_out).write_text(xml_str, encoding="utf-8")

            # Check for missing metrics before completing
            missing = self._check_resume_metrics(xml_str)
            if missing:
                self.after(0, lambda m=missing: self._show_metric_popup(
                    m,
                    on_continue=self._complete_alex_save,
                    on_edit=self._abort_and_edit))
                return

            self._complete_alex_save()
        except Exception as e:
            err2=str(e); self.after(0, lambda err2=err2: self._on_xml_error(err2))

    def _on_success(self):
        self._status_lbl.configure(text="Profile saved ✓")
        self._add_bot_message(
            "Done! 🎉 Your profile has been saved.\n\n"
            "You can close this window — head back to Settings "
            "to view your profile or run the bot whenever you're ready.")
        finish_btn = ctk.CTkButton(
            self._chat_scroll, text="Close  ✓",
            fg_color=SUCCESS, hover_color="#16A34A",
            font=F("label_b"), height=40, width=160,
            command=self._finish)
        finish_btn.pack(pady=16)
        self._scroll_to_bottom()

    def _on_xml_error(self, err: str):
        self._status_lbl.configure(text="Error")
        self._add_bot_message(
            f"Ugh, something went wrong building the XML 😬\n\n{err}\n\n"
            "Try closing and uploading your resume again.")






    def _show_error(self, msg: str):
        self._status_lbl.configure(text="Error")
        self._add_bot_message(f"Oops! {msg}")


class ProfileViewWindow(ctk.CTkToplevel):
    """Displays the current resume profile parsed from resume_data.xml."""

    def __init__(self, parent, xml_path: str):
        super().__init__(parent)
        self.title("Resume Profile")
        self.geometry("700x620")
        self.resizable(True, True)
        self.minsize(560, 400)
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=52)
        hdr.grid(row=0, column=0, sticky="ew"); hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="Resume Profile",
                     font=F("heading"), text_color=FG
                     ).pack(side="left", padx=20, pady=10)
        ctk.CTkButton(hdr, text="Close", width=80, height=32,
                      font=F("small"), fg_color=BG_FIELD,
                      hover_color=BG_HOVER,
                      command=self.destroy
                      ).pack(side="right", padx=16, pady=10)

        # Scrollable content
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        try:
            from api.intake import safe_parse_xml_file
            root = safe_parse_xml_file(xml_path)
            self._render_profile(scroll, root)
        except Exception as e:
            ctk.CTkLabel(scroll, text=f"Could not read profile: {e}",
                         font=F("body"), text_color=DANGER
                         ).pack(padx=24, pady=40)

    def _section(self, parent, title: str):
        ctk.CTkLabel(parent, text=title, font=F("label_b"),
                     text_color=ACCENT, anchor="w"
                     ).pack(fill="x", padx=24, pady=(18, 4))
        ctk.CTkFrame(parent, height=1, fg_color=BG_HOVER
                     ).pack(fill="x", padx=24, pady=(0, 6))

    def _row(self, parent, label: str, value: str):
        if not (value or "").strip(): return
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=2)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=label, font=F("small_b"),
                     text_color=MUTED, width=130, anchor="w"
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row, text=value, font=F("small"),
                     text_color=FG_SOFT, anchor="w", wraplength=450,
                     justify="left"
                     ).grid(row=0, column=1, sticky="w")

    def _render_profile(self, parent, root):
        # Personal info
        self._section(parent, "Personal Info")
        for tag, label in [("name","Name"),("email","Email"),
                            ("phone","Phone"),("location","Location"),
                            ("linkedin","LinkedIn"),("github","GitHub")]:
            self._row(parent, label, root.findtext(f".//{tag}") or "")

        # Summary
        summary = root.findtext(".//summary") or ""
        if summary:
            self._section(parent, "Summary")
            ctk.CTkLabel(parent, text=summary, font=F("small"),
                         text_color=FG_SOFT, wraplength=620,
                         justify="left", anchor="w"
                         ).pack(fill="x", padx=24, pady=(0, 4))

        # Skills
        skills = root.find(".//skills")
        if skills is not None:
            self._section(parent, "Skills")
            for cat in skills:
                # Category name is in the 'name' attribute, not the tag itself
                cat_name = (cat.attrib.get("name","")
                             or cat.attrib.get("type","")
                             or cat.tag).replace("_"," ").title()
                # Items are <skill> children; fall back to direct text children
                items = [c.text for c in cat
                          if c.text and c.tag in ("skill","item","technology")]
                if not items:
                    items = [c.text for c in cat if c.text]
                if items:
                    self._row(parent, cat_name, ", ".join(items))

        # Experience
        exp = root.find(".//experience")
        if exp is not None:
            self._section(parent, "Experience")
            for job in exp.findall("job"):
                title   = job.findtext("title","")
                company = job.findtext("company","")
                dates   = (job.findtext("dates","")
                            or job.findtext("duration","")
                            or job.findtext("period",""))
                header  = "  @  ".join(filter(None, [title, company]))
                if dates: header += f"  ({dates})"
                ctk.CTkLabel(parent, text=header,
                              font=F("small_b"), text_color=FG, anchor="w"
                              ).pack(fill="x", padx=24, pady=(6, 2))
                bullets_el = job.find("bullets")
                bullet_els = (bullets_el.findall("bullet")
                               if bullets_el is not None else []) or \
                              job.findall("bullet") or \
                              job.findall(".//bullet")
                for b in bullet_els:
                    if b.text and b.text.strip():
                        ctk.CTkLabel(parent, text=f"  • {b.text.strip()}",
                                      font=F("small"), text_color=FG_SOFT,
                                      anchor="w", wraplength=600, justify="left"
                                      ).pack(fill="x", padx=30)

        # Education — stacked layout so long degree names don't overlap
        edu = root.find(".//education")
        if edu is not None:
            self._section(parent, "Education")
            for deg in edu.findall("degree"):
                school = deg.findtext("school","")
                title  = (deg.findtext("name","")
                           or deg.findtext("degree","")
                           or deg.findtext("title",""))
                dates  = (deg.findtext("year","")
                           or deg.findtext("dates","")
                           or deg.findtext("graduation",""))
                # Stacked: school name on line 1, degree + dates on line 2
                # Using _row for the school→degree mapping causes overlap
                # when both strings are long — use two labels instead
                card = ctk.CTkFrame(parent, fg_color="transparent")
                card.pack(fill="x", padx=24, pady=(4, 2))
                ctk.CTkLabel(card, text=school, font=F("small_b"),
                              text_color=FG, anchor="w"
                              ).pack(anchor="w")
                if title or dates:
                    sub = "  •  ".join(filter(None, [title, dates]))
                    ctk.CTkLabel(card, text=sub, font=F("small"),
                                  text_color=FG_DIM, anchor="w",
                                  wraplength=600, justify="left"
                                  ).pack(anchor="w")

        # Projects
        projs = root.find(".//projects")
        if projs is not None:
            self._section(parent, "Projects")
            for p in projs.findall("project"):
                name = p.findtext("name","")
                # Description may be <description>, <summary>, or <tech>
                desc  = (p.findtext("description","")
                          or p.findtext("summary",""))
                tech  = p.findtext("tech","")
                ctk.CTkLabel(parent, text=name, font=F("small_b"),
                              text_color=FG, anchor="w"
                              ).pack(fill="x", padx=24, pady=(6, 0))
                if tech:
                    ctk.CTkLabel(parent, text=tech, font=F("tiny"),
                                  text_color=ACCENT, anchor="w"
                                  ).pack(fill="x", padx=26, pady=(1, 0))
                if desc:
                    ctk.CTkLabel(parent, text=desc, font=F("small"),
                                  text_color=FG_SOFT, anchor="w",
                                  wraplength=600, justify="left"
                                  ).pack(fill="x", padx=30)
                # Bullets inside projects
                bullets_el = p.find("bullets")
                bullet_els = (bullets_el.findall("bullet")
                               if bullets_el is not None else []) or \
                              p.findall("bullet")
                for b in bullet_els:
                    if b.text and b.text.strip():
                        ctk.CTkLabel(parent, text=f"  • {b.text.strip()}",
                                      font=F("small"), text_color=FG_SOFT,
                                      anchor="w", wraplength=600, justify="left"
                                      ).pack(fill="x", padx=30)

        # Spacer at bottom
        ctk.CTkFrame(parent, height=24, fg_color="transparent").pack()


# ── Entry point ────────────────────────────────────────────────────
class ReviewWindow(ctk.CTkToplevel):
    """
    Checklist of previously-applied jobs.
    User reads each reason, ticks the ones to re-apply,
    then clicks Start Review to open them in the browser.
    """

    def __init__(self, parent, candidates: list, api_key: str,
                 runner=None, on_done=None):
        super().__init__(parent)
        self.title("Review Old Applications")
        self.geometry("720x580")
        self.minsize(580, 400)
        self.resizable(True, True)
        self.grab_set()
        self._candidates = candidates
        self._api_key    = api_key
        self._runner     = runner   # existing bot subprocess — stopped before review
        self._on_done    = on_done
        self._checks     = []
        self._q          = queue.Queue()
        self._build()
        self._poll()

    def _poll(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "status":
                    self._status_lbl.configure(text=data, text_color=FG_DIM)
                elif kind == "done":
                    self._status_lbl.configure(
                        text="Review complete. Close this window.",
                        text_color=SUCCESS)
                    self._start_btn.configure(state="disabled")
        except Exception:
            pass
        if self.winfo_exists():
            self.after(100, self._poll)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(16,8))
        ctk.CTkLabel(hdr, text="Select jobs to re-apply",
                     font=F("body_b"), text_color=FG).pack(side="left")
        ctk.CTkButton(hdr, text="Select All", width=90, height=28,
                      font=F("small"), fg_color=BG_FIELD,
                      hover_color=BG_HOVER,
                      command=self._select_all).pack(side="right")
        ctk.CTkButton(hdr, text="Clear All", width=90, height=28,
                      font=F("small"), fg_color=BG_FIELD,
                      hover_color=BG_HOVER,
                      command=self._clear_all).pack(side="right", padx=(0,8))

        # Scrollable job list
        scroll = ctk.CTkScrollableFrame(self, fg_color=BG)
        scroll.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0,8))
        scroll.grid_columnconfigure(0, weight=1)

        self._checks = []
        for job in self._candidates:
            var  = ctk.BooleanVar(value=False)
            card = ctk.CTkFrame(scroll, fg_color=BG_CARD, corner_radius=10)
            card.pack(fill="x", pady=(0,8))
            card.grid_columnconfigure(1, weight=1)

            # Checkbox
            ctk.CTkCheckBox(card, text="", variable=var,
                            width=24).grid(row=0, column=0,
                            rowspan=2, padx=(12,8), pady=12, sticky="n")

            # Job info
            title   = job.get("job_title","") or "Unknown role"
            company = job.get("company","")   or "Unknown company"
            reason  = job.get("ai_reason","") or "No reason recorded"
            score   = job.get("match_score",0) or 0
            days    = job.get("days_ago",-1)
            rtype   = job.get("_review_type","reapply")

            if rtype == "queued":
                when = "Queued — resume ready, not yet applied"
                badge_col = ACCENT
                badge_txt = "Queued"
            else:
                when = ("Applied %d day(s) ago" % days) if days >= 0 else "Previously applied"
                badge_col = SUCCESS
                badge_txt = "Re-apply"

            hdr_row = ctk.CTkFrame(card, fg_color="transparent")
            hdr_row.grid(row=0, column=1, sticky="ew", padx=(0,12), pady=(12,2))
            hdr_row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(hdr_row,
                         text="%s @ %s" % (title, company),
                         font=F("label_b"), text_color=FG,
                         anchor="w").grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(hdr_row, text="  %s  " % badge_txt,
                         fg_color=badge_col, corner_radius=4,
                         font=F("tiny"), text_color=BG
                         ).grid(row=0, column=1, padx=(8,0))
            ctk.CTkLabel(card,
                         text="%s  ·  Match %d%%  |  %s" % (when, score, reason),
                         font=F("small"), text_color=FG_DIM,
                         wraplength=500, justify="left",
                         anchor="w").grid(row=1, column=1, sticky="w",
                                          padx=(0,12), pady=(0,12))

            self._checks.append((var, job))

        # Footer
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=20, pady=(0,16))
        foot.grid_columnconfigure(0, weight=1)

        self._status_lbl = ctk.CTkLabel(foot, text="",
                                         font=F("small"), text_color=FG_DIM)
        self._status_lbl.grid(row=0, column=0, sticky="w")

        self._start_btn = ctk.CTkButton(
            foot, text="▶  Start Review",
            height=40, font=F("body_b"),
            fg_color=ACCENT, hover_color=ACCENT_HV,
            command=self._start_review)
        self._start_btn.grid(row=0, column=1, sticky="e")

    def _select_all(self):
        for var, _ in self._checks:
            var.set(True)

    def _clear_all(self):
        for var, _ in self._checks:
            var.set(False)

    def _start_review(self):
        selected = [job for var, job in self._checks if var.get()]
        if not selected:
            messagebox.showwarning("Nothing selected",
                "Tick at least one job to review.")
            return
        self._start_btn.configure(state="disabled")
        self._status_lbl.configure(
            text="Opening browser for %d job(s)..." % len(selected))
        threading.Thread(
            target=self._do_review,
            args=(selected,), daemon=True).start()

    def _do_review(self, jobs: list):
        """
        Open browser for selected jobs.
        Stops any existing bot subprocess first so Chrome profile is free,
        then uses a queue to feed answers from GUI buttons instead of stdin.
        """
        import queue as _queue
        self._input_q = _queue.Queue()

        # Stop existing bot subprocess so it releases the Chrome profile lock.
        # Without this, Chrome opens a new tab in the existing window instead
        # of a fresh LinkedIn session.
        if self._runner and self._runner.running():
            try:
                self._runner.send("stop")
                import time as _t; _t.sleep(1.5)   # wait for clean shutdown
                self._runner.stop()
                import time as _t; _t.sleep(0.5)
            except Exception:
                pass

        # Switch checklist to "applying" mode — show action buttons
        self.after(0, lambda: self._show_apply_ui(jobs))

        try:
            import asyncio
            import backend.browser as _browser
            from playwright.async_api import async_playwright
            from backend.browser import create_logged_in_context, guided_apply_session

            # Wire GUI input queue into browser module
            _browser._GUI_INPUT_QUEUE = self._input_q

            queued_jobs  = [j for j in jobs if j.get("_review_type") == "queued"]
            reapply_jobs = [j for j in jobs if j.get("_review_type") != "queued"]

            async def _run():
                async with async_playwright() as pw:
                    browser, context, _ = await create_logged_in_context(pw)
                    applied = skipped = 0
                    if queued_jobs:
                        r = await guided_apply_session(
                            context, queued_jobs, reapply=False)
                        applied += r.get("applied", 0)
                        skipped += r.get("skipped", 0)
                    if reapply_jobs:
                        r = await guided_apply_session(
                            context, reapply_jobs, reapply=True)
                        applied += r.get("applied", 0)
                        skipped += r.get("skipped", 0)
                    try:
                        await browser.close()
                    except Exception:
                        pass
                    return {"applied": applied, "skipped": skipped}

            result = asyncio.run(_run())
            _browser._GUI_INPUT_QUEUE = None   # reset
            self._q.put(("done",
                "Applied: %d  Skipped: %d" % (
                    result.get("applied", 0), result.get("skipped", 0))))
            if self._on_done:
                self.after(500, self._on_done)

        except Exception as e:
            import backend.browser as _browser
            _browser._GUI_INPUT_QUEUE = None
            self._q.put(("status", "Error: %s" % e))
            self.after(0, lambda: self._start_btn.configure(state="normal"))

    def _show_apply_ui(self, jobs: list):
        """Replace checklist with apply action buttons."""
        for w in self.winfo_children():
            w.destroy()
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        self.grid_columnconfigure(0, weight=1)

        info = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        info.grid(row=0, column=0, sticky="nsew", padx=20, pady=(20,8))
        info.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(info,
                     text="Browser is open — apply to each job then confirm below.",
                     font=F("label_b"), text_color=FG,
                     wraplength=500).pack(pady=(20, 4))
        self._apply_status = ctk.CTkLabel(info, text="Waiting for browser...",
                                           font=F("small"), text_color=FG_DIM)
        self._apply_status.pack(pady=(0, 20))

        # d/s/q buttons — feed answers into the queue
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", padx=20, pady=(0,20))
        btn_row.grid_columnconfigure((0,1,2), weight=1)

        remaining = [0]   # mutable counter for last-job detection
        remaining[0] = len(jobs)

        def _send(val):
            remaining[0] -= 1
            if remaining[0] <= 0 or val == "q":
                self._apply_status.configure(
                    text="Session complete — you can close this window.")
                for w in btn_row.winfo_children():
                    w.configure(state="disabled")
            self._input_q.put(val)

        ctk.CTkButton(btn_row, text="✓  Applied",
                      fg_color=SUCCESS, hover_color="#16A34A",
                      height=44, font=F("body_b"),
                      command=lambda: _send("d")
                      ).grid(row=0, column=0, padx=(0,8), sticky="ew")
        ctk.CTkButton(btn_row, text="—  Skip",
                      fg_color=BG_FIELD, hover_color=BG_HOVER,
                      height=44, font=F("body_b"),
                      command=lambda: _send("s")
                      ).grid(row=0, column=1, padx=(0,8), sticky="ew")
        ctk.CTkButton(btn_row, text="✕  Quit",
                      fg_color=DANGER, hover_color="#B91C1C",
                      height=44, font=F("body_b"),
                      command=lambda: _send("q")
                      ).grid(row=0, column=2, sticky="ew")