"""Game Analysis: research (form, injuries, rest, H2H), model breakdown per market, news."""
from __future__ import annotations
import threading
import webbrowser
import customtkinter as ctk
from typing import Callable

from ..api.odds_api import Game
from ..analysis.probability import build_leg_analysis, LegAnalysis
from ..analysis.research import research_game, build_factors_for_selection, GameResearch, empty_research
from ..analysis import model as M
from ..utils.formatters import format_american, format_pct, format_game_time, format_money
from . import theme as T
from .widgets import (Card, Pill, StatBlock, ProbBar, FactorBar, PrimaryButton, GhostButton,
                      EmptyState, Tooltip, make_scroll, section_label)
from .state import AppState
from .runtime import ui_call


class AnalysisView(ctk.CTkFrame):
    """Deep analysis of a single game."""

    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        self._current_game_id: str | None = None
        self._research: GameResearch | None = None

        self.scroller = make_scroll(self)
        self.scroller.pack(fill="both", expand=True, padx=T.SP_4, pady=T.SP_4)
        self._show_placeholder()

    def _show_placeholder(self):
        EmptyState(self.scroller, "Pick a game to analyse",
                   "Open a game from the Board with “Analysis →” to see research, the model breakdown and news.",
                   icon="◎").pack(pady=T.SP_6 * 3)

    def show_game(self, game_id: str):
        self._current_game_id = game_id
        for w in self.scroller.winfo_children():
            w.destroy()

        game = self.state.find_game(game_id)
        if game is None:
            EmptyState(self.scroller, "Game not found", "Refresh the board and try again.").pack(pady=T.SP_6 * 2)
            return

        self._build_header(game)
        loading = ctk.CTkLabel(self.scroller, text="Loading research from ESPN…", font=T.FONT, text_color=T.TEXT_MUTED)
        loading.pack(pady=T.SP_4)

        def work():
            try:
                r = research_game(game)
            except Exception:
                r = empty_research(game)
            ui_call(self._on_research_ready, game, r, loading)

        threading.Thread(target=work, daemon=True).start()

    def _on_research_ready(self, game: Game, research: GameResearch, loading_widget):
        if self._current_game_id != game.id:
            return
        loading_widget.destroy()
        self._research = research
        self._build_markets_section(game, research)
        self._build_research_section(game, research)
        self._build_h2h_section(game, research)
        self._build_news_section(research)

    # ---------------------------------------------------------------- header

    def _build_header(self, game: Game):
        head = Card(self.scroller)
        head.pack(fill="x", pady=(0, T.SP_3))
        inner = head.body(padx=T.SP_5, pady=T.SP_4)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=f"{game.away_team}  @  {game.home_team}", font=T.FONT_TITLE, text_color=T.TEXT).pack(side="left")
        Pill(top, game.sport_title, variant="accent").pack(side="right")
        Pill(top, f"model v{M.MODEL_VERSION}", variant="model").pack(side="right", padx=(0, T.SP_2))
        ctk.CTkLabel(
            inner,
            text=f"{format_game_time(game.commence_time)}   ·   {len(game.bookmakers)} books surveyed   ·   "
                 f"{M.sport_params(game.sport_key).label or game.sport_title}: margin σ {M.sport_params(game.sport_key).margin_sd:g}, "
                 f"market efficiency {M.sport_params(game.sport_key).efficiency*100:.0f}%",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        ).pack(anchor="w", pady=(4, 0))

    # ---------------------------------------------------------------- research

    def _build_research_section(self, game: Game, research: GameResearch):
        card = Card(self.scroller)
        card.pack(fill="x", pady=T.SP_3)
        inner = card.body(padx=T.SP_5, pady=T.SP_4)
        section_label(inner, "Team research")
        cols = ctk.CTkFrame(inner, fg_color="transparent")
        cols.pack(fill="x", pady=(T.SP_3, 0))
        cols.grid_columnconfigure((0, 1), weight=1, uniform="t")
        self._team_column(cols, 0, "AWAY", research.away, game.away_team)
        self._team_column(cols, 1, "HOME", research.home, game.home_team)

    def _team_column(self, parent, col: int, label: str, tr, name: str):
        frame = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD)
        frame.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else T.SP_2, 0))
        body = ctk.CTkFrame(frame, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=T.SP_4, pady=T.SP_3)

        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x")
        Pill(top, label, variant="neutral").pack(side="left")
        ctk.CTkLabel(top, text=name, font=T.FONT_SUB, text_color=T.TEXT).pack(side="left", padx=T.SP_2)

        stats = ctk.CTkFrame(body, fg_color="transparent")
        stats.pack(fill="x", pady=(T.SP_3, 0))
        rec = tr.record if tr and tr.record else "—"
        l5 = tr.last5_summary if tr and tr.last5_summary else "—"
        StatBlock(stats, "Record", rec).pack(side="left", padx=(0, T.SP_5))
        StatBlock(stats, "Last 5", l5).pack(side="left", padx=(0, T.SP_5))
        imp = tr.injury_impact if tr else 0.0
        StatBlock(stats, "Injury impact", f"{imp:.2f}",
                  value_color=(T.POSITIVE if imp < 0.1 else T.WARNING if imp < 0.3 else T.NEGATIVE)).pack(side="left", padx=(0, T.SP_5))
        rest = tr.days_rest if tr else None
        StatBlock(stats, "Rest", ("—" if rest is None else f"{rest:.0f}d"),
                  value_color=(T.NEGATIVE if (rest is not None and rest <= 1) else T.TEXT)).pack(side="left")

        last5 = (tr.last5 or []) if tr else []
        if last5:
            form = ctk.CTkFrame(body, fg_color="transparent")
            form.pack(fill="x", pady=(T.SP_3, 0))
            ctk.CTkLabel(form, text="LAST 5", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(anchor="w")
            for g in last5:
                row = ctk.CTkFrame(form, fg_color="transparent")
                row.pack(fill="x", pady=1)
                res = g.get("result", "?")
                Pill(row, res, variant=("positive" if res == "W" else "negative"), font=T.FONT_TINY, height=18, width=28).pack(side="left")
                side = "vs" if g.get("home") else "@"
                ctk.CTkLabel(row, text=f" {side} {g.get('opponent', '')}", font=T.FONT_SMALL, text_color=T.TEXT).pack(side="left")
                ctk.CTkLabel(row, text=g.get("score", ""), font=T.FONT_MONO_SMALL, text_color=T.TEXT_MUTED).pack(side="right")

        injuries = (tr.injuries or []) if tr else []
        if injuries:
            inj = ctk.CTkFrame(body, fg_color="transparent")
            inj.pack(fill="x", pady=(T.SP_3, 0))
            ctk.CTkLabel(inj, text=f"INJURIES ({len(injuries)})", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(anchor="w")
            for i in injuries[:6]:
                row = ctk.CTkFrame(inj, fg_color="transparent")
                row.pack(fill="x", pady=1)
                status = i.get("status", "")
                ctk.CTkLabel(row, text=i.get("player", ""), font=T.FONT_SMALL, text_color=T.TEXT, anchor="w").pack(side="left", fill="x", expand=True)
                ctk.CTkLabel(row, text=i.get("position", ""), font=T.FONT_TINY, text_color=T.TEXT_MUTED, width=36).pack(side="left", padx=6)
                Pill(row, status, variant="neutral", text_color=T.severity_color(status), font=T.FONT_TINY, height=18).pack(side="right")
        elif tr is not None and not tr.record:
            ctk.CTkLabel(body, text="No ESPN research available (offline or unmapped team).",
                         font=T.FONT_TINY, text_color=T.TEXT_DIM).pack(anchor="w", pady=(T.SP_3, 0))

    # ---------------------------------------------------------------- head-to-head

    def _build_h2h_section(self, game: Game, research: GameResearch):
        h2h = research.h2h or []
        summary = research.h2h_summary or {}
        card = Card(self.scroller)
        card.pack(fill="x", pady=T.SP_3)
        inner = card.body(padx=T.SP_5, pady=T.SP_4)
        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text="HEAD-TO-HEAD", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(side="left")
        played = int(summary.get("played") or 0)
        if played:
            bias = float(summary.get("bias") or 0.0)
            Pill(header, f"{game.home_team} {summary.get('record', '')} in last {played}",
                 variant=("positive" if bias > 0.2 else "negative" if bias < -0.2 else "neutral")).pack(side="right")
        if not h2h:
            ctk.CTkLabel(inner, text="No recent head-to-head data found.", font=T.FONT_SMALL, text_color=T.TEXT_MUTED).pack(anchor="w", pady=(T.SP_2, 0))
            return
        for g in h2h:
            row = ctk.CTkFrame(inner, fg_color=T.BG_ELEV_2, corner_radius=T.R_SM)
            row.pack(fill="x", pady=2)
            home_won = bool(g.get("result") == "W")
            winner = game.home_team if home_won else game.away_team
            loser = game.away_team if home_won else game.home_team
            ctk.CTkLabel(row, text=(g.get("date") or "")[:10], font=T.FONT_SMALL, text_color=T.TEXT, width=110, anchor="w").pack(side="left", padx=T.SP_3, pady=6)
            ctk.CTkLabel(row, text=("Home" if g.get("home") else "Away"), font=T.FONT_SMALL, text_color=T.TEXT_MUTED, width=50, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=f"{winner} beat {loser}", font=T.FONT_SMALL,
                         text_color=(T.POSITIVE if home_won else T.NEGATIVE), anchor="w").pack(side="left", padx=T.SP_2)
            ctk.CTkLabel(row, text=g.get("score", ""), font=T.FONT_MONO_SMALL, text_color=T.TEXT, anchor="e").pack(side="right", padx=T.SP_3)

    # ---------------------------------------------------------------- model

    def _build_markets_section(self, game: Game, research: GameResearch):
        card = Card(self.scroller)
        card.pack(fill="x", pady=T.SP_3)
        inner = card.body(padx=T.SP_5, pady=T.SP_4)
        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text="PROBABILITY MODEL", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Weighted de-vigged consensus → cross-market fusion → line shift → research evidence. "
                 "Bars show the model (dot), its 80% band, and the best book's implied price (tick).",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w", justify="left", wraplength=760,
        ).pack(anchor="w", pady=(2, 0))

        rows = [
            ("Moneyline · Away", "h2h", game.away_team),
            ("Moneyline · Home", "h2h", game.home_team),
            ("Spread · Away", "spreads", game.away_team),
            ("Spread · Home", "spreads", game.home_team),
            ("Total · Over", "totals", "Over"),
            ("Total · Under", "totals", "Under"),
        ]
        for label, market, selection in rows:
            MarketRow(inner, game, research, label, market, selection, on_add=self.on_add_leg).pack(fill="x", pady=(T.SP_2, 0))

    # ---------------------------------------------------------------- news

    def _build_news_section(self, research: GameResearch):
        news = research.news or []
        card = Card(self.scroller)
        card.pack(fill="x", pady=(T.SP_3, T.SP_4))
        inner = card.body(padx=T.SP_5, pady=T.SP_4)
        ctk.CTkLabel(inner, text="LATEST NEWS", font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(anchor="w")
        if not news:
            ctk.CTkLabel(inner, text="No news found for this matchup.", font=T.FONT_SMALL, text_color=T.TEXT_MUTED).pack(anchor="w", pady=T.SP_2)
            return
        for a in news[:8]:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=T.SP_2)
            headline = a.get("headline") or "(no title)"
            ctk.CTkLabel(row, text="• " + headline, font=T.FONT_BOLD, text_color=T.TEXT, anchor="w", justify="left", wraplength=760).pack(anchor="w", fill="x")
            if a.get("description"):
                ctk.CTkLabel(row, text=a["description"], font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w", justify="left", wraplength=760).pack(anchor="w", fill="x", pady=(2, 0))
            if a.get("link"):
                GhostButton(row, "Read ↗", command=lambda u=a["link"]: webbrowser.open(u), width=76, height=24,
                            font=T.FONT_TINY, text_color=T.ACCENT).pack(anchor="w", pady=(4, 0))


class MarketRow(ctk.CTkFrame):
    """One market selection: price, probability bar, stats and the model breakdown."""

    def __init__(self, master, game: Game, research: GameResearch, label: str, market_key: str,
                 selection: str, on_add: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD)
        factors = build_factors_for_selection(game, research, market_key, selection)
        leg = build_leg_analysis(game, market_key, selection, factors)

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", padx=T.SP_4, pady=T.SP_3)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        lbl_col = ctk.CTkFrame(top, fg_color="transparent")
        lbl_col.pack(side="left", fill="x", expand=True)
        display = leg.selection if leg else label
        ctk.CTkLabel(lbl_col, text=display, font=T.FONT_SUB, text_color=T.TEXT, anchor="w").pack(anchor="w")
        if leg:
            ctk.CTkLabel(lbl_col, text=f"{label}   ·   best {format_american(leg.price)} @ {leg.bookmaker}",
                         font=T.FONT_TINY, text_color=T.TEXT_MUTED, anchor="w").pack(anchor="w", pady=(2, 0))
        if leg is None:
            ctk.CTkLabel(top, text="No data", font=T.FONT_SMALL, text_color=T.TEXT_DIM).pack(side="right")
            return

        PrimaryButton(top, "Add to slip", command=lambda: on_add(leg), width=110, height=32).pack(side="right")
        stats = ctk.CTkFrame(top, fg_color="transparent")
        stats.pack(side="right", padx=(0, T.SP_4))
        StatBlock(stats, "Book", format_pct(leg.book_implied)).pack(side="left", padx=T.SP_2)
        StatBlock(stats, "Consensus", format_pct(leg.fair_implied)).pack(side="left", padx=T.SP_2)
        StatBlock(stats, "Model", format_pct(leg.model_prob), value_color=T.edge_color(leg.edge),
                  sub=f"{leg.prob_low*100:.0f}–{leg.prob_high*100:.0f}%").pack(side="left", padx=T.SP_2)
        StatBlock(stats, "Edge", f"{leg.edge*100:+.1f}%", value_color=T.edge_color(leg.edge)).pack(side="left", padx=T.SP_2)
        StatBlock(stats, "EV / $1", format_money(leg.ev_per_dollar), value_color=T.edge_color(leg.ev_per_dollar)).pack(side="left", padx=T.SP_2)
        StatBlock(stats, "Conf", f"{leg.confidence*100:.0f}%", value_color=T.confidence_color(leg.confidence)).pack(side="left", padx=T.SP_2)

        bar = ProbBar(inner, height=16, bg=T.BG_ELEV_2)
        bar.pack(fill="x", pady=(T.SP_2, 0))
        bar.set(leg.model_prob, leg.prob_low, leg.prob_high, leg.book_implied)
        Tooltip(bar, "\n".join(leg.notes))

        # --- Breakdown: how the probability was built ---
        breakdown = ctk.CTkFrame(inner, fg_color="transparent")
        breakdown.pack(fill="x", pady=(T.SP_2, 0))
        ctk.CTkLabel(breakdown, text="BREAKDOWN", font=T.FONT_LABEL, text_color=T.TEXT_DIM).pack(anchor="w")

        # Market-structure steps (consensus → cross-market/line shift → prior).
        FactorBar(breakdown, f"Consensus  {leg.fair_implied*100:.1f}%", 0.0, informational=True,
                  description=leg.notes[0] if leg.notes else "").pack(anchor="w")
        structural = leg.prior_prob - leg.fair_implied
        if abs(structural) > 0.0005:
            src = "Cross-market + line shift" if leg.cross_prob is not None else "Line shift"
            desc = "\n".join(n for n in leg.notes if "→" in n or "shift" in n.lower())
            FactorBar(breakdown, src, structural, description=desc).pack(anchor="w")

        # Research evidence, attributed proportionally to the realized move.
        realized = leg.model_prob - leg.prior_prob
        raw = sum(f.contribution for f in leg.adjustments)
        for f in leg.adjustments:
            if f.scale == 0.0:
                FactorBar(breakdown, f.name, 0.0, informational=True, description=f.description).pack(anchor="w")
                continue
            share = (f.contribution / raw) if abs(raw) > 1e-9 else 0.0
            FactorBar(breakdown, f.name, realized * share,
                      description=f"{f.description}\nsignal {f.weight:+.2f} × scale {f.scale:.2f} × confidence {f.confidence:.2f}").pack(anchor="w")
        if leg.push_prob > 0.005:
            ctk.CTkLabel(breakdown, text=f"Whole-number line — ~{leg.push_prob*100:.1f}% push probability (refund).",
                         font=T.FONT_TINY, text_color=T.TEXT_DIM, anchor="w").pack(anchor="w", pady=(2, 0))
