from __future__ import annotations
import threading
import webbrowser
import customtkinter as ctk
from typing import Callable

from ..api.odds_api import Game
from ..analysis.probability import (
    build_leg_analysis, compute_fair_prob_from_game, best_price, LegAnalysis,
)
from ..analysis.research import research_game, build_factors_for_selection, GameResearch
from ..utils.formatters import format_american, format_pct, format_game_time, format_money
from . import theme as T
from .widgets import Card, Pill, StatBlock, make_scroll, hsep
from .state import AppState


class AnalysisView(ctk.CTkFrame):
    """Deep analysis of a single game: news, injuries, form, probability per market."""

    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        self._current_game_id: str | None = None
        self._research: GameResearch | None = None

        self.scroller = make_scroll(self)
        self.scroller.pack(fill="both", expand=True, padx=16, pady=16)

        self.placeholder = ctk.CTkLabel(
            self.scroller,
            text="Pick a game from the board to see a full analysis.",
            font=T.FONT, text_color=T.TEXT_MUTED,
        )
        self.placeholder.pack(pady=80)

    def show_game(self, game_id: str):
        self._current_game_id = game_id
        for w in self.scroller.winfo_children():
            w.destroy()

        game = self.state.find_game(game_id)
        if game is None:
            ctk.CTkLabel(self.scroller, text="Game not found.", font=T.FONT, text_color=T.TEXT_MUTED).pack(pady=40)
            return

        self._build_header(game)
        loading = ctk.CTkLabel(self.scroller, text="Loading research…", font=T.FONT, text_color=T.TEXT_MUTED)
        loading.pack(pady=16)

        # Load external research in a worker thread
        def work():
            try:
                r = research_game(game)
            except Exception as e:
                r = GameResearch(
                    game_id=game.id, sport_key=game.sport_key,
                    matchup=f"{game.away_team} @ {game.home_team}",
                    home=None, away=None,  # type: ignore[arg-type]
                )
                self._error = str(e)
            self.after(0, lambda: self._on_research_ready(game, r, loading))

        threading.Thread(target=work, daemon=True).start()

    def _on_research_ready(self, game: Game, research: GameResearch, loading_widget):
        if self._current_game_id != game.id:
            return
        loading_widget.destroy()
        self._research = research
        self._build_research_section(game, research)
        self._build_h2h_section(game, research)
        self._build_markets_section(game, research)
        self._build_news_section(research)

    # ---------------- Header ----------------
    def _build_header(self, game: Game):
        head = Card(self.scroller)
        head.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(head, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")

        title = ctk.CTkLabel(
            top, text=f"{game.away_team}   @   {game.home_team}",
            font=T.FONT_TITLE, text_color=T.TEXT,
        )
        title.pack(side="left")

        Pill(top, game.sport_title, color=T.BG_ELEV_3, text_color=T.ACCENT).pack(side="right", padx=4)

        sub = ctk.CTkLabel(
            inner,
            text=f"{format_game_time(game.commence_time)}   ·   {len(game.bookmakers)} books surveyed",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        )
        sub.pack(anchor="w", pady=(4, 0))

    # ---------------- Research section ----------------
    def _build_research_section(self, game: Game, research: GameResearch):
        card = Card(self.scroller)
        card.pack(fill="x", pady=12)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        ctk.CTkLabel(
            inner, text="TEAM RESEARCH", font=T.FONT_TINY, text_color=T.TEXT_MUTED
        ).pack(anchor="w")

        cols = ctk.CTkFrame(inner, fg_color="transparent")
        cols.pack(fill="x", pady=(10, 0))
        cols.grid_columnconfigure((0, 1), weight=1, uniform="t")

        self._team_column(cols, 0, "AWAY", research.away, game.away_team)
        self._team_column(cols, 1, "HOME", research.home, game.home_team)

    def _team_column(self, parent, col: int, label: str, team_research, name: str):
        frame = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_2, corner_radius=10)
        frame.grid(row=0, column=col, sticky="nsew", padx=6, pady=4)
        body = ctk.CTkFrame(frame, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=14)

        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x")
        Pill(top, label, color=T.BG_ELEV_3, text_color=T.TEXT_MUTED).pack(side="left")
        ctk.CTkLabel(top, text=name, font=T.FONT_BOLD, text_color=T.TEXT).pack(side="left", padx=10)

        stats = ctk.CTkFrame(body, fg_color="transparent")
        stats.pack(fill="x", pady=(12, 0))

        rec_val = team_research.record if team_research and team_research.record else "—"
        l5_val = team_research.last5_summary if team_research and team_research.last5_summary else "—"
        StatBlock(stats, "Record", rec_val).pack(side="left", padx=(0, 24))
        StatBlock(stats, "Last 5", l5_val).pack(side="left", padx=(0, 24))
        imp = team_research.injury_impact if team_research else 0.0
        imp_color = T.POSITIVE if imp < 0.1 else (T.WARNING if imp < 0.3 else T.NEGATIVE)
        StatBlock(stats, "Injury impact", f"{imp:.2f}", value_color=imp_color).pack(side="left")

        # Recent form list
        last5 = (team_research.last5 or []) if team_research else []
        if last5:
            form_frame = ctk.CTkFrame(body, fg_color="transparent")
            form_frame.pack(fill="x", pady=(12, 0))
            ctk.CTkLabel(form_frame, text="Last 5 games", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
            for g in last5:
                row = ctk.CTkFrame(form_frame, fg_color="transparent")
                row.pack(fill="x", pady=2)
                res = g.get("result", "?")
                color = T.POSITIVE if res == "W" else T.NEGATIVE
                ctk.CTkLabel(row, text=res, font=T.FONT_BOLD, text_color=color, width=20).pack(side="left")
                side = "vs" if g.get("home") else "@"
                ctk.CTkLabel(
                    row, text=f" {side} {g.get('opponent', '')}", font=T.FONT_SMALL,
                    text_color=T.TEXT,
                ).pack(side="left")
                ctk.CTkLabel(row, text=g.get("score", ""), font=T.FONT_SMALL, text_color=T.TEXT_MUTED).pack(side="right")

        # Injuries list
        injuries = (team_research.injuries or []) if team_research else []
        if injuries:
            inj_frame = ctk.CTkFrame(body, fg_color="transparent")
            inj_frame.pack(fill="x", pady=(12, 0))
            ctk.CTkLabel(inj_frame, text=f"Injuries ({len(injuries)})", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
            for inj in injuries[:6]:
                row = ctk.CTkFrame(inj_frame, fg_color="transparent")
                row.pack(fill="x", pady=2)
                status = inj.get("status", "")
                ctk.CTkLabel(
                    row, text=inj.get("player", ""), font=T.FONT_SMALL, text_color=T.TEXT, anchor="w",
                ).pack(side="left", fill="x", expand=True)
                ctk.CTkLabel(
                    row, text=inj.get("position", ""), font=T.FONT_TINY, text_color=T.TEXT_MUTED, width=36,
                ).pack(side="left", padx=6)
                Pill(row, status, color=T.BG_ELEV_3, text_color=T.severity_color(status)).pack(side="right")

    # ---------------- Head-to-head section ----------------
    def _build_h2h_section(self, game: Game, research: GameResearch):
        h2h = research.h2h or []
        summary = research.h2h_summary or {}
        card = Card(self.scroller)
        card.pack(fill="x", pady=12)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text="HEAD-TO-HEAD", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")
        played = int(summary.get("played") or 0)
        if played:
            bias = float(summary.get("bias") or 0.0)
            bias_color = T.POSITIVE if bias > 0.2 else (T.NEGATIVE if bias < -0.2 else T.TEXT_MUTED)
            Pill(
                header,
                f"{game.home_team} {summary.get('record','')} in last {played}",
                color=T.BG_ELEV_3, text_color=bias_color,
            ).pack(side="right")

        if not h2h:
            ctk.CTkLabel(
                inner, text="No recent head-to-head data found.",
                font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
            ).pack(anchor="w", pady=(8, 0))
            return

        table = ctk.CTkFrame(inner, fg_color="transparent")
        table.pack(fill="x", pady=(10, 0))
        # Header row
        hdr = ctk.CTkFrame(table, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 4))
        for text, w, anchor in (
            ("DATE", 120, "w"), ("SITE", 60, "w"), ("RESULT", 200, "w"), ("SCORE", 100, "e"),
        ):
            ctk.CTkLabel(hdr, text=text, font=T.FONT_TINY, text_color=T.TEXT_MUTED,
                         width=w, anchor=anchor).pack(side="left", padx=4)
        for g in h2h:
            row = ctk.CTkFrame(table, fg_color=T.BG_ELEV_2, corner_radius=8)
            row.pack(fill="x", pady=2)
            date_str = (g.get("date") or "")[:10]
            site = "Home" if g.get("home") else "Away"
            home_won = bool(g.get("result") == "W")
            winner = game.home_team if home_won else game.away_team
            loser = game.away_team if home_won else game.home_team
            color = T.POSITIVE if home_won else T.NEGATIVE
            ctk.CTkLabel(row, text=date_str, font=T.FONT_SMALL, text_color=T.TEXT, width=120, anchor="w").pack(side="left", padx=4, pady=6)
            ctk.CTkLabel(row, text=site, font=T.FONT_SMALL, text_color=T.TEXT_MUTED, width=60, anchor="w").pack(side="left", padx=4)
            ctk.CTkLabel(
                row, text=f"{winner} beat {loser}",
                font=T.FONT_SMALL, text_color=color, width=260, anchor="w",
            ).pack(side="left", padx=4)
            ctk.CTkLabel(row, text=g.get("score", ""), font=T.FONT_BOLD, text_color=T.TEXT, width=100, anchor="e").pack(side="right", padx=10)

    # ---------------- Markets section ----------------
    def _build_markets_section(self, game: Game, research: GameResearch):
        card = Card(self.scroller)
        card.pack(fill="x", pady=12)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text="PROBABILITY MODEL", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Best price across all books, then de-vigged consensus adjusted by research.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 0))

        rows: list[tuple[str, str, str]] = [
            ("Moneyline · Away", "h2h", game.away_team),
            ("Moneyline · Home", "h2h", game.home_team),
            ("Spread · Away", "spreads", game.away_team),
            ("Spread · Home", "spreads", game.home_team),
            ("Total · Over", "totals", "Over"),
            ("Total · Under", "totals", "Under"),
        ]

        grid = ctk.CTkFrame(inner, fg_color="transparent")
        grid.pack(fill="x", pady=(12, 0))
        for i, (label, market, selection) in enumerate(rows):
            MarketRow(
                grid, game, research, label, market, selection,
                on_add=self.on_add_leg,
            ).pack(fill="x", pady=6)

    # ---------------- News section ----------------
    def _build_news_section(self, research: GameResearch):
        news = research.news or []
        card = Card(self.scroller)
        card.pack(fill="x", pady=(12, 16))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        ctk.CTkLabel(inner, text="LATEST NEWS", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
        if not news:
            ctk.CTkLabel(
                inner, text="No news found for this matchup.",
                font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
            ).pack(anchor="w", pady=10)
            return

        for a in news[:8]:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=6)
            headline = a.get("headline") or "(no title)"
            desc = a.get("description") or ""
            link = a.get("link") or ""
            h = ctk.CTkLabel(row, text="• " + headline, font=T.FONT_BOLD, text_color=T.TEXT, anchor="w", justify="left", wraplength=820)
            h.pack(anchor="w", fill="x")
            if desc:
                d = ctk.CTkLabel(
                    row, text=desc, font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
                    anchor="w", justify="left", wraplength=820,
                )
                d.pack(anchor="w", fill="x", pady=(2, 0))
            if link:
                link_btn = ctk.CTkButton(
                    row, text="Read ↗", width=80, height=24,
                    fg_color=T.BG_ELEV_3, hover_color=T.ACCENT, text_color=T.ACCENT,
                    font=T.FONT_TINY, corner_radius=6,
                    command=lambda u=link: webbrowser.open(u),
                )
                link_btn.pack(anchor="w", pady=(4, 0))


class MarketRow(ctk.CTkFrame):
    def __init__(
        self,
        master,
        game: Game,
        research: GameResearch,
        label: str,
        market_key: str,
        selection: str,
        on_add: Callable[[LegAnalysis], None],
    ):
        super().__init__(master, fg_color=T.BG_ELEV_2, corner_radius=10)

        factors = build_factors_for_selection(game, research, market_key, selection)
        leg = build_leg_analysis(game, market_key, selection, factors)

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=12)

        lbl_col = ctk.CTkFrame(inner, fg_color="transparent")
        lbl_col.pack(side="left", fill="x", expand=True)
        display = label
        if leg and leg.point is not None and market_key != "totals":
            display = f"{label} ({leg.point:+g})"
        elif leg and leg.point is not None and market_key == "totals":
            display = f"{label} {leg.point}"
        ctk.CTkLabel(lbl_col, text=display, font=T.FONT_BOLD, text_color=T.TEXT).pack(anchor="w")
        if leg:
            ctk.CTkLabel(
                lbl_col,
                text=f"Best: {leg.bookmaker}  ·  {format_american(leg.price)}",
                font=T.FONT_TINY, text_color=T.TEXT_MUTED,
            ).pack(anchor="w", pady=(2, 0))

        stats = ctk.CTkFrame(inner, fg_color="transparent")
        stats.pack(side="left", padx=20)
        if leg is None:
            ctk.CTkLabel(stats, text="No data", font=T.FONT_SMALL, text_color=T.TEXT_DIM).pack()
        else:
            StatBlock(stats, "Book implied", format_pct(leg.book_implied)).pack(side="left", padx=12)
            StatBlock(stats, "Fair (devig)", format_pct(leg.fair_implied)).pack(side="left", padx=12)
            StatBlock(
                stats, "Model prob", format_pct(leg.model_prob),
                value_color=T.edge_color(leg.edge),
            ).pack(side="left", padx=12)
            StatBlock(
                stats, "Edge",
                f"{leg.edge * 100:+.1f}%",
                value_color=T.edge_color(leg.edge),
            ).pack(side="left", padx=12)
            StatBlock(
                stats, "EV / $1", format_money(leg.ev_per_dollar),
                value_color=T.edge_color(leg.ev_per_dollar),
            ).pack(side="left", padx=12)

        # Adjustment breakdown (on hover / inline)
        if leg and leg.adjustments:
            adj_frame = ctk.CTkFrame(self, fg_color="transparent")
            adj_frame.pack(fill="x", padx=14, pady=(0, 10))
            for f in leg.adjustments:
                color = T.POSITIVE if f.weight > 0 else (T.NEGATIVE if f.weight < 0 else T.TEXT_MUTED)
                Pill(
                    adj_frame,
                    f"{f.name}: {f.weight:+.2f}",
                    color=T.BG_ELEV_3,
                    text_color=color,
                ).pack(side="left", padx=(0, 6))

        # Add to slip
        if leg is not None:
            btn = ctk.CTkButton(
                inner, text="Add to slip", width=110, height=34,
                fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color="#0b0f17",
                font=T.FONT_BOLD, corner_radius=8,
                command=lambda: on_add(leg),
            )
            btn.pack(side="right")
