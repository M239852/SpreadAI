"""Markets view — per-game line shop.

For each game on the board we show three stacked tables (Moneyline / Spread /
Total). Each table lists every bookmaker's price on both sides of the market
so the user can spot the best line at a glance. The best price is highlighted
and compared against the sharpness-weighted, de-vigged consensus.

Line-shop math lives in `src.analysis.markets` — this file is all UI.
"""
from __future__ import annotations
import customtkinter as ctk
from tkinter import ttk
from typing import Callable

from ..analysis.markets import LineShop, line_shop_for
from ..analysis.probability import build_leg_analysis, LegAnalysis
from ..api.odds_api import Game, SPORT_LABELS
from ..utils.formatters import format_american, format_pct, format_game_time
from . import theme as T
from .widgets import Card, Pill, PageHeader, EmptyState, GhostButton, make_scroll, install_treeview_styles
from .state import AppState
from .runtime import LazyRenderMixin, render_chunked


class MarketsView(LazyRenderMixin, ctk.CTkFrame):
    """Scrollable list of games, each with three market line-shop tables."""

    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        install_treeview_styles()

        self.header = PageHeader(self, "Markets", "Every book's line on every market, side by side.")
        self.header.pack(fill="x")

        self.list = make_scroll(self)
        self.list.pack(fill="both", expand=True, padx=T.SP_4, pady=(T.SP_2, T.SP_4))

        state.subscribe(self._on_state_event)

    def _on_state_event(self, event: str):
        if event in ("games", "sport"):
            self.request_render()

    def render(self):
        for child in self.list.winfo_children():
            child.destroy()

        games = self.state.games
        src = getattr(self.state, "games_source", "demo")
        self.header.set_source(src)
        self.header.set_subtitle(f"{len(games)} games  ·  {SPORT_LABELS.get(self.state.sport_key, self.state.sport_key)}"
                                 "  ·  best price highlighted, consensus is sharpness-weighted")
        if src == "live":
            self.header.hide_banner()
        else:
            self.header.show_banner("Showing demo odds. Add an Odds API key in Settings for live sportsbook lines.")

        if not games:
            EmptyState(self.list, "No games loaded", "Hit Refresh in the top bar to pull the slate.").pack(pady=T.SP_6 * 2)
            return
        render_chunked(self.list, list(games),
                       lambda g: GameMarketsCard(self.list, g, self.state, self.on_add_leg).pack(fill="x", pady=(0, T.SP_3), padx=2),
                       chunk=2)


class GameMarketsCard(Card):
    """One game; three stacked line-shop tables (ML / Spread / Total)."""

    def __init__(self, master, game: Game, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master)
        self.game = game
        self.state = state
        self.on_add_leg = on_add_leg
        outer = self.body(padx=T.SP_4, pady=T.SP_3)

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text=f"{game.away_team}  @  {game.home_team}", font=T.FONT_HEAD, text_color=T.TEXT).pack(side="left")
        ctk.CTkLabel(header, text=f"{format_game_time(game.commence_time)}   ·   {len(game.bookmakers)} books",
                     font=T.FONT_SMALL, text_color=T.TEXT_MUTED).pack(side="left", padx=T.SP_4)

        self._block(outer, "Moneyline", "h2h", [(game.away_team, game.away_team), (game.home_team, game.home_team)], False)
        self._block(outer, "Spread", "spreads", [(game.away_team, game.away_team), (game.home_team, game.home_team)], True)
        self._block(outer, "Total", "totals", [("Over", "Over"), ("Under", "Under")], True)

    def _block(self, parent, title: str, market_key: str, selections: list[tuple[str, str]], show_point: bool):
        wrap = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD)
        wrap.pack(fill="x", pady=(T.SP_3, 0))
        ctk.CTkLabel(wrap, text=title.upper(), font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(anchor="w", padx=T.SP_4, pady=(T.SP_2, 0))
        grid = ctk.CTkFrame(wrap, fg_color="transparent")
        grid.pack(fill="x", padx=T.SP_2, pady=(T.SP_1, T.SP_2))
        grid.grid_columnconfigure((0, 1), weight=1, uniform="sel")
        for col, (display, sel_key) in enumerate(selections):
            shop = line_shop_for(self.game, market_key, sel_key)
            self._selection_table(grid, col, shop, display, show_point)

    def _selection_table(self, parent, col: int, shop: LineShop, display: str, show_point: bool):
        frame = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_1, corner_radius=T.R_SM)
        frame.grid(row=0, column=col, sticky="nsew", padx=4)

        point = shop.quotes[0].point if (show_point and shop.quotes) else None
        if show_point and point is not None:
            label_text = f"{display}  {point:+g}" if shop.market == "spreads" else f"{display}  {point:g}"
        else:
            label_text = display

        sel_row = ctk.CTkFrame(frame, fg_color="transparent")
        sel_row.pack(fill="x", padx=T.SP_3, pady=(T.SP_2, 2))
        ctk.CTkLabel(sel_row, text=label_text, font=T.FONT_BOLD, text_color=T.TEXT).pack(side="left")
        if shop.quotes:
            variant = "positive" if shop.best_edge >= 0.02 else ("warning" if shop.best_edge >= 0.0 else "neutral")
            Pill(sel_row, f"best {format_american(shop.best_price)} · {shop.best_book}", variant=variant).pack(side="right")
        else:
            Pill(sel_row, "no lines", variant="neutral").pack(side="right")

        meta = ctk.CTkFrame(frame, fg_color="transparent")
        meta.pack(fill="x", padx=T.SP_3, pady=(0, 2))
        if shop.fair_prob > 0:
            ctk.CTkLabel(meta, text=f"consensus {format_pct(shop.fair_prob, 1)}", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")
            ctk.CTkLabel(meta, text="  ·  ", font=T.FONT_TINY, text_color=T.TEXT_DIM).pack(side="left")
            edge_color = T.POSITIVE if shop.best_edge >= 0.02 else (T.NEGATIVE if shop.best_edge < 0 else T.TEXT_MUTED)
            ctk.CTkLabel(meta, text=f"edge {shop.best_edge*100:+.2f}%", font=T.FONT_TINY, text_color=edge_color).pack(side="left")
        else:
            ctk.CTkLabel(meta, text="consensus unavailable", font=T.FONT_TINY, text_color=T.TEXT_DIM).pack(side="left")
        GhostButton(meta, "+ Add", command=lambda: self._add_leg(shop.market, shop.selection),
                    width=58, height=22, font=T.FONT_TINY, hover_color=T.ACCENT).pack(side="right")

        if not shop.quotes:
            return
        tbl_wrap = ctk.CTkFrame(frame, fg_color="transparent")
        tbl_wrap.pack(fill="x", padx=T.SP_2, pady=(2, T.SP_2))
        cols = ("book", "point", "price") if show_point else ("book", "price")
        tree = ttk.Treeview(tbl_wrap, columns=cols, show="headings", style="Markets.Treeview",
                            selectmode="none", height=min(len(shop.quotes), 8))
        tree.heading("book", text="Book")
        tree.column("book", width=140, anchor="w", stretch=True)
        if show_point:
            tree.heading("point", text="Point")
            tree.column("point", width=60, anchor="e")
        tree.heading("price", text="Price")
        tree.column("price", width=80, anchor="e")
        tree.tag_configure("best", foreground=T.POSITIVE, font=T.FONT_BOLD)
        tree.tag_configure("mid", foreground=T.TEXT)
        tree.tag_configure("worst", foreground=T.TEXT_MUTED)

        best_price = shop.quotes[0].price
        worst_price = shop.quotes[-1].price
        for q in shop.quotes:
            tag = "best" if q.price == best_price else ("worst" if (q.price == worst_price and len(shop.quotes) > 1) else "mid")
            if show_point:
                point_str = "" if q.point is None else (f"{q.point:+g}" if shop.market == "spreads" else f"{q.point:g}")
                values = (q.bookmaker_title, point_str, format_american(q.price))
            else:
                values = (q.bookmaker_title, format_american(q.price))
            tree.insert("", "end", values=values, tags=(tag,))
        tree.pack(fill="x")

    def _add_leg(self, market_key: str, selection: str):
        leg = build_leg_analysis(self.game, market_key, selection, factors=[])
        if leg is not None:
            self.on_add_leg(leg)
