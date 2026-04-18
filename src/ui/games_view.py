from __future__ import annotations
import customtkinter as ctk
from typing import Callable

from ..api.odds_api import Game
from ..analysis.probability import (
    best_price, compute_fair_prob_from_game, build_leg_analysis, LegAnalysis,
)
from ..utils.formatters import format_american, format_pct, format_game_time
from . import theme as T
from .widgets import Card, Pill, make_scroll
from .state import AppState


class GamesView(ctk.CTkFrame):
    """Scrollable list of games with inline odds and quick add-to-slip buttons."""

    def __init__(
        self,
        master,
        state: AppState,
        on_add_leg: Callable[[LegAnalysis], None],
        on_view_analysis: Callable[[str], None],
    ):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        self.on_view_analysis = on_view_analysis

        self.header = ctk.CTkFrame(self, fg_color="transparent", height=40)
        self.header.pack(fill="x", padx=24, pady=(20, 8))
        self.title = ctk.CTkLabel(self.header, text="Today's Board", font=T.FONT_TITLE, text_color=T.TEXT)
        self.title.pack(side="left")
        self.subtitle = ctk.CTkLabel(self.header, text="", font=T.FONT_SMALL, text_color=T.TEXT_MUTED)
        self.subtitle.pack(side="left", padx=12)
        self.source_pill = Pill(self.header, "DEMO", color=T.BG_ELEV_3, text_color=T.TEXT_MUTED)
        self.source_pill.pack(side="right")

        self.banner = ctk.CTkLabel(
            self, text="", font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
            fg_color=T.BG_ELEV_2, corner_radius=8, anchor="w", height=34,
        )

        self.empty = ctk.CTkLabel(
            self,
            text="No games loaded. Click Refresh in the sidebar.",
            font=T.FONT,
            text_color=T.TEXT_MUTED,
        )

        self.list = make_scroll(self)
        self.list.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        state.subscribe(self._on_state_event)

    def _on_state_event(self, event: str):
        if event in ("games", "sport"):
            self.render()

    def render(self):
        for child in self.list.winfo_children():
            child.destroy()

        games = self.state.games
        self.subtitle.configure(text=f"{len(games)} games  ·  {self._sport_label()}")
        self._render_source_banner()

        if not games:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
            return
        self.empty.place_forget()

        for g in games:
            GameCard(self.list, g, self.state, self.on_add_leg, self.on_view_analysis).pack(
                fill="x", pady=8, padx=8
            )

    def _sport_label(self) -> str:
        from ..api.odds_api import SPORT_LABELS
        return SPORT_LABELS.get(self.state.sport_key, self.state.sport_key)

    def _render_source_banner(self):
        src = getattr(self.state, "games_source", "demo")
        if src == "live":
            self.source_pill.configure(text="LIVE", text_color=T.POSITIVE)
            self.banner.pack_forget()
        else:
            self.source_pill.configure(text="DEMO", text_color=T.TEXT_MUTED)
            self.banner.configure(
                text="  Showing demo odds. Add an Odds API key in Settings for live sportsbook lines.",
            )
            self.banner.pack(fill="x", padx=24, pady=(0, 8))


class GameCard(Card):
    def __init__(
        self,
        master,
        game: Game,
        state: AppState,
        on_add_leg: Callable[[LegAnalysis], None],
        on_view_analysis: Callable[[str], None],
    ):
        super().__init__(master)
        self.game = game
        self.state = state
        self.on_add_leg = on_add_leg
        self.on_view_analysis = on_view_analysis

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="x", padx=18, pady=16)

        # --- Header row ---
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x")

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)

        matchup = ctk.CTkLabel(
            left,
            text=f"{game.away_team}   @   {game.home_team}",
            font=T.FONT_HEAD,
            text_color=T.TEXT,
        )
        matchup.pack(anchor="w")

        meta = ctk.CTkLabel(
            left,
            text=f"{game.sport_title}   ·   {format_game_time(game.commence_time)}"
                 f"   ·   {len(game.bookmakers)} books",
            font=T.FONT_SMALL,
            text_color=T.TEXT_MUTED,
        )
        meta.pack(anchor="w", pady=(2, 0))

        btn = ctk.CTkButton(
            header,
            text="View Analysis →",
            width=140,
            height=32,
            fg_color=T.BG_ELEV_3,
            hover_color=T.BORDER,
            text_color=T.ACCENT,
            font=T.FONT_BOLD,
            command=lambda: self.on_view_analysis(game.id),
        )
        btn.pack(side="right")

        # --- Markets ---
        markets = ctk.CTkFrame(outer, fg_color="transparent")
        markets.pack(fill="x", pady=(14, 0))
        markets.grid_columnconfigure((0, 1, 2), weight=1, uniform="m")

        # ML (h2h)
        self._build_market_cell(
            markets, 0, "MONEYLINE",
            [
                (game.away_team, "h2h", game.away_team, None),
                (game.home_team, "h2h", game.home_team, None),
            ],
        )
        # Spread
        self._build_market_cell(
            markets, 1, "SPREAD",
            [
                (game.away_team, "spreads", game.away_team, None),
                (game.home_team, "spreads", game.home_team, None),
            ],
        )
        # Total
        self._build_market_cell(
            markets, 2, "TOTAL",
            [
                ("Over", "totals", "Over", None),
                ("Under", "totals", "Under", None),
            ],
        )

    def _build_market_cell(self, parent, col: int, title: str, rows: list[tuple[str, str, str, float | None]]):
        cell = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_2, corner_radius=10)
        cell.grid(row=0, column=col, sticky="nsew", padx=4)

        lbl = ctk.CTkLabel(cell, text=title, font=T.FONT_TINY, text_color=T.TEXT_MUTED)
        lbl.pack(anchor="w", padx=12, pady=(10, 4))

        for display_name, market_key, selection, _ in rows:
            row_frame = ctk.CTkFrame(cell, fg_color="transparent")
            row_frame.pack(fill="x", padx=8, pady=4)

            bp = best_price(self.game, market_key, selection)
            fair = compute_fair_prob_from_game(self.game, market_key, selection)
            if bp is None:
                ctk.CTkLabel(
                    row_frame, text=f"{display_name}", font=T.FONT_SMALL,
                    text_color=T.TEXT_DIM,
                ).pack(side="left", padx=6)
                ctk.CTkLabel(
                    row_frame, text="—", font=T.FONT_BOLD, text_color=T.TEXT_DIM,
                ).pack(side="right", padx=6)
                continue

            price, bk, point = bp

            # Build label text
            if market_key == "spreads" and point is not None:
                label_text = f"{display_name} {point:+g}"
            elif market_key == "totals" and point is not None:
                label_text = f"{display_name} {point}"
            else:
                label_text = display_name

            name = ctk.CTkLabel(
                row_frame, text=label_text, font=T.FONT_SMALL,
                text_color=T.TEXT, anchor="w",
            )
            name.pack(side="left", padx=6, fill="x", expand=True)

            if fair > 0:
                fair_lbl = ctk.CTkLabel(
                    row_frame, text=format_pct(fair, 0),
                    font=T.FONT_TINY, text_color=T.TEXT_MUTED,
                )
                fair_lbl.pack(side="right", padx=(4, 6))

            price_lbl = ctk.CTkLabel(
                row_frame, text=format_american(price),
                font=T.FONT_BOLD, text_color=T.TEXT,
            )
            price_lbl.pack(side="right", padx=4)

            add_btn = ctk.CTkButton(
                row_frame, text="+", width=26, height=26,
                corner_radius=6,
                fg_color=T.BG_ELEV_3, hover_color=T.ACCENT,
                text_color=T.TEXT, font=T.FONT_BOLD,
                command=lambda mk=market_key, sel=selection: self._add_leg(mk, sel),
            )
            add_btn.pack(side="right", padx=4)

            # bookmaker pill
            Pill(row_frame, bk, color=T.BG_ELEV_3, text_color=T.TEXT_MUTED).pack(
                side="right", padx=(4, 6)
            )

    def _add_leg(self, market_key: str, selection: str):
        leg = build_leg_analysis(self.game, market_key, selection, factors=[])
        if leg is None:
            return
        self.on_add_leg(leg)
