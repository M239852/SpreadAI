"""Prop Generator — auto-build a PrizePicks / Underdog player-prop slip."""
from __future__ import annotations
import threading
from typing import Callable
import customtkinter as ctk

from ..analysis.prop_generator import generate_prop_slips, GeneratedPropSlip, MODES as PROP_MODES
from ..analysis.probability import LegAnalysis
from ..api.prizepicks_api import PlayerProp, PP_LEAGUE_IDS, POWER_PAYOUTS
from ..api.underdog_api import UD_SPORT_TAGS, BOOK_CHOICES, fetch_props_from_books, book_from_ui_choice
from ..api.demo_props import demo_props
from ..utils.formatters import format_money
from . import theme as T
from .widgets import (Card, Pill, StatBlock, PageHeader, Segmented, PrimaryButton, ProbBar, Sparkline, EmptyState,
                      Tooltip, entry, option_menu, switch, checkbox, make_scroll, section_label)
from .state import AppState
from .runtime import ui_call

_BOOK_COLORS = {"prizepicks": T.ACCENT, "underdog": "#E8553C", "demo": T.TEXT_MUTED}
SLIP_COUNT_CHOICES = (1, 2, 3, 4, 5)


class PropGeneratorView(ctk.CTkFrame):
    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        self.mode = "balanced"
        self.leg_count = 3
        self.stake = 10.0
        self.stat_filter = ""
        self.team_filter = ""
        self.book_choice = BOOK_CHOICES[0]
        self.use_network = True
        self.slip_count = 1
        self.dedupe_legs = True
        self._current_slips: list[GeneratedPropSlip] = []
        self._props_cache: list[PlayerProp] = []
        self._props_cache_key: tuple | None = None
        state.subscribe(self._on_state_event)

        self.header = PageHeader(self, "Prop Generator", "Auto-build a verified player-prop slip — pick a style and tier, the model does the rest.",
                                 show_source=False)
        self.header.pack(fill="x")
        self.scroll = make_scroll(self)
        self.scroll.pack(fill="both", expand=True, padx=T.SP_4, pady=(0, T.SP_4))
        self._build_controls()
        self.results_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.results_frame.pack(fill="x", pady=(T.SP_3, 0))
        EmptyState(self.results_frame, "No prop slip yet",
                   "Pick a mode and leg count, then Generate — projections are pulled from the DFS books,\neach player's history is modeled, and the slip is scored against the power-play break-even.",
                   icon="✧").pack(pady=T.SP_6)

    # ---------------------------------------------------------------- controls

    def _build_controls(self):
        card = Card(self.scroll)
        card.pack(fill="x", pady=(0, T.SP_2))
        inner = card.body(padx=T.SP_5, pady=T.SP_4)

        section_label(inner, "Mode")
        Segmented(inner, [(k, cfg["label"], cfg["subtitle"]) for k, cfg in PROP_MODES.items()],
                  value=self.mode, command=self._pick_mode, tall=True).pack(fill="x", pady=(T.SP_1, 0))

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(T.SP_4, 0))
        legs_col = ctk.CTkFrame(row, fg_color="transparent")
        legs_col.pack(side="left", padx=(0, T.SP_6))
        section_label(legs_col, "Legs · payout")
        Segmented(legs_col, [(str(n), f"{n} · {POWER_PAYOUTS[n]:.0f}×") for n in sorted(POWER_PAYOUTS)],
                  value=str(self.leg_count), command=lambda k: self._pick_legs(int(k)), height=30).pack(anchor="w", pady=(T.SP_1, 0))

        slips_col = ctk.CTkFrame(row, fg_color="transparent")
        slips_col.pack(side="left", padx=(0, T.SP_6))
        section_label(slips_col, "Slips")
        sr = ctk.CTkFrame(slips_col, fg_color="transparent")
        sr.pack(fill="x", pady=(T.SP_1, 0))
        Segmented(sr, [(str(n), str(n)) for n in SLIP_COUNT_CHOICES], value="1",
                  command=lambda k: self._pick_slip_count(int(k)), height=28, width=34).pack(side="left")
        self.dedupe_var = ctk.BooleanVar(value=self.dedupe_legs)
        switch(sr, "Unique legs", self.dedupe_var, command=self._on_dedupe_toggle).pack(side="left", padx=(T.SP_3, 0))

        fr = ctk.CTkFrame(inner, fg_color="transparent")
        fr.pack(fill="x", pady=(T.SP_4, 0))
        ctk.CTkLabel(fr, text="STAKE $", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(side="left", padx=(0, T.SP_2))
        self.stake_var = ctk.StringVar(value=str(int(self.stake)))
        entry(fr, self.stake_var, width=70, font=T.FONT_BOLD).pack(side="left", padx=(0, T.SP_4))
        self.stake_var.trace_add("write", lambda *_: self._on_stake_change())
        ctk.CTkLabel(fr, text="BOOK", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(side="left", padx=(0, T.SP_2))
        self.book_var = ctk.StringVar(value=self.book_choice)
        option_menu(fr, list(BOOK_CHOICES), command=self._on_book_change, width=140, variable=self.book_var).pack(side="left", padx=(0, T.SP_4))
        ctk.CTkLabel(fr, text="STAT", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(side="left", padx=(0, T.SP_2))
        self.stat_var = ctk.StringVar()
        entry(fr, self.stat_var, width=120, placeholder="Any stat").pack(side="left", padx=(0, T.SP_4))
        self.stat_var.trace_add("write", lambda *_: self._on_stat_change())
        ctk.CTkLabel(fr, text="TEAM", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(side="left", padx=(0, T.SP_2))
        self.team_var = ctk.StringVar()
        entry(fr, self.team_var, width=90, placeholder="Any team").pack(side="left", padx=(0, T.SP_4))
        self.team_var.trace_add("write", lambda *_: self._on_team_change())
        self.net_var = ctk.BooleanVar(value=True)
        cb = checkbox(fr, "Use player history", self.net_var, command=self._on_network_toggle)
        cb.pack(side="right")
        Tooltip(cb, "Pull each player's recent game log from ESPN (slower, more accurate). Off = sport-default volatility around the line.")

        action = ctk.CTkFrame(inner, fg_color="transparent")
        action.pack(fill="x", pady=(T.SP_4, 0))
        self.generate_btn = PrimaryButton(action, "Generate prop slip", command=self._generate, height=38, width=190)
        self.generate_btn.pack(side="left")
        self.progress_lbl = ctk.CTkLabel(action, text="", font=T.FONT_SMALL, text_color=T.TEXT_MUTED)
        self.progress_lbl.pack(side="left", padx=T.SP_3)

    def _pick_mode(self, key: str):
        self.mode = key

    def _pick_legs(self, n: int):
        self.leg_count = n

    def _pick_slip_count(self, n: int):
        self.slip_count = n

    def _on_dedupe_toggle(self):
        self.dedupe_legs = bool(self.dedupe_var.get())

    def _on_stake_change(self):
        try:
            v = float(self.stake_var.get() or 0)
        except ValueError:
            return
        self.stake = max(0.0, v)

    def _set_stake(self, amt: float):
        self.stake_var.set(str(int(amt)))
        self.stake = float(amt)

    def _on_stat_change(self):
        self.stat_filter = (self.stat_var.get() or "").strip()

    def _on_team_change(self):
        self.team_filter = (self.team_var.get() or "").strip()

    def _on_book_change(self, choice: str):
        self.book_choice = choice
        self._props_cache = []
        self._props_cache_key = None

    def _on_network_toggle(self):
        self.use_network = bool(self.net_var.get())

    def _on_state_event(self, event: str):
        if event == "sport":
            self._props_cache = []
            self._props_cache_key = None

    # ---------------------------------------------------------------- generation

    def _generate(self):
        sport_key = self.state.sport_key
        if sport_key not in PP_LEAGUE_IDS and sport_key not in UD_SPORT_TAGS:
            self.progress_lbl.configure(text=f"PrizePicks/Underdog don't cover {sport_key} in SpreadAI.", text_color=T.WARNING)
            return
        self.generate_btn.configure(state="disabled", text="Working…")
        self.progress_lbl.configure(text="Starting…", text_color=T.TEXT_MUTED)
        for w in self.results_frame.winfo_children():
            w.destroy()
        loading = ctk.CTkLabel(self.results_frame, text="Fetching projections & analyzing…", font=T.FONT, text_color=T.TEXT_MUTED)
        loading.pack(pady=T.SP_6)

        mode, legs, stake = self.mode, self.leg_count, self.stake
        stat_f, team_f = self.stat_filter or None, self.team_filter or None
        book_choice = self.book_choice
        book_list = book_from_ui_choice(book_choice)
        book_filter_for_generator = None if not book_list else book_list[0]
        skip_network, slip_count, dedupe = not self.use_network, self.slip_count, self.dedupe_legs
        cache_key = (sport_key, tuple(book_list) if book_list else ("all",))

        def progress_cb(done: int, total: int, note: str):
            ui_call(self.progress_lbl.configure, text=f"{done}/{total} — {note}", text_color=T.TEXT_MUTED)

        def work():
            err = ""
            slips: list[GeneratedPropSlip] = []
            try:
                if self._props_cache and self._props_cache_key == cache_key:
                    props = self._props_cache
                else:
                    ui_call(self.progress_lbl.configure, text=f"Fetching {book_choice}…", text_color=T.TEXT_MUTED)
                    try:
                        props = fetch_props_from_books(sport_key, book_list)
                    except Exception:
                        props = []
                    if not props:
                        props = demo_props(sport_key)
                    self._props_cache, self._props_cache_key = props, cache_key
                slips = generate_prop_slips(props=props, mode=mode, max_legs=legs, min_legs=legs, stake=stake,
                                            count=slip_count, dedupe_legs=dedupe, progress_cb=progress_cb,
                                            skip_network=skip_network, stat_filter=stat_f, team_filter=team_f,
                                            book_filter=book_filter_for_generator)
            except Exception as e:
                err = f"Generation failed: {e}"
            ui_call(self._on_generated, slips, err, loading)

        threading.Thread(target=work, daemon=True).start()

    def _on_generated(self, slips: list[GeneratedPropSlip], err: str, loading_widget):
        loading_widget.destroy()
        self.generate_btn.configure(state="normal", text="Generate prop slip")
        self._current_slips = slips
        if err:
            self.progress_lbl.configure(text=err, text_color=T.NEGATIVE)
            return
        if not slips:
            self.progress_lbl.configure(text="No qualifying props found. Loosen filters or try a different mode.", text_color=T.WARNING)
            return
        if len(slips) < self.slip_count:
            short_note, color = f" (asked for {self.slip_count}, pool only supported {len(slips)})", T.WARNING
        else:
            short_note, color = "", T.POSITIVE
        if len(slips) == 1:
            text = f"Built a {len(slips[0].legs)}-leg {slips[0].mode_label} prop slip{short_note}."
        else:
            text = f"Built {len(slips)} {slips[0].mode_label} prop slips · {sum(len(s.legs) for s in slips)} legs total{short_note}."
        self.progress_lbl.configure(text=text, text_color=color)
        for idx, slip in enumerate(slips, start=1):
            self._render_slip(slip, idx, len(slips))

    # ---------------------------------------------------------------- render

    def _render_slip(self, slip: GeneratedPropSlip, slip_index: int = 1, total_slips: int = 1):
        head = Card(self.results_frame)
        head.pack(fill="x", pady=(T.SP_3 if slip_index > 1 else 0, T.SP_2))
        inner = head.body(padx=T.SP_5, pady=T.SP_4)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        title = (f"{slip.mode_label}  ·  {len(slip.legs)}-leg prop slip" if total_slips == 1
                 else f"Slip {slip_index} of {total_slips}  ·  {slip.mode_label}  ·  {len(slip.legs)} legs")
        ctk.CTkLabel(top, text=title, font=T.FONT_TITLE, text_color=T.TEXT).pack(side="left")
        PrimaryButton(top, "Apply to bet slip", command=lambda s=slip: self._apply_to_slip(s), width=150, height=32).pack(side="right")
        if slip.power_multiplier > 0:
            Pill(top, f"{slip.power_multiplier:.0f}× power play", variant="accent").pack(side="right", padx=(0, T.SP_2))
        book_counts: dict[str, int] = {}
        for pk in slip.picks:
            src = pk.prop.source or "?"
            book_counts[src] = book_counts.get(src, 0) + 1
        Pill(top, "✓ " + "  ·  ".join(f"{n}× {src}" for src, n in book_counts.items()), variant="positive").pack(side="right", padx=(0, T.SP_2))

        p = slip.parlay
        gap = p.combined_prob - slip.break_even_prob
        prob_color = T.POSITIVE if gap >= 0.03 else (T.WARNING if gap >= 0 else T.NEGATIVE)
        stats = ctk.CTkFrame(inner, fg_color="transparent")
        stats.pack(fill="x", pady=(T.SP_3, 0))
        StatBlock(stats, "Hit probability", f"{p.combined_prob*100:.1f}%", value_color=prob_color,
                  sub=f"80% band {p.prob_low*100:.0f}–{p.prob_high*100:.0f}%").pack(side="left", padx=(0, T.SP_5))
        StatBlock(stats, "Break-even", f"{slip.break_even_prob*100:.1f}%" if slip.power_multiplier > 0 else "—").pack(side="left", padx=(0, T.SP_5))
        StatBlock(stats, "Edge vs BE", f"{gap*100:+.1f}%" if slip.power_multiplier > 0 else "—", value_color=prob_color).pack(side="left", padx=(0, T.SP_5))
        StatBlock(stats, "Payout", format_money(slip.projected_payout) if slip.power_multiplier > 0 else "—",
                  sub=f"profit {format_money(slip.projected_profit)}").pack(side="left", padx=(0, T.SP_5))
        StatBlock(stats, "Expected value", format_money(slip.ev_dollars) if slip.power_multiplier > 0 else "—",
                  value_color=(T.POSITIVE if slip.ev_dollars > 0 else T.NEGATIVE)).pack(side="left", padx=(0, T.SP_5))
        StatBlock(stats, "Confidence", f"{p.confidence*100:.0f}%", value_color=T.confidence_color(p.confidence)).pack(side="left")

        bar = ProbBar(inner, height=16, bg=T.BG_ELEV_1)
        bar.pack(fill="x", pady=(T.SP_2, 0))
        bar.set(p.combined_prob, p.prob_low, p.prob_high, slip.break_even_prob if slip.power_multiplier > 0 else None)

        ctk.CTkLabel(inner, text="WHY THIS SLIP", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(anchor="w", pady=(T.SP_3, 0))
        ctk.CTkLabel(inner, text=slip.overall_reasoning, font=T.FONT_SMALL, text_color=T.TEXT, justify="left", wraplength=760,
                     anchor="w").pack(anchor="w", pady=(2, 0), fill="x")
        if p.correlation_note:
            ctk.CTkLabel(inner, text="⟲ " + p.correlation_note, font=T.FONT_TINY, text_color=T.MODEL, justify="left",
                         wraplength=760, anchor="w").pack(anchor="w", pady=(4, 0), fill="x")

        for i, (leg, pick) in enumerate(zip(slip.legs, slip.picks)):
            self._render_leg(leg, pick, i + 1)

    def _render_leg(self, leg: LegAnalysis, pick, idx: int):
        card = Card(self.results_frame, fg_color=T.BG_ELEV_2, border_width=0, corner_radius=T.R_MD)
        card.pack(fill="x", pady=(0, T.SP_2))
        inner = card.body(padx=T.SP_4, pady=T.SP_3)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        hr = ctk.CTkFrame(left, fg_color="transparent")
        hr.pack(fill="x", anchor="w")
        ctk.CTkLabel(hr, text=f"Leg {idx}  ·  {pick.prop.player_name}", font=T.FONT_SUB, text_color=T.TEXT).pack(side="left")
        src = pick.prop.source or "?"
        Pill(hr, f"✓ {src}", variant="neutral", text_color=_BOOK_COLORS.get(src.lower(), T.TEXT)).pack(side="left", padx=(T.SP_2, 0))
        ctk.CTkLabel(left, text=f"{pick.side} {pick.prop.line:g} {pick.prop.stat_type}   ·   {pick.prop.team or '?'} vs {pick.prop.opponent or '?'}",
                     font=T.FONT_SMALL, text_color=(T.POSITIVE if pick.side == "Over" else T.NEGATIVE), anchor="w").pack(anchor="w", pady=(2, 0))

        right = ctk.CTkFrame(top, fg_color="transparent")
        right.pack(side="right")
        Sparkline(right, pick.analysis.samples, pick.prop.line, width=150, height=40, bg=T.BG_ELEV_2).pack(side="left", padx=(0, T.SP_3))
        a = pick.analysis
        StatBlock(right, "Model", f"{pick.prob*100:.1f}%", value_color=T.prob_color(pick.prob),
                  sub=f"conf {a.confidence*100:.0f}%").pack(side="left", padx=T.SP_2)
        StatBlock(right, "Projection", f"{a.projection:.1f}", sub=f"σ {a.stdev:.1f} · {a.distribution}").pack(side="left", padx=T.SP_2)
        StatBlock(right, f"Last {len(a.samples)}" if a.samples else "Samples", f"{a.season_avg:.1f}" if a.samples else "none").pack(side="left", padx=T.SP_2)

        bar = ProbBar(inner, height=14, bg=T.BG_ELEV_2)
        bar.pack(fill="x", pady=(T.SP_2, 0))
        bar.set(leg.model_prob, leg.prob_low, leg.prob_high, leg.book_implied)
        Tooltip(bar, "\n".join(leg.notes))
        if pick.reasoning:
            ctk.CTkLabel(inner, text=pick.reasoning, font=T.FONT_SMALL, text_color=T.TEXT_MUTED, justify="left",
                         wraplength=760, anchor="w").pack(anchor="w", pady=(T.SP_2, 0), fill="x")

    def _apply_to_slip(self, slip: GeneratedPropSlip):
        self.state.clear_slip()
        for leg in slip.legs:
            self.on_add_leg(leg)
