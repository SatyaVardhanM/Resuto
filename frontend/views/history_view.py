"""
frontend/views/history_view.py
──────────────────────────────
HistoryView — job history tab.
"""
import json
import os
import sqlite3
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from frontend.constants import (
    BG, BG_CARD, BG_FIELD, BG_HOVER,
    ACCENT, ACCENT_HV, DANGER, SUCCESS, WARNING, STRETCH, MUTED,
    FG, FG_SOFT, FG_DIM, F,
)


def _db_path() -> str:
    from frontend.app import _db_path as _dp
    return _dp()

def _read_history(filt: str) -> list:
    from frontend.app import _read_history as _rh
    return _rh(filt)


class HistoryMixin:
    """History tab — paginated job table with filter strip."""

    def _bg_fetch_history(self):
        """Background thread: read DB for history, post to queue."""
        try:
            filt = self._h_filter.get()
            rows = _read_history(filt)
            self._q.put(("history_data", rows))
        except Exception:
            pass

    # ── History tab ───────────────────────────────────────────────
    # ── History page size ─────────────────────────────────────────
    HIST_PAGE = 20

    def _build_history(self):
        f = self._tabs["history"]
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(0, weight=0)  # header
        f.grid_rowconfigure(1, weight=0)  # filter bar
        f.grid_rowconfigure(2, weight=1)  # scrollable list — gets all space
        f.grid_rowconfigure(3, weight=0)  # pagination bar

        # Header row
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 6))
        ctk.CTkLabel(hdr, text="Job History",
                     font=F("body_b"), text_color=FG).pack(side="left")
        ctk.CTkButton(hdr, text="↻  Refresh", width=100, height=30,
                      font=F("small"), fg_color=BG_CARD, hover_color=BG_HOVER,
                      command=self._load_history).pack(side="right")
        ctk.CTkButton(hdr, text="🗑  Clear All History", width=150, height=30,
                      font=F("small"), fg_color="#5C1010", hover_color=DANGER,
                      command=self._confirm_clear_history).pack(side="right", padx=(0,8))

        # Delete selected button (shown only when matched rows selected)
        self._h_del_btn = ctk.CTkButton(
            hdr, text="🗑  Delete selected", width=150, height=30,
            font=F("small"), fg_color=DANGER, hover_color="#C0392B",
            command=self._h_delete_selected)
        # Not packed until checkboxes are ticked

        # Filter row — scrollable so buttons never clip on narrow windows
        filt_outer = ctk.CTkFrame(f, fg_color="transparent")
        filt_outer.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 6))
        filt_outer.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(filt_outer, text="Filter:", font=F("small"),
                     text_color=FG_DIM).grid(row=0, column=0, padx=(0, 8))
        filt = ctk.CTkFrame(filt_outer, fg_color="transparent")
        filt.grid(row=0, column=1, sticky="ew")
        self._h_filter = ctk.StringVar(value="all")
        for txt, val in [("All","all"),("Applied","applied"),
                          ("Skipped","skipped"),("Failed","failed"),
                          ("Queued","matched"),("Stretch","stretch")]:  # stretch filter
            ctk.CTkRadioButton(
                filt, text=txt, variable=self._h_filter, value=val,
                font=F("small"), command=self._load_history,
            ).pack(side="left", padx=4)

        # Scrollable list — height gives a starting size; expands via grid weight
        self._h_scroll = ctk.CTkScrollableFrame(
            f, fg_color="transparent", corner_radius=0)
        self._h_scroll.grid(row=2, column=0, sticky="nsew", padx=20)
        self._h_scroll.grid_columnconfigure(0, weight=1)

        # Pagination bar
        pg = ctk.CTkFrame(f, fg_color="transparent")
        pg.grid(row=3, column=0, sticky="ew", padx=20, pady=6)
        self._h_prev_btn = ctk.CTkButton(
            pg, text="← Prev", width=80, height=28, font=F("small"),
            fg_color=BG_CARD, hover_color=BG_HOVER,
            command=self._h_prev_page)
        self._h_prev_btn.pack(side="left")
        self._h_page_lbl = ctk.CTkLabel(pg, text="", font=F("small"),
                                          text_color=FG_DIM)
        self._h_page_lbl.pack(side="left", padx=12)
        self._h_next_btn = ctk.CTkButton(
            pg, text="Next →", width=80, height=28, font=F("small"),
            fg_color=BG_CARD, hover_color=BG_HOVER,
            command=self._h_next_page)
        self._h_next_btn.pack(side="left")

        # State
        self._h_all_rows    = []
        self._h_page        = 0
        self._h_expanded    = None
        self._h_check_vars  = {}   # row_id -> BooleanVar (for deletion)
        self._h_del_visible = False

    def _load_history(self):
        """Full reload — only called on filter change, tab open, manual refresh."""
        threading.Thread(target=self._bg_fetch_history, daemon=True).start()

    def _apply_history_data(self, rows: list):
        """Full render from fresh DB data — rebuilds all widgets."""
        self._h_all_rows   = rows
        self._h_page       = 0
        self._h_expanded   = None
        self._h_check_vars = {}
        self._h_row_map    = {}   # id -> {svar_name: StringVar, ...}
        self._h_del_btn.pack_forget()
        self._h_del_visible = False
        self._h_render_page()

    def _h_prev_page(self):
        if self._h_page > 0:
            self._h_page -= 1
            self._h_expanded = None
            self._h_render_page()

    def _h_next_page(self):
        pages = max(1, (len(self._h_all_rows) + self.HIST_PAGE - 1)
                    // self.HIST_PAGE)
        if self._h_page < pages - 1:
            self._h_page += 1
            self._h_expanded = None
            self._h_render_page()

    # ── Real-time incremental update ─────────────────────────────

    def _do_push_latest(self, job: dict):
        """
        Main-thread handler for push_latest queue message.
        Decides whether to update an existing row in-place or prepend a new one.
        Never touches rows that aren't changing — zero flicker.
        """
        filt   = self._h_filter.get()
        job_id = job.get("id")
        status = job.get("status", "")

        # Filter check
        if filt != "all" and status != filt:
            # Job doesn't match current filter — still update data list
            # so switching filter later shows correct data
            for i, r in enumerate(self._h_all_rows):
                if r.get("id") == job_id:
                    self._h_all_rows[i] = job
                    return
            self._h_all_rows.insert(0, job)
            return

        # Does a row for this id already exist?
        if job_id in getattr(self, "_h_row_map", {}):
            # Update StringVars in-place — zero widget recreation
            self._h_update_row_svars(job_id, job)
            # Also update data list
            for i, r in enumerate(self._h_all_rows):
                if r.get("id") == job_id:
                    self._h_all_rows[i] = job
                    break
            return

        # New job — prepend to data list and build one new widget
        self._h_all_rows.insert(0, job)
        if getattr(self, "_active_tab", -1) == 3 and self._h_page == 0:
            self._h_prepend_row(job)
            total = len(self._h_all_rows)
            pages = max(1, (total + self.HIST_PAGE - 1) // self.HIST_PAGE)
            self._h_page_lbl.configure(
                text=f"Page 1 of {pages}  ({total} total)")

    def _h_update_row_svars(self, job_id: int, job: dict):
        """Update the StringVars of an existing row — no widget changes at all."""
        if not hasattr(self, "_h_row_map"):
            return
        svars = self._h_row_map.get(job_id)
        if not svars:
            return

        STATUS_ICON = {"applied":"\u2713","skipped":"\u2212",
                       "failed":"\u2715","matched":"\u25b6"}
        STATUS_COL  = {"applied":SUCCESS,"skipped":WARNING,
                       "failed":DANGER,  "matched":ACCENT}
        DECISION    = {"applied":"Applied","skipped":"Skipped",
                       "failed":"Failed", "matched":"Queued"}
        def _h_badge(row):
            if row.get("status") == "matched" and row.get("stretch"):
                return ("Stretch", STRETCH)
            st = row.get("status","")
            return (DECISION.get(st, st.capitalize()), STATUS_COL.get(st, MUTED))

        score    = job.get("match_score") or 0
        status   = job.get("status", "")
        title    = job.get("job_title") or "Unknown"
        company  = job.get("company") or ""
        overlap  = job.get("skill_overlap") or 0
        when     = (job.get("applied_at") or job.get("logged_at") or "")[:16]
        reason   = job.get("ai_reason") or (
            "Queued \u2014 resume being generated..." if status == "matched"
            else "No reason recorded.")
        decision = DECISION.get(status, status.capitalize())
        sk_txt   = f"  |  Overlap: {overlap}%" if overlap else ""

        # Update every StringVar — Tk propagates changes to widgets automatically
        svars["icon"].set(STATUS_ICON.get(status, "\u2022"))
        svars["title"].set(title)
        svars["meta"].set(f"{company}  \u2022  {when}" if company else when)
        svars["score"].set(f"{score}%" if score else "")
        svars["badge"].set(f"  {decision}  ")
        svars["match"].set(f"  Match {score}%{sk_txt}")
        svars["reason"].set(reason)

        # Update icon + bar colours via widget configure
        # (StringVar can't change text_color — need direct configure)
        try:
            s_col   = STATUS_COL.get(status, MUTED)
            bar_col = SUCCESS if score >= 70 else WARNING if score >= 45 else DANGER
            if "icon_lbl" in svars:
                svars["icon_lbl"].configure(text_color=s_col)
            if "score_lbl" in svars:
                svars["score_lbl"].configure(text_color=bar_col)
            if "badge_lbl" in svars:
                svars["badge_lbl"].configure(fg_color=s_col)
            if "bar_fill" in svars:
                w = max(2, int(score / 100 * 70))
                svars["bar_fill"].configure(width=w, fg_color=bar_col)
        except Exception:
            pass

    def _h_prepend_row(self, job: dict):
        """Build one new row widget and insert it at the top of the scroll frame."""
        widget = self._h_make_row(job)
        if not widget:
            return
        children = self._h_scroll.winfo_children()
        if children:
            widget.pack(fill="x", pady=2, before=children[0])
        else:
            widget.pack(fill="x", pady=2)

    def _h_render_page(self):
        """Full page render — only called on initial load, filter change, page nav."""
        for w in self._h_scroll.winfo_children():
            w.destroy()
        self._h_expanded = None
        if not hasattr(self, "_h_row_map"):
            self._h_row_map = {}
        self._h_row_map.clear()

        rows  = self._h_all_rows
        total = len(rows)
        if not total:
            msg = ("No queued jobs — run the bot to scan and queue new jobs."
                   if self._h_filter.get() == "matched"
                   else "No records match this filter.")
            ctk.CTkLabel(self._h_scroll, text=msg,
                         font=F("body"), text_color=MUTED,
                         wraplength=400, justify="center").pack(pady=40)
            self._h_page_lbl.configure(text="")
            self._h_prev_btn.configure(state="disabled")
            self._h_next_btn.configure(state="disabled")
            return

        PAGE  = self.HIST_PAGE
        pages = max(1, (total + PAGE - 1) // PAGE)
        pg    = max(0, min(self._h_page, pages - 1))
        self._h_page = pg
        page_rows = rows[pg * PAGE : (pg + 1) * PAGE]

        self._h_page_lbl.configure(
            text=f"Page {pg+1} of {pages}  ({total} total)")
        self._h_prev_btn.configure(state="normal" if pg > 0 else "disabled")
        self._h_next_btn.configure(
            state="normal" if pg < pages - 1 else "disabled")

        for job in page_rows:
            w = self._h_make_row(job)
            if w:
                w.pack(fill="x", pady=2)

    def _h_make_row(self, job: dict):
        """
        Build one accordion row. Stores StringVars and widget refs into
        self._h_row_map[job_id] so the row can be updated in-place later
        without any widget recreation.
        """
        STATUS_ICON = {"applied":"\u2713","skipped":"\u2212",
                       "failed":"\u2715","matched":"\u25b6"}
        STATUS_COL  = {"applied":SUCCESS,"skipped":WARNING,
                       "failed":DANGER,  "matched":ACCENT}
        DECISION    = {"applied":"Applied","skipped":"Skipped",
                       "failed":"Failed", "matched":"Queued"}
        def _h_badge(row):
            if row.get("status") == "matched" and row.get("stretch"):
                return ("Stretch", STRETCH)
            st = row.get("status","")
            return (DECISION.get(st, st.capitalize()), STATUS_COL.get(st, MUTED))

        score     = job.get("match_score") or 0
        status    = job.get("status","")
        title     = job.get("job_title") or "Unknown"
        company   = job.get("company") or ""
        reason    = job.get("ai_reason") or (
            "Queued \u2014 resume being generated..." if status == "matched"
            else "No reason recorded.")
        overlap   = job.get("skill_overlap") or 0
        when      = (job.get("applied_at") or job.get("logged_at") or "")[:16]
        row_id    = job.get("id")
        decision  = DECISION.get(status, status.capitalize())
        s_col     = STATUS_COL.get(status, MUTED)
        bar_col   = SUCCESS if score >= 70 else WARNING if score >= 45 else DANGER
        icon      = STATUS_ICON.get(status, "\u2022")
        sk_txt    = f"  |  Overlap: {overlap}%" if overlap else ""
        is_queued = status == "matched"

        # ── StringVars — all changing fields ─────────────────────
        sv_icon   = ctk.StringVar(value=icon)
        sv_title  = ctk.StringVar(value=title)
        sv_meta   = ctk.StringVar(value=f"{company}  \u2022  {when}" if company else when)
        sv_score  = ctk.StringVar(value=f"{score}%" if score else "")
        sv_badge  = ctk.StringVar(value=f"  {decision}  ")
        sv_match  = ctk.StringVar(value=f"  Match {score}%{sk_txt}")
        sv_reason = ctk.StringVar(value=reason)
        sv_chev   = ctk.StringVar(value="\u25be")

        # Outer card
        outer = ctk.CTkFrame(self._h_scroll, fg_color=BG_CARD, corner_radius=8)
        outer.grid_columnconfigure(0, weight=1)

        # Summary row — fixed 4 columns
        summ = ctk.CTkFrame(outer, fg_color="transparent", cursor="hand2")
        summ.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
        summ.grid_columnconfigure(0, minsize=24)
        summ.grid_columnconfigure(1, minsize=28)
        summ.grid_columnconfigure(2, weight=1)
        summ.grid_columnconfigure(3, minsize=90)

        # Col 0: checkbox or spacer
        if is_queued and row_id is not None:
            chk_var = ctk.BooleanVar(value=False)
            self._h_check_vars[row_id] = chk_var
            ctk.CTkCheckBox(summ, text="", variable=chk_var, width=20,
                             checkbox_width=18, checkbox_height=18,
                             command=self._h_check_changed
                             ).grid(row=0, column=0, rowspan=2, sticky="w")
        else:
            ctk.CTkFrame(summ, width=20, height=1,
                          fg_color="transparent").grid(row=0, column=0, rowspan=2)

        # Col 1: status icon — stores widget ref for colour updates
        icon_lbl = ctk.CTkLabel(summ, textvariable=sv_icon, font=F("icon"),
                                  text_color=s_col, width=24, anchor="center")
        icon_lbl.grid(row=0, column=1, rowspan=2, sticky="w")

        # Col 2: title + meta
        info = ctk.CTkFrame(summ, fg_color="transparent")
        info.grid(row=0, column=2, sticky="ew", padx=(6, 0), rowspan=2)
        info.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(info, textvariable=sv_title, font=F("label_b"),
                     text_color=FG, anchor="w"
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(info, textvariable=sv_meta, font=F("tiny"),
                     text_color=MUTED, anchor="w"
                     ).grid(row=1, column=0, sticky="w")

        # Col 3: score + bar + chevron
        rgt = ctk.CTkFrame(summ, fg_color="transparent")
        rgt.grid(row=0, column=3, rowspan=2, padx=(8, 0), sticky="e")
        score_lbl = ctk.CTkLabel(rgt, textvariable=sv_score, font=F("label_b"),
                                   text_color=bar_col)
        score_lbl.pack(anchor="e")
        bar_bg = ctk.CTkFrame(rgt, width=70, height=5,
                               fg_color=BG_FIELD, corner_radius=2)
        bar_bg.pack(pady=(2, 0)); bar_bg.pack_propagate(False)
        bar_fill = ctk.CTkFrame(bar_bg,
                                 width=max(2, int(score / 100 * 70)),
                                 height=5, fg_color=bar_col, corner_radius=2)
        bar_fill.place(x=0, y=0)
        ctk.CTkLabel(rgt, textvariable=sv_chev, font=F("small"),
                     text_color=MUTED).pack(anchor="e")

        # Detail panel
        det = ctk.CTkFrame(outer, fg_color="transparent")
        ctk.CTkFrame(det, height=1, fg_color=BG_HOVER
                     ).pack(fill="x", padx=8, pady=(0, 4))
        det_inner = ctk.CTkFrame(det, fg_color="transparent")
        det_inner.pack(fill="x", padx=14, pady=(0, 10))
        badge_row = ctk.CTkFrame(det_inner, fg_color="transparent")
        badge_row.pack(anchor="w", pady=(0, 6))
        badge_lbl = ctk.CTkLabel(badge_row, textvariable=sv_badge,
                                   fg_color=s_col, corner_radius=4,
                                   font=F("small"), text_color=BG)
        badge_lbl.pack(side="left")
        ctk.CTkLabel(badge_row, textvariable=sv_match,
                      font=F("small"), text_color=FG_DIM).pack(side="left")
        ctk.CTkLabel(det_inner, textvariable=sv_reason, font=F("small"),
                      text_color=FG_SOFT, wraplength=500,
                      justify="left", anchor="w").pack(anchor="w")

        # Generate Resume button — shown for matched jobs without a resume
        if is_queued:
            gen_row = ctk.CTkFrame(det_inner, fg_color="transparent")
            gen_row.pack(anchor="w", pady=(8,0))
            ctk.CTkButton(
                gen_row,
                text="Generate Resume",
                width=140, height=28,
                font=F("small"),
                fg_color=ACCENT, hover_color=ACCENT_HV,
                command=lambda rid=row_id, t=title, c=company: (
                    self._manual_generate_resume(rid, t, c))
            ).pack(side="left")
            ctk.CTkLabel(gen_row,
                text="  Resume not yet generated for this job",
                font=F("small"), text_color=MUTED).pack(side="left")

        # Toggle accordion
        def _toggle(ev=None, d=det, cv=sv_chev):
            if d.winfo_ismapped():
                d.grid_forget(); cv.set("\u25be")
            else:
                try:
                    if (self._h_expanded is not None
                            and self._h_expanded is not d
                            and self._h_expanded.winfo_exists()):
                        self._h_expanded.grid_forget()
                except Exception:
                    pass
                d.grid(row=1, column=0, sticky="ew")
                cv.set("\u25b4")
                self._h_expanded = d

        for w in (outer, summ, info, rgt):
            w.bind("<Button-1>", _toggle)
        for ch in list(info.winfo_children()) + list(rgt.winfo_children()):
            ch.bind("<Button-1>", _toggle)

        # ── Register in row map for in-place updates ──────────────
        if row_id is not None:
            if not hasattr(self, "_h_row_map"):
                self._h_row_map = {}
            self._h_row_map[row_id] = {
                "icon":      sv_icon,   "title":  sv_title,
                "meta":      sv_meta,   "score":  sv_score,
                "badge":     sv_badge,  "match":  sv_match,
                "reason":    sv_reason,
                "icon_lbl":  icon_lbl,  "score_lbl": score_lbl,
                "badge_lbl": badge_lbl, "bar_fill":  bar_fill,
            }
        return outer

    def _h_check_changed(self):
        """Show/hide delete button based on whether any queued rows are checked."""
        any_checked = any(v.get() for v in self._h_check_vars.values())
        if any_checked and not self._h_del_visible:
            self._h_del_btn.pack(side="right", padx=(0, 8))
            self._h_del_visible = True
        elif not any_checked and self._h_del_visible:
            self._h_del_btn.pack_forget()
            self._h_del_visible = False

    def _confirm_clear_history(self):
        """Show confirmation dialog before wiping all job history."""
        import customtkinter as ctk

        popup = ctk.CTkToplevel(self)
        popup.title("Clear All History")
        popup.geometry("440x260")
        popup.resizable(False, False)
        popup.grab_set()

        ctk.CTkLabel(popup,
            text="Clear All Job History?",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#FF6B6B").pack(pady=(24,8))

        ctk.CTkLabel(popup,
            text="This permanently deletes ALL job records from the database.\n"
                 "Applied jobs, resumes queued, history — everything gone.\n\n"
                 "The generated DOCX/PDF files on disk are NOT deleted.",
            font=ctk.CTkFont(size=12),
            text_color="#AAAAAA",
            justify="center").pack(pady=(0,20))

        btn_row = ctk.CTkFrame(popup, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(0,20))

        ctk.CTkButton(btn_row, text="Cancel",
            height=38, fg_color="#3A3A3A", hover_color="#4A4A4A",
            font=ctk.CTkFont(size=12),
            command=popup.destroy).pack(side="left", expand=True, fill="x", padx=(0,8))

        def _confirm():
            popup.destroy()
            self._do_clear_history()

        ctk.CTkButton(btn_row, text="Yes, Clear Everything",
            height=38, fg_color=DANGER, hover_color="#C0392B",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_confirm).pack(side="left", expand=True, fill="x")

    def _do_clear_history(self):
        """Wipe all rows from applications DB and refresh UI."""
        try:
            import db.tracker as tracker
            deleted = tracker.clear_all_history()
            self._set_status("History cleared — %d records deleted" % deleted)
            self._load_history()
            self._force_stats_refresh()
        except Exception as e:
            messagebox.showerror("Clear Failed",
                "Could not clear history:\n%s" % e)

    def _h_delete_selected(self):
        """Delete checked queued jobs from DB and filesystem."""
        to_delete = [rid for rid, var in self._h_check_vars.items() if var.get()]
        if not to_delete:
            return
        import tracker as _tr
        _tr.delete_jobs(to_delete)
        self._h_check_vars  = {}
        self._h_del_visible = False
        self._h_del_btn.pack_forget()
        self._load_history()
        self._stats_last_hash = None
        self._refresh_stats()

    def _show_action_bar(self, kind: str):
        for w in self._action_bar.winfo_children():
            w.destroy()

        configs = {
            "dsq": ("Did you apply to this job?",
                    "Review the job in the browser, then respond.",
                    [("Applied — Finish" if getattr(self,"_is_last_job",False)
                      else "Applied — Next job","d",SUCCESS),
                     ("Skip this one","s",BG_CARD),
                     ("Quit applying","q",DANGER)]),
            "nf": ("How long to skip?", "",
                   [("Ask me next run","n",BG_CARD),
                    ("Never show again","f",DANGER)]),
            "yn_apply": ("Resumes are ready!",
                         "Tailored resumes generated. Start applying now?",
                         [("Yes, start applying","y",SUCCESS),
                          ("Not now — I'll review later","n",BG_CARD)]),

        }
        headline, sub, buttons = configs.get(kind, ("","",""))
        if headline:
            self._attention_hl.configure(text=headline)
            self._attention_sub.configure(text=sub)
            self._attention_card.place(relx=0.5, rely=0.65, anchor="center",
                                        relwidth=0.85)

        btn_row = ctk.CTkFrame(self._action_bar, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=10)
        for txt, val, col in buttons:
            ctk.CTkButton(btn_row, text=txt,
                          fg_color=col,
                          hover_color=SUCCESS if col==SUCCESS else DANGER if col==DANGER else BG_HOVER,
                          command=lambda v=val, k=kind: self._answer(v, k),
                          height=36, font=F("label")
                          ).pack(side="left", padx=(0,8))

        self._action_bar.grid(row=2, column=0, sticky="ew",
                               padx=20, pady=(0,8))
        self._nav(0)

    def _manual_generate_resume(self, job_id: int, title: str, company: str):
        """
        Manually trigger resume generation for a specific previously matched job.
        Uses the same BotRunner as normal runs — ensures API key, logging,
        and output all work identically.
        """
        if not messagebox.askyesno(
            "Generate Resume",
            "Generate a tailored resume for:\n\n"
            "%s @ %s\n\n"
            "Uses your current profile and the stored job description." % (title, company)):
            return

        api_key = self._api_var.get().strip() if hasattr(self, "_api_var") else ""
        if not api_key:
            messagebox.showerror("No API Key",
                "Enter your Anthropic API key in Settings first.")
            return

        # Use --phase2-only --job-ids N  — same subprocess mechanism as normal run
        # BotRunner sets ANTHROPIC_API_KEY, captures stdout to GUI and bot.log
        args = ["--phase2-only",
                "--gui",
                "--job-ids", str(job_id)]

        self._set_status("Generating resume: %s @ %s..." % (title[:40], company[:30]))
        self._set_phase("Phase 2 — Generating resume...")

        def _on_done(code):
            if code == 0:
                self.after(0, lambda: self._set_status(
                    "Resume ready: %s @ %s" % (title[:40], company[:30])))
                self.after(0, self._load_history)
                self.after(0, self._force_stats_refresh)
            else:
                self.after(0, lambda: messagebox.showerror(
                    "Generation Failed",
                    "Resume generation failed for %s @ %s.\n"
                    "Check the log file for details."
                    % (title, company)))

        runner = BotRunner(api_key, args, self._on_bot_line, _on_done)
        runner.start()
        log_msg = "Manual resume generation started: %s @ %s (id=%d)" % (title, company, job_id)
        self._run_sub_lbl.configure(text=log_msg) if hasattr(self, "_run_sub_lbl") else None

    def _show_metric_collection_popup(self, missing: list,
                                       on_continue=None, on_skip=None):
        """Before bot run: collect real metrics from user. Saves to user_metrics.json."""
        import customtkinter as ctk
        import json as _json, os as _os

        popup = ctk.CTkToplevel(self)
        popup.title("Add Real Numbers - Improve Resume Quality")
        popup.geometry("680x600")
        popup.resizable(False, True)
        popup.grab_set()

        ctk.CTkLabel(popup,
            text="Your profile is missing real numbers",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#FFD580").pack(pady=(20,4))

        desc = (
            "Without real metrics, Claude estimates numbers like 20%% improvement. "
            "If a recruiter asks in an interview, you won't be able to back it up. "
            "Fill in what you remember — rough numbers are fine."
        )
        ctk.CTkLabel(popup, text=desc,
            font=ctk.CTkFont(size=12), text_color="#AAAAAA",
            wraplength=620, justify="center").pack(pady=(0,12))

        scroll = ctk.CTkScrollableFrame(popup, height=380, fg_color="#1A1A1A")
        scroll.pack(fill="x", padx=20)

        questions = [
            ("scale",  "Scale / Volume",     "e.g. 10,000 records, 5M transactions"),
            ("speed",  "Speed / Time saved", "e.g. cut from 2 min to 30 sec, 3x faster"),
            ("impact", "Impact / Result",    "e.g. 100% success rate, zero downtime"),
            ("team",   "Team / Scope",        "e.g. 9-person team, 3 microservices"),
        ]
        entries = {}

        for job in missing:
            company = job.get("company","")
            title   = job.get("title","")
            jf = ctk.CTkFrame(scroll, fg_color="#242424", corner_radius=8)
            jf.pack(fill="x", padx=4, pady=(8,2))
            ctk.CTkLabel(jf, text="%s  @  %s" % (title, company),
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#FFFFFF").pack(anchor="w", padx=12, pady=(10,2))
            sample = job.get("sample","")[:70]
            ctk.CTkLabel(jf, text="Sample: %s..." % sample,
                font=ctk.CTkFont(size=11), text_color="#666666").pack(
                anchor="w", padx=12, pady=(0,6))
            entries[company] = {}
            for key, label, placeholder in questions:
                row = ctk.CTkFrame(jf, fg_color="transparent")
                row.pack(fill="x", padx=12, pady=2)
                ctk.CTkLabel(row, text=label + ":",
                    font=ctk.CTkFont(size=11), text_color="#AAAAAA",
                    width=130, anchor="w").pack(side="left")
                e = ctk.CTkEntry(row, placeholder_text=placeholder,
                    font=ctk.CTkFont(size=11), height=30)
                e.pack(side="left", fill="x", expand=True, padx=(4,0))
                entries[company][key] = e

        def _save_and_go():
            user_metrics = {}
            for company, fields in entries.items():
                parts = ["%s: %s" % (lbl, fields[k].get().strip())
                         for k, lbl, _ in questions if fields[k].get().strip()]
                if parts:
                    user_metrics[company] = ". ".join(parts)
            try:
                from core.settings import get_resume_data_path as _grp3
                xml = _grp3()
                out = _os.path.join(_os.path.dirname(xml),
                                    "user_metrics.json") if xml else "user_metrics.json"
                with open(out, "w") as f:
                    _json.dump(user_metrics, f, indent=2)
            except Exception:
                pass
            popup.destroy()
            if on_continue: on_continue()

        def _skip():
            popup.destroy()
            if on_skip: on_skip()

        btn_row = ctk.CTkFrame(popup, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=16)
        ctk.CTkButton(btn_row, text="Skip - Use AI estimates",
            height=38, fg_color="#3A3A3A", hover_color="#4A4A4A",
            font=ctk.CTkFont(size=12),
            command=_skip).pack(side="left", expand=True, fill="x", padx=(0,8))
        ctk.CTkButton(btn_row, text="Save Numbers & Start Run",
            height=38, fg_color=ACCENT, hover_color=ACCENT_HV,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=_save_and_go).pack(side="left", expand=True, fill="x")

    def _show_metric_warning(self, companies: str, count: int):
        """
        Show a non-blocking banner warning that some jobs lack metrics.
        Claude needs real numbers to avoid fabricating them.
        """
        msg = (
            "Tip: {} job(s) ({}) have no measurable metrics in their bullets.\n"
            "Add numbers to your resume XML (scale, % improvement, team size) so\n"
            "Claude uses real data instead of estimating. Real > estimated always."
            .format(count, companies)
        )
        try:
            import customtkinter as ctk
            banner = ctk.CTkFrame(self, fg_color="#7C4F00", corner_radius=6)
            banner.pack(fill="x", padx=16, pady=(4,0))
            ctk.CTkLabel(banner, text="⚠  " + msg,
                         font=ctk.CTkFont(size=11),
                         text_color="#FFD580",
                         wraplength=700,
                         justify="left").pack(padx=12, pady=6, anchor="w")
            ctk.CTkButton(banner, text="Dismiss", width=70, height=24,
                          fg_color="#A0620A",
                          command=banner.destroy).pack(padx=12, pady=(0,6), anchor="e")
        except Exception:
            pass

    def _hide_action_bar(self):
        self._action_bar.grid_forget()
        self._attention_card.place_forget()
        self._is_last_job = False

    def _answer(self, val: str, kind: str = ""):
        if self._runner: self._runner.send(val)
        self._hide_action_bar()
        # "Not now" on apply prompt → go to Stats so user sees Review button
        if val == "n" and kind == "yn_apply":
            self.after(300, lambda: (
                self._nav(4),           # switch to Stats tab (index 4)
                self._refresh_review_btn()
            ))


    def _h_push_latest(self):
        """Fetch latest job row on a background thread and push to history."""
        threading.Thread(target=self._bg_push_latest, daemon=True).start()

    def _bg_push_latest(self):
        try:
            db = _db_path()
            if not os.path.exists(db): return
            with sqlite3.connect(db, timeout=3) as c:
                c.execute("PRAGMA journal_mode=WAL")
                c.row_factory = sqlite3.Row
                row = c.execute(
                    "SELECT id,job_title,company,status,match_score,"
                    "skill_overlap,ai_reason,applied_at,logged_at "
                    "FROM applications ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
            if not row: return
            job = dict(row)
            self._q.put(("push_latest", job))
        except Exception:
            pass