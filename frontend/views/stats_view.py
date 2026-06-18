"""
frontend/views/stats_view.py
────────────────────────────
StatsView — statistics tab.
Shows applied/matched/failed counters, donut chart, and recent activity.
"""
import hashlib
import json
import math
import threading
from datetime import datetime

import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import queue
import re

from frontend.constants import (
    BG, BG_CARD, BG_FIELD, BG_HOVER,
    ACCENT, DANGER, SUCCESS, WARNING, STRETCH, MUTED,
    FG, FG_SOFT, FG_DIM, F,
)


def _read_stats(since=None, recent_since=None):
    """DB helper — imported here to keep stats self-contained."""
    from frontend.app import _read_stats as _rs
    return _rs(since=since, recent_since=recent_since)


class StatsMixin:
    """Statistics tab — read-only, polls DB every 30s (2s when live)."""

    def _build_stats(self):
        f = self._tabs["stats"]
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(0, weight=0)  # header
        f.grid_rowconfigure(1, weight=0)  # activity strip
        f.grid_rowconfigure(2, weight=0)  # stat cards
        f.grid_rowconfigure(3, weight=1)  # mid row (donut + list) — gets all space

        # Row 0: header
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 4))
        ctk.CTkLabel(hdr, text="Application Stats",
                     font=F("body_b"), text_color=FG).pack(side="left")
        self._live_dot = ctk.CTkLabel(hdr, text="● Live",
                                       font=F("small"), text_color=MUTED)
        self._live_dot.pack(side="right")

        # Review button — enabled only when re-apply candidates exist
        self._review_btn = ctk.CTkButton(
            hdr, text="📋  Review Old Applications",
            height=30, font=F("small"),
            fg_color=BG_FIELD, hover_color=BG_HOVER,
            state="disabled",
            command=self._open_review_window)
        self._review_btn.pack(side="right", padx=(0, 12))

        # Row 1: activity strip (hidden until bot runs)
        self._act_strip = ctk.CTkFrame(f, fg_color=BG_CARD, corner_radius=8)
        self._act_role  = ctk.CTkLabel(self._act_strip, text="",
                                        font=F("label_b"), text_color=ACCENT, anchor="w")
        self._act_role.pack(anchor="w", padx=14, pady=(8, 0))
        self._act_action = ctk.CTkLabel(self._act_strip, text="",
                                         font=F("small"), text_color=FG_DIM, anchor="w")
        self._act_action.pack(anchor="w", padx=14, pady=(2, 8))
        # Start hidden — shown via grid() when bot output arrives
        self._act_strip_visible = False

        # Row 2: stat cards
        self._cards_row = ctk.CTkFrame(f, fg_color="transparent")
        self._cards_row.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 8))
        self._stat_vars = {}
        self._cards_row.grid_columnconfigure((0,1,2,3,4), weight=1)
        card_defs = [
            ("applied", "✓ Applied",   SUCCESS),
            ("skipped", "− Skipped",   WARNING),
            ("failed",  "✕ Failed",    DANGER),
            ("avg",     "◉ Avg Match", FG_SOFT),
            ("queued",  "⌛ Queued",    ACCENT),
        ]
        for col_idx, (key, label, color) in enumerate(card_defs):
            c = ctk.CTkFrame(self._cards_row, fg_color=BG_CARD,
                              corner_radius=10)
            c.grid(row=0, column=col_idx, sticky="ew",
                   padx=(0, 6), pady=4, ipadx=8, ipady=8)
            var = ctk.StringVar(value="--")
            ctk.CTkLabel(c, textvariable=var, font=F("stat"),
                         text_color=color).pack()
            ctk.CTkLabel(c, text=label, font=F("small"),
                         text_color=MUTED).pack()
            self._stat_vars[key] = (var, c)


        # Middle: donut + job list
        mid = ctk.CTkFrame(f, fg_color="transparent")
        mid.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 8))
        mid.grid_columnconfigure(1, weight=1)
        mid.grid_rowconfigure(0, weight=1)

        # Donut canvas
        self._donut_cv = tk.Canvas(mid, width=130, height=180,
                                    bg=BG, highlightthickness=0)
        self._donut_cv.grid(row=0, column=0, padx=(0, 16), sticky="n")
        self._draw_donut(0, 0, 0)

        # Scrollable job list — height=1 lets it fill via sticky="nsew"
        list_col = ctk.CTkFrame(mid, fg_color="transparent")
        list_col.grid(row=0, column=1, sticky="nsew")
        list_col.grid_columnconfigure(0, weight=1)
        list_col.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(list_col, text="Recent  (click to expand)",
                     font=F("small"), text_color=FG_DIM
                     ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        self._job_scroll = ctk.CTkScrollableFrame(
            list_col, fg_color="transparent", corner_radius=0)
        self._job_scroll.grid(row=1, column=0, sticky="nsew")
        self._job_scroll.grid_columnconfigure(0, weight=1)
        self._job_rows    = []
        self._job_expanded = None

    def _draw_donut(self, applied: int, skipped: int, failed: int):
        cv = self._donut_cv
        cv.delete("arc","hole","txt")
        cx, cy, ro, ri = 65, 65, 52, 30
        total = applied + skipped + failed or 1
        start = -90.0
        for n, col in [(applied, SUCCESS),(skipped, WARNING),(failed, DANGER)]:
            ext = n / total * 360
            if ext > 0.5:
                cv.create_arc(cx-ro, cy-ro, cx+ro, cy+ro,
                              start=start, extent=ext,
                              fill=col, outline=BG, width=3, tags="arc")
            start += ext
        if applied+skipped+failed == 0:
            cv.create_oval(cx-ro, cy-ro, cx+ro, cy+ro,
                           fill=BG_CARD, outline=BG, tags="arc")
        cv.create_oval(cx-ri, cy-ri, cx+ri, cy+ri,
                       fill=BG, outline=BG, tags="hole")
        cv.create_text(cx, cy-8, text=str(applied+skipped+failed),
                       fill=FG, font=F("heading"), tags="txt")
        cv.create_text(cx, cy+9, text="done",
                       fill=MUTED, font=F("tiny"), tags="txt")

        # Legend labels below donut
        cv.delete("leg")
        for i, (label, col) in enumerate([("● Applied", SUCCESS),
                                           ("● Skipped", WARNING),
                                           ("● Failed",  DANGER)]):
            cv.create_text(5, 138 + i * 14, text=label,
                           fill=col, font=F("small"), anchor="w", tags="leg")
        cv.configure(height=180)

    def _refresh_stats(self):
        """Compatibility wrapper — kicks off background fetch."""
        threading.Thread(target=self._bg_fetch_stats, daemon=True).start()

    def _force_stats_refresh(self):
        """Force an immediate stats refresh, bypassing the hash cache."""
        self._stats_last_hash = None
        threading.Thread(target=self._bg_fetch_stats, daemon=True).start()

    def _apply_stats_data(self, d: dict):
        """Called on the main thread with pre-fetched data. Zero DB I/O here."""
        if not d:
            return
        h = hashlib.md5(
            json.dumps(d, sort_keys=True, default=str).encode()
        ).hexdigest()
        if h == self._stats_last_hash:
            return
        self._stats_last_hash = h
        self._job_expanded = None   # reset stale ref on data change
        counts  = d.get("counts", {})
        avg     = d.get("avg", 0)
        jobs    = d.get("jobs", [])
        queued  = d.get("queued", 0)
        applied = counts.get("applied", 0)
        skipped = counts.get("skipped", 0)
        failed  = counts.get("failed",  0)

        updates = {"applied": str(applied), "skipped": str(skipped),
                   "failed": str(failed),   "avg": f"{avg}%",
                   "queued": str(queued)}
        for key, (var, card) in self._stat_vars.items():
            var.set(updates[key])
            if key == "queued":
                # Queued card always visible; just update value
                pass  # grid layout keeps all cards visible

        self._draw_donut(applied, skipped, failed)

        # Incremental job list update
        # Track rows by position; only destroy/create if count changed
        self._job_expanded = None
        STATUS_ICON = {"applied":"✓","skipped":"−","failed":"✕","matched":"▶","scanning":"●"}
        STATUS_COL  = {"applied":SUCCESS,"skipped":WARNING,"failed":DANGER,"matched":ACCENT,"scanning":MUTED}
        DECISION    = {"applied":"Applied","skipped":"Skipped","failed":"Failed","matched":"Queued","scanning":"Analyzing..."}

        def _badge(job, status, decision, s_col):
            """Return (label, color) — stretch jobs get amber badge."""
            if status == "matched" and job.get("stretch"):
                return "  Stretch  ", STRETCH
            return "  %s  " % decision, s_col
        if not hasattr(self, "_job_svars"):
            self._job_svars = []   # list of dicts of StringVars per row
        # Full rebuild only when row count changes
        existing = len(self._job_scroll.winfo_children())
        if existing != len(jobs):
            for w in self._job_scroll.winfo_children():
                w.destroy()
            self._job_svars = []
            self._job_rows  = []
            self._rebuild_job_rows = True
        else:
            self._rebuild_job_rows = False

        for row_i, job in enumerate(jobs):
            score    = job.get("match_score") or 0
            status   = job.get("status", "")
            title    = job.get("job_title") or "Unknown"
            company  = job.get("company") or ""
            reason   = job.get("ai_reason") or (
                "Queued — resume being generated..." if status == "matched"
                else "No reason recorded.")
            overlap  = job.get("skill_overlap") or 0
            when     = (job.get("logged_at") or "")[:16]
            decision = DECISION.get(status, status.capitalize())
            s_col    = STATUS_COL.get(status, MUTED)
            bar_col  = SUCCESS if score >= 70 else WARNING if score >= 45 else DANGER
            icon     = STATUS_ICON.get(status, "•")
            sk_txt   = f"  |  Overlap: {overlap}%" if overlap else ""

            # If rows already exist, update StringVars in-place (no widget create)
            if not self._rebuild_job_rows and row_i < len(self._job_svars):
                sv = self._job_svars[row_i]
                sv["icon"].set(icon)
                sv["title"].set(title)
                sv["meta"].set(f"{company}  •  {when}" if company else when)
                sv["score"].set(f"{score}%" if score else "")
                sv["reason"].set(reason)
                sv["badge"].set(f"  {decision}  ")
                sv["match"].set(f"  Match {score}%{sk_txt}")
                # Update bar color and width
                try:
                    new_bar_col = SUCCESS if score >= 70 else WARNING if score >= 45 else DANGER
                    new_fill_w  = max(2, int(score / 100 * 80))
                    sv["bar_fill"].configure(width=new_fill_w, fg_color=new_bar_col)
                except Exception:
                    pass
                continue   # skip widget creation below

            outer = ctk.CTkFrame(self._job_scroll, fg_color=BG_CARD,
                                  corner_radius=8, cursor="hand2")
            outer.pack(fill="x", pady=2)
            outer.grid_columnconfigure(0, weight=1)

            # Summary row
            summ = ctk.CTkFrame(outer, fg_color="transparent")
            summ.grid(row=0, column=0, sticky="ew", padx=10, pady=6)
            summ.grid_columnconfigure(1, weight=1)

            _sv_icon = ctk.StringVar(value=icon)
            ctk.CTkLabel(summ, textvariable=_sv_icon, font=F("body_b"),
                         text_color=s_col, width=20).grid(row=0,column=0,rowspan=2)
            info = ctk.CTkFrame(summ, fg_color="transparent")
            info.grid(row=0, column=1, sticky="ew", padx=(8,0))
            info.grid_columnconfigure(0, weight=1)
            _sv_title  = ctk.StringVar(value=title)
            _sv_meta   = ctk.StringVar(value=f"{company}  •  {when}" if company else when)
            ctk.CTkLabel(info, textvariable=_sv_title, font=F("label_b"),
                         text_color=FG, anchor="w").grid(row=0,column=0,sticky="w")
            ctk.CTkLabel(info, textvariable=_sv_meta,
                         font=F("small"), text_color=MUTED, anchor="w"
                         ).grid(row=1,column=0,sticky="w")

            rgt = ctk.CTkFrame(summ, fg_color="transparent")
            rgt.grid(row=0, column=2, rowspan=2, padx=(8,0))
            _sv_score = ctk.StringVar(value=f"{score}%" if score else "")
            _sv_score = ctk.StringVar(value=f"{score}%" if score else "")
            ctk.CTkLabel(rgt, textvariable=_sv_score, font=F("label_b"),
                         text_color=bar_col).pack(anchor="e")
            bar_bg = ctk.CTkFrame(rgt, width=80, height=5,
                                   fg_color=BG_FIELD, corner_radius=2)
            bar_bg.pack(pady=(3,0)); bar_bg.pack_propagate(False)
            fill_w = max(2, int(score/100*80))
            _bar_fill = ctk.CTkFrame(bar_bg, width=fill_w, height=5,
                                      fg_color=bar_col, corner_radius=2)
            _bar_fill.place(x=0,y=0)
            # Chevron
            chev_var = ctk.StringVar(value="▾")
            chev_lbl = ctk.CTkLabel(rgt, textvariable=chev_var,
                                     font=F("small"), text_color=MUTED)
            chev_lbl.pack(anchor="e")

            # Detail panel (collapsed by default, expands inside the card)
            det = ctk.CTkFrame(outer, fg_color="transparent")
            # NOT packed yet

            ctk.CTkFrame(det, height=1, fg_color=BG_HOVER
                          ).pack(fill="x", padx=4)
            det_inner = ctk.CTkFrame(det, fg_color="transparent")
            det_inner.pack(fill="x", padx=12, pady=8)

            # Badge
            badge_row = ctk.CTkFrame(det_inner, fg_color="transparent")
            badge_row.pack(anchor="w", pady=(0,6))
            _badge_txt, _badge_col = _badge(job, status, decision, s_col)
            _sv_badge  = ctk.StringVar(value=_badge_txt)
            _sv_badge_col = _badge_col
            _sv_match  = ctk.StringVar(value=f"  Match {score}%{sk_txt}")
            _sv_reason = ctk.StringVar(value=reason)
            ctk.CTkLabel(badge_row, textvariable=_sv_badge,
                         fg_color=_sv_badge_col, corner_radius=4,
                         font=F("small_b"), text_color=BG).pack(side="left")
            ctk.CTkLabel(badge_row, textvariable=_sv_match,
                         font=F("small"), text_color=FG_DIM).pack(side="left")
            ctk.CTkLabel(det_inner, textvariable=_sv_reason,
                         font=F("small"), text_color=FG_SOFT,
                         wraplength=380, justify="left", anchor="w"
                         ).pack(anchor="w")
            # Register StringVars for in-place updates
            self._job_svars.append({
                "icon":     _sv_icon,   "title":    _sv_title,
                "meta":     _sv_meta,   "score":     _sv_score,
                "badge":    _sv_badge,  "match":     _sv_match,
                "reason":   _sv_reason, "bar_fill":  _bar_fill,
                "bar_bg":   bar_bg,
            })

            def _toggle(ev=None, o=outer, d=det, cv=chev_var):
                if d.winfo_ismapped():
                    # collapse — must use grid_remove since placed with grid()
                    d.grid_remove()
                    cv.set("▾")
                    self._job_expanded = None
                else:
                    try:
                        if (self._job_expanded
                                and self._job_expanded is not d
                                and self._job_expanded.winfo_exists()):
                            self._job_expanded.grid_remove()
                    except Exception:
                        pass
                    d.grid(row=1, column=0, sticky="ew")
                    cv.set("▴")
                    self._job_expanded = d

            for w in (outer, summ, info, rgt, chev_lbl):
                w.bind("<Button-1>", _toggle)
            for ch in info.winfo_children() + rgt.winfo_children():
                ch.bind("<Button-1>", _toggle)

    def _refresh_review_btn(self):
        """Enable/disable review button — counts queued + previously applied jobs."""
        try:
            from db.tracker import get_reapply_candidates, get_jobs_ready_to_apply
            from datetime import datetime, timedelta

            # Queued jobs with resume ready — skipped during Phase 3, apply anytime
            queued = get_jobs_ready_to_apply()

            # Previously applied jobs from older runs (> 1 hour ago)
            cutoff = (datetime.now() - timedelta(hours=1)).strftime(
                "%Y-%m-%d %H:%M:%S")
            applied_old = get_reapply_candidates(session_start=cutoff)

            count = len(queued) + len(applied_old)
            if count > 0:
                self._review_btn.configure(
                    state="normal",
                    text="📋  Review Pending (%d)" % count)
            else:
                self._review_btn.configure(
                    state="disabled",
                    text="📋  Review Pending")
        except Exception:
            pass

    def _sched_stats(self):
        """
        Kicks off a background DB read — never blocks the main thread.
        The background thread posts ("stats_data", data) to _q;
        _poll picks it up and calls _apply_stats_data(data).
        """
        active = getattr(self, "_active_tab", 0)
        if self._live or active == 2:
            threading.Thread(target=self._bg_fetch_stats, daemon=True).start()
        interval = 2000 if self._live else 30000
        self._stats_after_id = self.after(interval, self._sched_stats)

    def _bg_fetch_stats(self):
        """Background thread: read DB, post result to queue."""
        try:
            # Counters filter by bot run start — Recent always shows last 12
            bot_since = getattr(self, "_bot_start", None)
            data = _read_stats(since=bot_since)
            self._q.put(("stats_data", data))
        except Exception:
            pass