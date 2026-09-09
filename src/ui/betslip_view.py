"""Bet slip drawer: current legs, stake, and the parlay math with uncertainty."""
from __future__ import annotations
import customtkinter as ctk

from ..analysis.probability import analyze_parlay, LegAnalysis, ParlayAnalysis
from ..analysis import model as M
from ..utils.formatters import format_american, format_pct, format_money
from . import theme as T
from .widgets import Card, Pill, StatBlock, ProbBar, GhostButton, IconButton, Tooltip, make_scroll, hsep
from . import fastwidgets as fw
from .state import AppState


class BetSlipPanel(ctk.CTkFrame):
    """Always-available right-hand drawer. Shows the current parlay and its math."""

    def __init__(self, master, state: AppState):
        super().__init__(master, fg_color=T.BG_ELEV_1, corner_radius=0, width=T.SLIP_W)
        self.grid_propagate(False)
        self.state = state
        self._rows: list["LegRow"] = []
        self._placeholder = None
        self._analysis_key: tuple | None = None
        self._analysis: ParlayAnalysis | None = None

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
        analysis = self._parlay(legs)
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
        legs = self.state.bet_slip
        self.count_pill.set(str(len(legs)), variant=("accent" if legs else "neutral"))
        for row in self._rows[len(legs):]:
            row.pack_forget()
        for i, leg in enumerate(legs):
            if i < len(self._rows):
                row = self._rows[i]
            else:
                row = LegRow(self.legs_scroll, self.state)
                self._rows.append(row)
            row.bind(leg)
            if not row.winfo_manager():
                row.pack(fill="x", pady=3, padx=4)
        if self._placeholder is not None:
            self._placeholder.destroy()
            self._placeholder = None
        if not legs:
            ph = fw.frame(self.legs_scroll, bg=T.BG_ELEV_1)
            ph.pack(pady=(T.SP_6, 0))
            fw.label(ph, "◌", bg=T.BG_ELEV_1, fg=T.TEXT_DIM, font=(T.FONT_FAMILY, 26),
                     anchor="center").pack()
            fw.label(ph, "Your slip is empty", bg=T.BG_ELEV_1, fg=T.TEXT_MUTED,
                     font=T.FONT_BOLD, anchor="center").pack(pady=(T.SP_1, 0))
            fw.label(ph, "Tap + on any line, tile or prop to add it here.", bg=T.BG_ELEV_1,
                     fg=T.TEXT_DIM, font=T.FONT_TINY, anchor="center").pack()
            self._placeholder = ph
        self._update_summary()

    def _kelly_stake(self, analysis: ParlayAnalysis) -> float:
        bankroll = float(self.state.config.get("bankroll") or 0) or 1000.0
        kelly_cap = float(self.state.config.get("kelly_fraction") or 0.25)
        frac = analysis.kelly_conservative if M.current_settings().kelly_conservative else analysis.kelly_fraction
        return bankroll * frac * kelly_cap

    def _parlay(self, legs) -> ParlayAnalysis:
        """`analyze_parlay` cached on the leg set.

        It runs a 6000-sample Gaussian copula, which is cheap once but not
        cheap on every stake keystroke — and the stake does not change the
        combined probability.
        """
        key = tuple((l.game_id, l.market, l.selection, l.price, l.model_prob) for l in legs)
        if key != self._analysis_key or self._analysis is None:
            self._analysis_key = key
            self._analysis = analyze_parlay(list(legs))
        return self._analysis

    def _update_summary(self):
        legs = self.state.bet_slip
        analysis = self._parlay(legs)

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
    """One leg in the slip. Widgets are built once and rebound by `bind`.

    Only the rounded shell is CustomTkinter; the contents are plain Tk from
    `fastwidgets`, so adding a leg no longer rebuilds ~60 canvas-backed
    widgets.
    """

    def __init__(self, master, state: AppState):
        super().__init__(master, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD)
        self.state = state
        self.leg: LegAnalysis | None = None
        bg = T.BG_ELEV_2

        inner = fw.frame(self, bg=bg)
        inner.pack(fill="x", padx=T.SP_3, pady=T.SP_2)

        top = fw.frame(inner, bg=bg)
        top.pack(fill="x")
        self.remove_btn = fw.Button(top, "×", self._remove, bg=bg, fill=bg, hover=T.NEGATIVE_SOFT,
                                    fg=T.TEXT_MUTED, width=24, height=24)
        self.remove_btn.pack(side="right")
        self.price = fw.label(top, "", bg=bg, fg=T.ACCENT, font=T.FONT_BOLD, anchor="e")
        self.price.pack(side="right", padx=(0, 4))
        self.selection = fw.label(top, "", bg=bg, fg=T.TEXT, font=T.FONT_BOLD,
                                  wraplength=210, justify="left")
        self.selection.pack(side="left", fill="x", expand=True)

        self.matchup = fw.label(inner, "", bg=bg, fg=T.TEXT_MUTED, font=T.FONT_TINY)
        self.matchup.pack(fill="x")

        self.bar = fw.ProbBar(inner, height=14, bg=bg)
        self.bar.pack(fill="x", pady=(T.SP_1, 0))

        metrics = fw.frame(inner, bg=bg)
        metrics.pack(fill="x", pady=(T.SP_1, 0))
        self.model_pill = fw.Pill(metrics, "", bg=bg, variant="model", font=T.FONT_TINY, height=18)
        self.model_pill.pack(side="left", padx=(0, 4))
        self.edge_pill = fw.Pill(metrics, "", bg=bg, variant="neutral", font=T.FONT_TINY, height=18)
        self.edge_pill.pack(side="left", padx=(0, 4))
        self.conf_pill = fw.Pill(metrics, "", bg=bg, variant="neutral", font=T.FONT_TINY, height=18)
        self.conf_pill.pack(side="left")
        self._metrics = metrics

    def _remove(self):
        if self.leg is not None:
            self.state.remove_leg(self.leg)

    def bind(self, leg: LegAnalysis):
        self.leg = leg
        self.selection.configure(text=leg.selection)
        self.price.configure(text=format_american(leg.price))
        self.matchup.configure(text=f"{leg.matchup}  ·  {leg.bookmaker}")
        self.bar.set(leg.model_prob, leg.prob_low, leg.prob_high, leg.book_implied)
        self.model_pill.set(f"Model {format_pct(leg.model_prob, 0)}", variant="model")
        self.edge_pill.set(f"Edge {leg.edge*100:+.1f}%", variant=T.edge_variant(leg.edge))
        if leg.confidence:
            self.conf_pill.set(f"{leg.confidence*100:.0f}% conf", variant="neutral",
                               fg=T.confidence_color(leg.confidence))
            if not self.conf_pill.winfo_manager():
                self.conf_pill.pack(side="left")
        elif self.conf_pill.winfo_manager():
            self.conf_pill.pack_forget()
        fw.tip(self._metrics, f"Book implied {leg.book_implied*100:.1f}% at {leg.bookmaker}")
        fw.tip(self.bar, "\n".join(leg.notes))
