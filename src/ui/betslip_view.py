"""Bet slip drawer: current legs, stake, and the parlay math with uncertainty."""
from __future__ import annotations
import customtkinter as ctk

from ..analysis.probability import analyze_parlay, LegAnalysis, ParlayAnalysis
from ..analysis import model as M
from ..utils.formatters import format_american, format_pct, format_money
from . import theme as T
from .widgets import Card, Pill, StatBlock, ProbBar, GhostButton, IconButton, Tooltip, make_scroll, hsep
from .state import AppState


class BetSlipPanel(ctk.CTkFrame):
    """Always-available right-hand drawer. Shows the current parlay and its math."""

    def __init__(self, master, state: AppState):
        super().__init__(master, fg_color=T.BG_ELEV_1, corner_radius=0, width=T.SLIP_W)
        self.grid_propagate(False)
        self.state = state

        # ---- header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=T.SP_4, pady=(T.SP_4, T.SP_2))
        ctk.CTkLabel(header, text="BET SLIP", font=T.FONT_HEAD, text_color=T.TEXT).pack(side="left")
        self.count_pill = Pill(header, "0", variant="neutral")
        self.count_pill.pack(side="left", padx=(T.SP_2, 0))
        self.clear_btn = GhostButton(header, "Clear", command=self._clear, width=64, height=26,
                                     font=T.FONT_TINY, text_color=T.TEXT_MUTED, hover_color=T.NEGATIVE_SOFT)
        self.clear_btn.pack(side="right")
        self.mode_pill = Pill(header, "PARLAY", variant="accent")
        self.mode_pill.pack(side="right", padx=(0, T.SP_2))

        # ---- legs
        self.legs_scroll = make_scroll(self, fg_color=T.BG_ELEV_1)
        self.legs_scroll.pack(fill="both", expand=True, padx=T.SP_2, pady=(0, T.SP_2))

        # ---- stake
        stake_card = Card(self, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD, border_width=0)
        stake_card.pack(fill="x", padx=T.SP_3, pady=(0, T.SP_2))
        stake_inner = stake_card.body(padx=T.SP_3, pady=T.SP_3)
        top = ctk.CTkFrame(stake_inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="STAKE", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(side="left")
        self.kelly_chip = GhostButton(top, "Use Kelly", command=self._use_kelly, height=22, width=80,
                                      font=T.FONT_TINY, fg_color=T.MODEL_SOFT, text_color=T.MODEL, hover_color=T.BG_ELEV_4)
        self.kelly_chip.pack(side="right")
        Tooltip(self.kelly_chip, "Set the stake to the fractional-Kelly suggestion for this slip")
        row = ctk.CTkFrame(stake_inner, fg_color="transparent")
        row.pack(fill="x", pady=(T.SP_1, 0))
        ctk.CTkLabel(row, text="$", font=T.FONT_HEAD, text_color=T.TEXT).pack(side="left")
        self.stake_var = ctk.StringVar(value=str(int(state.stake)))
        self.stake_entry = ctk.CTkEntry(
            row, textvariable=self.stake_var, width=84, height=30,
            fg_color=T.BG_ELEV_3, border_width=0, text_color=T.TEXT, font=T.FONT_BOLD,
        )
        self.stake_entry.pack(side="left", padx=(6, T.SP_2))
        self.stake_var.trace_add("write", lambda *_: self._on_stake_change())
        for amt in (10, 25, 50, 100):
            ctk.CTkButton(
                row, text=f"${amt}", width=44, height=24,
                fg_color=T.BG_ELEV_3, hover_color=T.ACCENT_SOFT, text_color=T.TEXT_MUTED,
                font=T.FONT_TINY, corner_radius=T.R_SM,
                command=lambda a=amt: self._set_stake(a),
            ).pack(side="left", padx=2)

        # ---- summary
        summary_card = Card(self, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD, border_width=0)
        summary_card.pack(fill="x", padx=T.SP_3, pady=(0, T.SP_3))
        self.summary = summary_card.body(padx=T.SP_3, pady=T.SP_3)
        self._build_summary()

        state.subscribe(self._on_state_event)
        self.render()

    # ---------------------------------------------------------------- stake

    def _on_stake_change(self):
        try:
            val = float(self.stake_var.get() or 0)
        except ValueError:
            return
        self.state.stake = max(0.0, val)
        self._update_summary()

    def _set_stake(self, amount: float):
        self.stake_var.set(str(int(amount)))

    def _use_kelly(self):
        legs = self.state.bet_slip
        if not legs:
            return
        analysis = analyze_parlay(legs)
        stake = self._kelly_stake(analysis)
        if stake > 0:
            self.stake_var.set(f"{stake:.0f}" if stake >= 10 else f"{stake:.2f}")

    def _clear(self):
        self.state.clear_slip()

    def _on_state_event(self, event: str):
        if event in ("betslip", "settings"):
            self.render()

    # ---------------------------------------------------------------- summary

    def _build_summary(self):
        grid = ctk.CTkFrame(self.summary, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure((0, 1), weight=1, uniform="s")
        self.stat_prob = StatBlock(grid, "Hit probability", "—", sub=" ")
        self.stat_prob.grid(row=0, column=0, sticky="w", pady=(0, T.SP_2))
        self.stat_odds = StatBlock(grid, "Payout odds", "—", sub=" ")
        self.stat_odds.grid(row=0, column=1, sticky="w", pady=(0, T.SP_2))
        self.stat_payout = StatBlock(grid, "To win", "—", sub=" ")
        self.stat_payout.grid(row=1, column=0, sticky="w", pady=(0, T.SP_2))
        self.stat_ev = StatBlock(grid, "Expected value", "—", sub=" ")
        self.stat_ev.grid(row=1, column=1, sticky="w", pady=(0, T.SP_2))
        self.stat_edge = StatBlock(grid, "Edge vs price", "—", sub=" ")
        self.stat_edge.grid(row=2, column=0, sticky="w")
        self.stat_kelly = StatBlock(grid, "Kelly stake", "—", sub=" ")
        self.stat_kelly.grid(row=2, column=1, sticky="w")

        self.prob_bar = ProbBar(self.summary, height=16, bg=T.BG_ELEV_2)
        self.prob_bar.pack(fill="x", pady=(T.SP_2, 0))

        self.corr_lbl = ctk.CTkLabel(self.summary, text="", font=T.FONT_TINY, text_color=T.MODEL,
                                     justify="left", wraplength=330, anchor="w")

        hsep(self.summary, pad_y=T.SP_2)
        self.verdict_lbl = ctk.CTkLabel(
            self.summary, text="Add legs to get a verdict.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED, justify="left", wraplength=320,
            anchor="w", fg_color=T.BG_ELEV_3, corner_radius=T.R_SM, padx=10, pady=8,
        )
        self.verdict_lbl.pack(fill="x")

    def render(self):
        for w in self.legs_scroll.winfo_children():
            w.destroy()
        legs = self.state.bet_slip
        self.count_pill.set(str(len(legs)), variant=("accent" if legs else "neutral"))
        if not legs:
            ctk.CTkLabel(
                self.legs_scroll, text="◌", font=(T.FONT_FAMILY, 26), text_color=T.TEXT_DIM,
            ).pack(pady=(T.SP_6, 0))
            ctk.CTkLabel(
                self.legs_scroll, text="Your slip is empty",
                font=T.FONT_BOLD, text_color=T.TEXT_MUTED,
            ).pack(pady=(T.SP_1, 0))
            ctk.CTkLabel(
                self.legs_scroll, text="Tap + on any line, tile or prop to add it here.",
                font=T.FONT_TINY, text_color=T.TEXT_DIM,
            ).pack()
        else:
            for leg in legs:
                LegRow(self.legs_scroll, leg, self.state).pack(fill="x", pady=3, padx=4)
        self._update_summary()

    def _kelly_stake(self, analysis: ParlayAnalysis) -> float:
        bankroll = float(self.state.config.get("bankroll") or 0) or 1000.0
        kelly_cap = float(self.state.config.get("kelly_fraction") or 0.25)
        frac = analysis.kelly_conservative if M.current_settings().kelly_conservative else analysis.kelly_fraction
        return bankroll * frac * kelly_cap

    def _update_summary(self):
        legs = self.state.bet_slip
        analysis = analyze_parlay(legs)

        if not legs:
            for s in (self.stat_prob, self.stat_odds, self.stat_payout, self.stat_ev, self.stat_edge, self.stat_kelly):
                s.set("—", color=T.TEXT, sub=" ")
            self.prob_bar.set(0.5, 0.5, 0.5, None)
            self.corr_lbl.pack_forget()
            self.mode_pill.set("PARLAY", variant="accent")
            self.verdict_lbl.configure(text="Add legs to get a verdict.", text_color=T.TEXT_MUTED, fg_color=T.BG_ELEV_3)
            return

        stake = self.state.stake
        p = analysis.combined_prob
        band = f"80% band {analysis.prob_low*100:.0f}–{analysis.prob_high*100:.0f}%"
        conf_txt = f"conf {analysis.confidence*100:.0f}%"

        if analysis.is_dfs and analysis.dfs_multiplier > 0:
            mult = analysis.dfs_multiplier
            payout = stake * mult
            profit = payout - stake
            ev_dollars = analysis.dfs_ev_per_dollar * stake
            break_even = 1.0 / mult
            edge = p - break_even
            self.mode_pill.set(f"POWER PLAY {mult:.0f}×", variant="accent")
            self.stat_prob.set(format_pct(p), color=T.edge_color(edge), sub=band)
            self.stat_odds.set(f"{mult:.0f}×", color=T.TEXT, sub=f"break-even {break_even*100:.1f}%")
            self.stat_payout.set(format_money(profit), color=T.TEXT, sub=f"returns {format_money(payout)}")
            self.stat_ev.set(format_money(ev_dollars), color=T.edge_color(analysis.dfs_ev_per_dollar),
                             sub=f"{analysis.dfs_ev_per_dollar*100:+.1f}% per $1")
            self.stat_edge.set(f"{edge*100:+.1f}%", color=T.edge_color(edge), sub=conf_txt)
            self.prob_bar.set(p, analysis.prob_low, analysis.prob_high, break_even)
            kelly_frac = M.kelly_fraction(analysis.prob_low if M.current_settings().kelly_conservative else p, mult)
            bankroll = float(self.state.config.get("bankroll") or 0) or 1000.0
            kelly_stake = bankroll * kelly_frac * float(self.state.config.get("kelly_fraction") or 0.25)
            self.stat_kelly.set(format_money(kelly_stake), color=T.edge_color(kelly_frac),
                                sub=f"{kelly_frac*100:.1f}% full Kelly")
        else:
            payout = stake * analysis.combined_decimal
            profit = payout - stake
            ev_dollars = analysis.ev_per_dollar * stake
            is_kalshi = all(l.bookmaker.lower() == "kalshi" for l in legs)
            self.mode_pill.set("KALSHI" if is_kalshi else ("PARLAY" if len(legs) > 1 else "SINGLE"), variant="accent")
            self.stat_prob.set(format_pct(p), color=T.edge_color(analysis.edge), sub=band)
            self.stat_odds.set(format_american(analysis.combined_american), color=T.TEXT,
                               sub=f"implies {analysis.implied_prob*100:.1f}%")
            self.stat_payout.set(format_money(profit), color=T.TEXT, sub=f"returns {format_money(payout)}")
            self.stat_ev.set(format_money(ev_dollars), color=T.edge_color(analysis.ev_per_dollar),
                             sub=f"{analysis.ev_per_dollar*100:+.1f}% per $1")
            self.stat_edge.set(f"{analysis.edge*100:+.1f}%", color=T.edge_color(analysis.edge), sub=conf_txt)
            self.prob_bar.set(p, analysis.prob_low, analysis.prob_high, analysis.implied_prob)
            kelly_stake = self._kelly_stake(analysis)
            frac = analysis.kelly_conservative if M.current_settings().kelly_conservative else analysis.kelly_fraction
            self.stat_kelly.set(format_money(kelly_stake), color=T.edge_color(frac),
                                sub=f"{frac*100:.1f}% full · {'conservative' if M.current_settings().kelly_conservative else 'point'}")

        if analysis.correlation_note:
            self.corr_lbl.configure(text="⟲ " + analysis.correlation_note)
            if not self.corr_lbl.winfo_manager():
                self.corr_lbl.pack(fill="x", pady=(T.SP_2, 0), before=self.verdict_lbl)
        else:
            self.corr_lbl.pack_forget()

        text, color, bg = self._verdict(analysis, legs)
        self.verdict_lbl.configure(text=text, text_color=color, fg_color=bg)

    def _verdict(self, analysis: ParlayAnalysis, legs: list[LegAnalysis]) -> tuple[str, str, str]:
        n = len(legs)
        p = analysis.combined_prob
        if analysis.is_dfs and analysis.dfs_multiplier > 0:
            break_even = 1.0 / analysis.dfs_multiplier
            gap = p - break_even
            head = f"{n}-leg power play ({analysis.dfs_multiplier:.0f}×)."
            if gap > 0.03:
                return (f"{head}  Model {p*100:.1f}% vs break-even {break_even*100:.1f}% — +EV.",
                        T.POSITIVE, T.POSITIVE_SOFT)
            if gap > 0.0:
                return (f"{head}  Model {p*100:.1f}% barely clears the {break_even*100:.1f}% break-even — slim margin.",
                        T.WARNING, T.WARNING_SOFT)
            return (f"{head}  Break-even {break_even*100:.1f}% is above the model's {p*100:.1f}% — -EV.",
                    T.NEGATIVE, T.NEGATIVE_SOFT)

        head = f"{n}-leg parlay." if n > 1 else "Single."
        low_conf = analysis.confidence < 0.4
        if analysis.edge > 0.03:
            msg = f"{head}  Model sees value — {analysis.edge*100:+.1f}% over the payout-implied odds."
            if low_conf:
                msg += "  Low confidence: size down."
            return msg, T.POSITIVE, T.POSITIVE_SOFT
        if analysis.edge > 0.0:
            return f"{head}  Slight edge, inside the model's error band.", T.WARNING, T.WARNING_SOFT
        if analysis.edge > -0.03:
            return f"{head}  Fair price. Bet only with conviction beyond the model.", T.TEXT_MUTED, T.BG_ELEV_3
        tail = "  Long parlays trade frequency for splash." if (p < 0.15 and n >= 3) else ""
        return f"{head}  Model says -EV ({analysis.edge*100:+.1f}%).{tail}", T.NEGATIVE, T.NEGATIVE_SOFT


class LegRow(ctk.CTkFrame):
    def __init__(self, master, leg: LegAnalysis, state: AppState):
        super().__init__(master, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD)
        self.leg = leg
        self.state = state

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", padx=T.SP_3, pady=T.SP_2)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top, text=leg.selection, font=T.FONT_BOLD, text_color=T.TEXT,
            anchor="w", wraplength=230, justify="left",
        ).pack(side="left", fill="x", expand=True)
        IconButton(top, "×", command=lambda: state.remove_leg(leg), width=24, height=24,
                   hover_color=T.NEGATIVE_SOFT).pack(side="right")
        ctk.CTkLabel(top, text=format_american(leg.price), font=T.FONT_BOLD, text_color=T.ACCENT).pack(side="right", padx=(0, 4))

        ctk.CTkLabel(
            inner, text=f"{leg.matchup}  ·  {leg.bookmaker}",
            font=T.FONT_TINY, text_color=T.TEXT_MUTED, anchor="w",
        ).pack(fill="x")

        bar = ProbBar(inner, height=14, bg=T.BG_ELEV_2)
        bar.pack(fill="x", pady=(T.SP_1, 0))
        bar.set(leg.model_prob, leg.prob_low, leg.prob_high, leg.book_implied)

        metrics = ctk.CTkFrame(inner, fg_color="transparent")
        metrics.pack(fill="x", pady=(T.SP_1, 0))
        Pill(metrics, f"Model {format_pct(leg.model_prob, 0)}", variant="model", font=T.FONT_TINY, height=20).pack(side="left", padx=(0, 4))
        Pill(metrics, f"Edge {leg.edge*100:+.1f}%", variant=T.edge_variant(leg.edge), font=T.FONT_TINY, height=20).pack(side="left", padx=(0, 4))
        if leg.confidence:
            Pill(metrics, f"{leg.confidence*100:.0f}% conf", variant="neutral", font=T.FONT_TINY, height=20,
                 text_color=T.confidence_color(leg.confidence)).pack(side="left")
        Tooltip(metrics, f"Book implied {leg.book_implied*100:.1f}% at {leg.bookmaker}")
        if leg.notes:
            Tooltip(bar, "\n".join(leg.notes))
