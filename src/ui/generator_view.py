"""Parlay Generator — research-backed multi-leg slips in three styles."""
from __future__ import annotations
import threading
from typing import Callable
import customtkinter as ctk

from ..analysis.generator import generate_slips, GeneratedSlip, MODES
from ..analysis.probability import LegAnalysis
from ..utils.formatters import format_american, format_pct, format_money
from . import theme as T
from .widgets import (Card, Pill, StatBlock, PageHeader, Segmented, PrimaryButton, ProbBar, EmptyState,
                      Tooltip, option_menu, switch, slider, make_scroll, section_label)
from .state import AppState
from .runtime import ui_call

SLIP_COUNT_CHOICES = (1, 2, 3, 4, 5)


class GeneratorView(ctk.CTkFrame):
    ALL_BOOKS_LABEL = "All books (best price)"

    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        self.mode = "balanced"
        self.max_legs = 4
        self.slip_count = 1
        self.dedupe_legs = True
        self.bookmaker_filter: str | None = None
        self._current_slips: list[GeneratedSlip] = []
        state.subscribe(self._on_state_event)

        self.header = PageHeader(self, "Parlay Generator", "Research-backed parlays — pick a style, the model handles the math.",
                                 show_source=False)
        self.header.pack(fill="x")
        self.scroll = make_scroll(self)
        self.scroll.pack(fill="both", expand=True, padx=T.SP_4, pady=(0, T.SP_4))
        self._build_controls()
        self.results_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.results_frame.pack(fill="x", pady=(T.SP_3, 0))
        EmptyState(self.results_frame, "No slip yet",
                   "Pick a mode and click Generate — every game is researched, every market scored,\nand the best legs are assembled with correlation-aware parlay math.",
                   icon="✦").pack(pady=T.SP_6)

    # ---------------------------------------------------------------- controls

    def _build_controls(self):
        card = Card(self.scroll)
        card.pack(fill="x", pady=(0, T.SP_2))
        inner = card.body(padx=T.SP_5, pady=T.SP_4)

        section_label(inner, "Mode")
        self.mode_seg = Segmented(inner, [(k, cfg["label"], cfg["subtitle"]) for k, cfg in MODES.items()],
                                  value=self.mode, command=self._pick_mode, tall=True)
        self.mode_seg.pack(fill="x", pady=(T.SP_1, 0))

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(T.SP_4, 0))
        legs_col = ctk.CTkFrame(row, fg_color="transparent")
        legs_col.pack(side="left", padx=(0, T.SP_6))
        section_label(legs_col, "Max legs")
        legs_row = ctk.CTkFrame(legs_col, fg_color="transparent")
        legs_row.pack(fill="x", pady=(T.SP_1, 0))
        self.legs_val = ctk.CTkLabel(legs_row, text=str(self.max_legs), font=T.FONT_HEAD, text_color=T.TEXT, width=28)
        self.legs_val.pack(side="left")
        s = slider(legs_row, 2, 8, 6, command=self._on_legs_change, width=200)
        s.set(self.max_legs)
        s.pack(side="left", padx=T.SP_2)

        slips_col = ctk.CTkFrame(row, fg_color="transparent")
        slips_col.pack(side="left", padx=(0, T.SP_6))
        section_label(slips_col, "Slips")
        sr = ctk.CTkFrame(slips_col, fg_color="transparent")
        sr.pack(fill="x", pady=(T.SP_1, 0))
        Segmented(sr, [(str(n), str(n)) for n in SLIP_COUNT_CHOICES], value="1",
                  command=lambda k: self._pick_slip_count(int(k)), height=28, width=40).pack(side="left")
        self.dedupe_var = ctk.BooleanVar(value=self.dedupe_legs)
        switch(sr, "Unique legs", self.dedupe_var, command=self._on_dedupe_toggle).pack(side="left", padx=(T.SP_3, 0))

        book_col = ctk.CTkFrame(row, fg_color="transparent")
        book_col.pack(side="left")
        section_label(book_col, "Sportsbook")
        br = ctk.CTkFrame(book_col, fg_color="transparent")
        br.pack(fill="x", pady=(T.SP_1, 0))
        self.book_menu = option_menu(br, [self.ALL_BOOKS_LABEL], command=self._on_book_change, width=200)
        self.book_menu.pack(side="left")
        self.book_menu.set(self.ALL_BOOKS_LABEL)
        Tooltip(self.book_menu, "Pick a book to keep every leg on one slip you could actually place.")

        action = ctk.CTkFrame(inner, fg_color="transparent")
        action.pack(fill="x", pady=(T.SP_4, 0))
        self.generate_btn = PrimaryButton(action, "Generate slip", command=self._generate, height=38, width=180)
        self.generate_btn.pack(side="left")
        self.progress_lbl = ctk.CTkLabel(action, text="", font=T.FONT_SMALL, text_color=T.TEXT_MUTED)
        self.progress_lbl.pack(side="left", padx=T.SP_3)
        self._refresh_book_menu()

    def _pick_mode(self, key: str):
        self.mode = key

    def _on_legs_change(self, val: float):
        self.max_legs = int(round(val))
        self.legs_val.configure(text=str(self.max_legs))

    def _pick_slip_count(self, n: int):
        self.slip_count = n

    def _on_dedupe_toggle(self):
        self.dedupe_legs = bool(self.dedupe_var.get())

    def _on_book_change(self, label: str):
        self.bookmaker_filter = None if label == self.ALL_BOOKS_LABEL else label

    def _on_state_event(self, event: str):
        if event in ("games", "sport"):
            self._refresh_book_menu()

    def _refresh_book_menu(self):
        titles: list[str] = []
        seen: set[str] = set()
        for g in self.state.games:
            for bk in g.bookmakers:
                if bk.title not in seen:
                    seen.add(bk.title)
                    titles.append(bk.title)
        values = [self.ALL_BOOKS_LABEL] + sorted(titles)
        self.book_menu.configure(values=values)
        if self.book_menu.get() not in values:
            self.book_menu.set(self.ALL_BOOKS_LABEL)
            self.bookmaker_filter = None

    # ---------------------------------------------------------------- generation

    def _generate(self):
        games = self.state.games
        if not games:
            self.progress_lbl.configure(text="No games loaded. Refresh first.", text_color=T.NEGATIVE)
            return
        self.generate_btn.configure(state="disabled", text="Working…")
        self.progress_lbl.configure(text="Starting…", text_color=T.TEXT_MUTED)
        for w in self.results_frame.winfo_children():
            w.destroy()
        loading = ctk.CTkLabel(self.results_frame, text="Researching games and scoring markets…", font=T.FONT, text_color=T.TEXT_MUTED)
        loading.pack(pady=T.SP_6)

        def progress_cb(done: int, total: int, note: str):
            ui_call(self.progress_lbl.configure, text=f"{done}/{total} — {note}", text_color=T.TEXT_MUTED)

        book, slip_count, dedupe, mode, max_legs = self.bookmaker_filter, self.slip_count, self.dedupe_legs, self.mode, self.max_legs

        def work():
            slips: list[GeneratedSlip] = []
            err = ""
            try:
                slips = generate_slips(games=games, mode=mode, max_legs=max_legs, min_legs=2, count=slip_count,
                                       dedupe_legs=dedupe, progress_cb=progress_cb, bookmaker_filter=book)
            except Exception as e:
                err = f"Generation failed: {e}"
            ui_call(self._on_generated, slips, err, loading)

        threading.Thread(target=work, daemon=True).start()

    def _on_generated(self, slips: list[GeneratedSlip], err: str, loading_widget):
        loading_widget.destroy()
        self.generate_btn.configure(state="normal", text="Generate slip")
        self._current_slips = slips
        if err:
            self.progress_lbl.configure(text=err, text_color=T.NEGATIVE)
            return
        if not slips:
            hint = (f"No qualifying legs found on {self.bookmaker_filter}. Try All books or a different mode."
                    if self.bookmaker_filter else "No qualifying legs found. Try a different mode.")
            self.progress_lbl.configure(text=hint, text_color=T.WARNING)
            return
        book_note = f" · {self.bookmaker_filter} only" if self.bookmaker_filter else ""
        if len(slips) < self.slip_count:
            short_note, color = f" (asked for {self.slip_count}, slate only supported {len(slips)})", T.WARNING
        else:
            short_note, color = "", T.POSITIVE
        if len(slips) == 1:
            text = f"Built a {len(slips[0].legs)}-leg {slips[0].mode_label} slip{book_note}{short_note}."
        else:
            text = f"Built {len(slips)} {slips[0].mode_label} slips · {sum(len(s.legs) for s in slips)} legs total{book_note}{short_note}."
        self.progress_lbl.configure(text=text, text_color=color)
        for idx, slip in enumerate(slips, start=1):
            self._render_slip(slip, idx, len(slips))

    # ---------------------------------------------------------------- render

    def _render_slip(self, slip: GeneratedSlip, slip_index: int = 1, total_slips: int = 1):
        head = Card(self.results_frame)
        head.pack(fill="x", pady=(T.SP_3 if slip_index > 1 else 0, T.SP_2))
        inner = head.body(padx=T.SP_5, pady=T.SP_4)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        title = f"{slip.mode_label}  ·  {len(slip.legs)} legs" if total_slips == 1 else f"Slip {slip_index} of {total_slips}  ·  {slip.mode_label}  ·  {len(slip.legs)} legs"
        ctk.CTkLabel(top, text=title, font=T.FONT_TITLE, text_color=T.TEXT).pack(side="left")
        PrimaryButton(top, "Apply to bet slip", command=lambda s=slip: self._apply_to_slip(s), width=150, height=32).pack(side="right")
        Pill(top, slip.mode_subtitle, variant="accent").pack(side="right", padx=(0, T.SP_2))
        if self.bookmaker_filter:
            Pill(top, f"{self.bookmaker_filter} only", variant="positive").pack(side="right", padx=(0, T.SP_2))

        p = slip.parlay
        stats = ctk.CTkFrame(inner, fg_color="transparent")
        stats.pack(fill="x", pady=(T.SP_3, 0))
        StatBlock(stats, "Hit probability", format_pct(p.combined_prob), value_color=T.edge_color(p.edge),
                  sub=f"80% band {p.prob_low*100:.0f}–{p.prob_high*100:.0f}%").pack(side="left", padx=(0, T.SP_5))
        StatBlock(stats, "Combined odds", format_american(p.combined_american), sub=f"implies {p.implied_prob*100:.1f}%").pack(side="left", padx=(0, T.SP_5))
        StatBlock(stats, "Risk : Reward", f"1 : {p.risk_reward:.2f}").pack(side="left", padx=(0, T.SP_5))
        StatBlock(stats, "Edge vs price", f"{p.edge*100:+.1f}%", value_color=T.edge_color(p.edge)).pack(side="left", padx=(0, T.SP_5))
        StatBlock(stats, "EV / $1", format_money(p.ev_per_dollar), value_color=T.edge_color(p.ev_per_dollar)).pack(side="left", padx=(0, T.SP_5))
        StatBlock(stats, "Confidence", f"{p.confidence*100:.0f}%", value_color=T.confidence_color(p.confidence)).pack(side="left")

        bar = ProbBar(inner, height=16, bg=T.BG_ELEV_1)
        bar.pack(fill="x", pady=(T.SP_2, 0))
        bar.set(p.combined_prob, p.prob_low, p.prob_high, p.implied_prob)

        ctk.CTkLabel(inner, text="WHY THIS SLIP", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(anchor="w", pady=(T.SP_3, 0))
        ctk.CTkLabel(inner, text=slip.overall_reasoning, font=T.FONT_SMALL, text_color=T.TEXT, justify="left", wraplength=760,
                     anchor="w").pack(anchor="w", pady=(2, 0), fill="x")
        if p.correlation_note:
            ctk.CTkLabel(inner, text="⟲ " + p.correlation_note, font=T.FONT_TINY, text_color=T.MODEL, justify="left",
                         wraplength=760, anchor="w").pack(anchor="w", pady=(4, 0), fill="x")

        for i, leg in enumerate(slip.legs):
            reasoning = slip.per_leg_reasoning[i] if i < len(slip.per_leg_reasoning) else ""
            self._render_leg(leg, reasoning, i + 1)

    def _render_leg(self, leg: LegAnalysis, reasoning: str, idx: int):
        card = Card(self.results_frame, fg_color=T.BG_ELEV_2, border_width=0, corner_radius=T.R_MD)
        card.pack(fill="x", pady=(0, T.SP_2))
        inner = card.body(padx=T.SP_4, pady=T.SP_3)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(left, text=f"Leg {idx}  ·  {leg.selection}", font=T.FONT_SUB, text_color=T.TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(left, text=f"{leg.matchup}  ·  best {format_american(leg.price)} @ {leg.bookmaker}",
                     font=T.FONT_TINY, text_color=T.TEXT_MUTED, anchor="w").pack(anchor="w", pady=(2, 0))
        stats = ctk.CTkFrame(top, fg_color="transparent")
        stats.pack(side="right")
        StatBlock(stats, "Model", format_pct(leg.model_prob), value_color=T.edge_color(leg.edge),
                  sub=f"{leg.prob_low*100:.0f}–{leg.prob_high*100:.0f}%").pack(side="left", padx=T.SP_2)
        StatBlock(stats, "Book", format_pct(leg.book_implied)).pack(side="left", padx=T.SP_2)
        StatBlock(stats, "Edge", f"{leg.edge*100:+.1f}%", value_color=T.edge_color(leg.edge)).pack(side="left", padx=T.SP_2)
        StatBlock(stats, "Conf", f"{leg.confidence*100:.0f}%", value_color=T.confidence_color(leg.confidence)).pack(side="left", padx=T.SP_2)

        bar = ProbBar(inner, height=14, bg=T.BG_ELEV_2)
        bar.pack(fill="x", pady=(T.SP_2, 0))
        bar.set(leg.model_prob, leg.prob_low, leg.prob_high, leg.book_implied)
        Tooltip(bar, "\n".join(leg.notes))

        active = [f for f in leg.adjustments if abs(f.contribution) >= 0.02]
        if active:
            adj = ctk.CTkFrame(inner, fg_color="transparent")
            adj.pack(fill="x", pady=(T.SP_2, 0))
            for f in active:
                Pill(adj, f"{f.name}: {f.contribution:+.2f}", variant=("positive" if f.contribution > 0 else "negative"),
                     font=T.FONT_TINY).pack(side="left", padx=(0, 4))
        if reasoning:
            ctk.CTkLabel(inner, text=reasoning, font=T.FONT_SMALL, text_color=T.TEXT_MUTED, justify="left",
                         wraplength=760, anchor="w").pack(anchor="w", pady=(T.SP_2, 0), fill="x")

    def _apply_to_slip(self, slip: GeneratedSlip):
        self.state.clear_slip()
        for leg in slip.legs:
            self.on_add_leg(leg)
