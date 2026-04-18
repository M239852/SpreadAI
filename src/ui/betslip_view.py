from __future__ import annotations
import customtkinter as ctk

from ..analysis.probability import analyze_parlay, LegAnalysis
from ..utils.formatters import format_american, format_pct, format_money
from . import theme as T
from .widgets import Card, Pill, StatBlock, make_scroll, hsep
from .state import AppState


class BetSlipPanel(ctk.CTkFrame):
    """Always-visible right-hand panel. Shows the current parlay and math."""

    def __init__(self, master, state: AppState):
        super().__init__(
            master,
            fg_color=T.BG_ELEV_1,
            corner_radius=0,
            border_width=0,
        )
        self.state = state

        self.header = ctk.CTkFrame(self, fg_color="transparent", height=60)
        self.header.pack(fill="x", padx=20, pady=(20, 8))
        ctk.CTkLabel(self.header, text="BET SLIP", font=T.FONT_HEAD, text_color=T.TEXT).pack(side="left")
        self.clear_btn = ctk.CTkButton(
            self.header, text="Clear", width=60, height=28,
            fg_color=T.BG_ELEV_3, hover_color=T.NEGATIVE, text_color=T.TEXT_MUTED,
            font=T.FONT_TINY, corner_radius=6,
            command=self._clear,
        )
        self.clear_btn.pack(side="right")

        # Legs list (scrollable)
        self.legs_wrap = ctk.CTkFrame(self, fg_color="transparent")
        self.legs_wrap.pack(fill="both", expand=True, padx=12, pady=6)

        self.legs_scroll = make_scroll(self.legs_wrap, fg_color=T.BG_ELEV_1, width=340)
        self.legs_scroll.pack(fill="both", expand=True)

        self.empty_lbl = ctk.CTkLabel(
            self.legs_scroll,
            text="Tap + on any line to add it here.",
            font=T.FONT_SMALL,
            text_color=T.TEXT_MUTED,
        )

        # Stake input
        self.stake_card = Card(self, fg_color=T.BG_ELEV_2, corner_radius=10, border_width=0)
        self.stake_card.pack(fill="x", padx=12, pady=(8, 4))
        stake_inner = ctk.CTkFrame(self.stake_card, fg_color="transparent")
        stake_inner.pack(fill="x", padx=14, pady=12)
        ctk.CTkLabel(stake_inner, text="STAKE", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
        entry_row = ctk.CTkFrame(stake_inner, fg_color="transparent")
        entry_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(entry_row, text="$", font=T.FONT_HEAD, text_color=T.TEXT).pack(side="left")
        self.stake_var = ctk.StringVar(value=str(int(state.stake)))
        self.stake_entry = ctk.CTkEntry(
            entry_row, textvariable=self.stake_var, width=90, height=32,
            fg_color=T.BG_ELEV_3, border_width=0, text_color=T.TEXT,
            font=T.FONT_BOLD,
        )
        self.stake_entry.pack(side="left", padx=8)
        self.stake_var.trace_add("write", lambda *_: self._on_stake_change())

        # Quick stake buttons
        quick = ctk.CTkFrame(stake_inner, fg_color="transparent")
        quick.pack(fill="x", pady=(8, 0))
        for amt in (5, 10, 25, 50, 100):
            ctk.CTkButton(
                quick, text=f"${amt}", width=48, height=26,
                fg_color=T.BG_ELEV_3, hover_color=T.ACCENT, text_color=T.TEXT,
                font=T.FONT_TINY, corner_radius=6,
                command=lambda a=amt: self._set_stake(a),
            ).pack(side="left", padx=2)

        # Summary area
        self.summary_card = Card(self, fg_color=T.BG_ELEV_2, corner_radius=10, border_width=0)
        self.summary_card.pack(fill="x", padx=12, pady=(6, 16))
        self.summary_inner = ctk.CTkFrame(self.summary_card, fg_color="transparent")
        self.summary_inner.pack(fill="x", padx=14, pady=14)

        self._build_summary_placeholders()

        state.subscribe(self._on_state_event)
        self.render()

    # ---------------- Stake plumbing ----------------

    def _on_stake_change(self):
        try:
            val = float(self.stake_var.get() or 0)
        except ValueError:
            return
        self.state.stake = max(0.0, val)
        self._update_summary()

    def _set_stake(self, amount: float):
        self.stake_var.set(str(int(amount)))

    def _clear(self):
        self.state.clear_slip()

    def _on_state_event(self, event: str):
        if event == "betslip":
            self.render()

    # ---------------- Rendering ----------------

    def _build_summary_placeholders(self):
        ctk.CTkLabel(self.summary_inner, text="PARLAY", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")

        row1 = ctk.CTkFrame(self.summary_inner, fg_color="transparent")
        row1.pack(fill="x", pady=(8, 0))
        self.stat_prob = StatBlock(row1, "Hit probability", "—")
        self.stat_prob.pack(side="left", padx=(0, 16))
        self.stat_odds = StatBlock(row1, "Combined odds", "—")
        self.stat_odds.pack(side="left")

        row2 = ctk.CTkFrame(self.summary_inner, fg_color="transparent")
        row2.pack(fill="x", pady=(12, 0))
        self.stat_risk_reward = StatBlock(row2, "Risk : Reward", "—")
        self.stat_risk_reward.pack(side="left", padx=(0, 16))
        self.stat_edge = StatBlock(row2, "Edge vs price", "—")
        self.stat_edge.pack(side="left")

        row3 = ctk.CTkFrame(self.summary_inner, fg_color="transparent")
        row3.pack(fill="x", pady=(12, 0))
        self.stat_payout = StatBlock(row3, "Potential payout", "—", value_color=T.TEXT)
        self.stat_payout.pack(side="left", padx=(0, 16))
        self.stat_ev = StatBlock(row3, "Expected value", "—")
        self.stat_ev.pack(side="left")

        row4 = ctk.CTkFrame(self.summary_inner, fg_color="transparent")
        row4.pack(fill="x", pady=(12, 0))
        self.stat_kelly = StatBlock(row4, "Suggested stake (Kelly)", "—")
        self.stat_kelly.pack(side="left")

        hsep(self.summary_inner, pad_y=10)

        self.verdict_lbl = ctk.CTkLabel(
            self.summary_inner, text="Add legs to get a verdict.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED, justify="left", wraplength=300,
        )
        self.verdict_lbl.pack(anchor="w")

    def render(self):
        for w in self.legs_scroll.winfo_children():
            w.destroy()

        if not self.state.bet_slip:
            self.empty_lbl = ctk.CTkLabel(
                self.legs_scroll,
                text="Tap + on any line to add it here.",
                font=T.FONT_SMALL,
                text_color=T.TEXT_MUTED,
            )
            self.empty_lbl.pack(pady=40)
        else:
            for leg in self.state.bet_slip:
                LegRow(self.legs_scroll, leg, self.state).pack(fill="x", pady=4, padx=4)

        self._update_summary()

    def _update_summary(self):
        legs = self.state.bet_slip
        analysis = analyze_parlay(legs)

        if not legs:
            self.stat_prob.set("—", color=T.TEXT)
            self.stat_odds.set("—", color=T.TEXT)
            self.stat_risk_reward.set("—", color=T.TEXT)
            self.stat_edge.set("—", color=T.TEXT)
            self.stat_payout.set("—", color=T.TEXT)
            self.stat_ev.set("—", color=T.TEXT)
            self.stat_kelly.set("—", color=T.TEXT)
            self.verdict_lbl.configure(text="Add legs to get a verdict.", text_color=T.TEXT_MUTED)
            return

        stake = self.state.stake

        if analysis.is_dfs and analysis.dfs_multiplier > 0:
            # DFS pick-em: use PrizePicks power-play multiplier instead of parlay math
            payout = stake * analysis.dfs_multiplier
            profit = payout - stake
            ev_dollars = analysis.dfs_ev_per_dollar * stake
            self.stat_prob.set(format_pct(analysis.combined_prob), color=T.edge_color(analysis.dfs_ev_per_dollar))
            self.stat_odds.set(f"{analysis.dfs_multiplier:.0f}x  (PrizePicks)", color=T.ACCENT)
            self.stat_risk_reward.set(f"1 : {analysis.dfs_multiplier - 1:.0f}", color=T.TEXT)
            dfs_edge = analysis.combined_prob - (1.0 / analysis.dfs_multiplier)
            self.stat_edge.set(f"{dfs_edge * 100:+.1f}%", color=T.edge_color(dfs_edge))
            self.stat_payout.set(f"{format_money(payout)}  ({format_money(profit)} net)", color=T.TEXT)
            self.stat_ev.set(format_money(ev_dollars), color=T.edge_color(analysis.dfs_ev_per_dollar))
        else:
            payout = stake * analysis.combined_decimal
            profit = payout - stake
            ev_dollars = analysis.ev_per_dollar * stake
            self.stat_prob.set(format_pct(analysis.combined_prob), color=T.edge_color(analysis.edge))
            self.stat_odds.set(format_american(analysis.combined_american), color=T.TEXT)
            self.stat_risk_reward.set(f"1 : {analysis.risk_reward:.2f}", color=T.TEXT)
            self.stat_edge.set(f"{analysis.edge * 100:+.1f}%", color=T.edge_color(analysis.edge))
            self.stat_payout.set(f"{format_money(payout)}  ({format_money(profit)} net)", color=T.TEXT)
            self.stat_ev.set(format_money(ev_dollars), color=T.edge_color(analysis.ev_per_dollar))

        bankroll = float(self.state.config.get("bankroll") or 0) or 1000.0
        kelly_cap = float(self.state.config.get("kelly_fraction") or 0.25)
        kelly_stake = bankroll * analysis.kelly_fraction * kelly_cap
        self.stat_kelly.set(
            f"{format_money(kelly_stake)}  ({analysis.kelly_fraction * 100:.1f}% full)",
            color=T.edge_color(analysis.kelly_fraction),
        )

        self.verdict_lbl.configure(
            text=self._verdict(analysis, legs),
            text_color=T.edge_color(analysis.edge),
        )

    def _verdict(self, analysis, legs: list[LegAnalysis]) -> str:
        n = len(legs)
        if analysis.is_dfs and analysis.dfs_multiplier > 0:
            break_even = 1.0 / analysis.dfs_multiplier
            gap = analysis.combined_prob - break_even
            parts = [f"{n}-leg PrizePicks power play ({analysis.dfs_multiplier:.0f}x)."]
            if gap > 0.03:
                parts.append(f"Break-even is {break_even * 100:.1f}%; model sees {analysis.combined_prob * 100:.1f}%. +EV.")
            elif gap > 0.0:
                parts.append(f"Break-even {break_even * 100:.1f}% — slim margin of value.")
            else:
                parts.append(f"Break-even {break_even * 100:.1f}% is above model's {analysis.combined_prob * 100:.1f}% — -EV.")
            return "  ".join(parts)

        parts = [f"{n}-leg parlay"]
        if analysis.edge > 0.03:
            parts.append("Model sees value — probability exceeds the payout-implied odds.")
        elif analysis.edge > 0.0:
            parts.append("Slight edge vs. book; within margin of error.")
        elif analysis.edge > -0.03:
            parts.append("Fair price. Bet only with conviction beyond the model.")
        else:
            parts.append("Model suggests this is a -EV spot.")
        if analysis.combined_prob < 0.15 and n >= 3:
            parts.append("Remember: long parlays trade frequency for splash.")
        return "  ".join(parts)


class LegRow(ctk.CTkFrame):
    def __init__(self, master, leg: LegAnalysis, state: AppState):
        super().__init__(master, fg_color=T.BG_ELEV_2, corner_radius=8)
        self.leg = leg
        self.state = state

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=8)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top, text=leg.selection, font=T.FONT_BOLD, text_color=T.TEXT,
            anchor="w", wraplength=220, justify="left",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            top, text=format_american(leg.price), font=T.FONT_BOLD, text_color=T.ACCENT,
        ).pack(side="right")

        sub = ctk.CTkFrame(inner, fg_color="transparent")
        sub.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(
            sub, text=f"{leg.matchup}  ·  {leg.bookmaker}",
            font=T.FONT_TINY, text_color=T.TEXT_MUTED, anchor="w",
        ).pack(side="left", fill="x", expand=True)

        remove_btn = ctk.CTkButton(
            sub, text="×", width=22, height=22, corner_radius=4,
            fg_color="transparent", hover_color=T.NEGATIVE,
            text_color=T.TEXT_MUTED, font=T.FONT_BOLD,
            command=lambda: state.remove_leg(leg),
        )
        remove_btn.pack(side="right")

        # Leg metrics row
        metrics = ctk.CTkFrame(inner, fg_color="transparent")
        metrics.pack(fill="x", pady=(6, 0))
        Pill(metrics, f"Model {format_pct(leg.model_prob, 0)}", color=T.BG_ELEV_3, text_color=T.edge_color(leg.edge)).pack(side="left", padx=(0, 4))
        Pill(metrics, f"Book {format_pct(leg.book_implied, 0)}", color=T.BG_ELEV_3, text_color=T.TEXT_MUTED).pack(side="left", padx=(0, 4))
        Pill(metrics, f"Edge {leg.edge * 100:+.1f}%", color=T.BG_ELEV_3, text_color=T.edge_color(leg.edge)).pack(side="left")
