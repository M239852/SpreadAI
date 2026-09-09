"""Markets view — per-game line shop.

For each game we show three stacked market panels (Moneyline / Spread /
Total). Each panel lists every bookmaker's price on both sides so the best
line is obvious at a glance, compared against the sharpness-weighted,
de-vigged consensus.

Like the Board, cards are pooled and rebound rather than rebuilt, and only
the rounded shells stay on CustomTkinter — the rest is `fastwidgets`. The
per-book price tables are `ttk.Treeview`, which is C-backed and refills
cheaply. Line-shop math lives in `src.analysis.markets`; this file is all UI.
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
from . import fastwidgets as fw
from .widgets import Card, PageHeader, EmptyState, make_scroll, install_treeview_styles
from .state import AppState
from .runtime import LazyRenderMixin, render_chunked


# (title, market_key, show_point)
_BLOCKS = (("Moneyline", "h2h", False), ("Spread", "spreads", True), ("Total", "totals", True))


class MarketsView(LazyRenderMixin, ctk.CTkFrame):
    """Scrollable list of games, each with three market line-shop tables."""

    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        self._pool: list["GameMarketsCard"] = []
        self._empty: EmptyState | None = None
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
        games = list(self.state.games)
        src = getattr(self.state, "games_source", "demo")
        self.header.set_source(src)
        self.header.set_subtitle(
            f"{len(games)} games  ·  {SPORT_LABELS.get(self.state.sport_key, self.state.sport_key)}"
            "  ·  best price highlighted, consensus is sharpness-weighted")
        if src == "live":
            self.header.hide_banner()
        else:
            self.header.show_banner("Showing demo odds. Add an Odds API key in Settings for live sportsbook lines.")

        if self._empty is not None:
            self._empty.destroy()
            self._empty = None

        if not games:
            for card in self._pool:
                card.pack_forget()
            self._empty = EmptyState(self.list, "No games loaded", "Hit Refresh in the top bar to pull the slate.")
            self._empty.pack(pady=T.SP_6 * 2)
            return

        for card in self._pool[len(games):]:
            card.pack_forget()

        reuse = min(len(self._pool), len(games))
        for i in range(reuse):
            card = self._pool[i]
            card.bind_game(games[i])
            if not card.winfo_manager():
                card.pack(fill="x", pady=(0, T.SP_3), padx=2)

        pending = games[reuse:]
        if pending:
            def build(game):
                card = GameMarketsCard(self.list, self.state, self.on_add_leg)
                self._pool.append(card)
                card.bind_game(game)
                card.pack(fill="x", pady=(0, T.SP_3), padx=2)
            render_chunked(self.list, pending, build, chunk=2)


class _SelectionPanel:
    """One side of one market: header, consensus line, and the per-book table."""

    __slots__ = ("frame", "title", "best", "consensus", "edge", "add", "tree", "market", "selection", "_on_add")

    def __init__(self, parent, market_key: str, show_point: bool, on_add):
        bg = T.BG_ELEV_1
        self._on_add = on_add
        self.market = market_key
        self.selection = ""
        self.frame = ctk.CTkFrame(parent, fg_color=bg, corner_radius=T.R_SM)

        sel_row = fw.frame(self.frame, bg=bg)
        sel_row.pack(fill="x", padx=T.SP_3, pady=(T.SP_2, 2))
        # Right-hand pill first — see the note in games_view.GameCard.
        self.best = fw.Pill(sel_row, "", bg=bg, variant="neutral", font=T.FONT_TINY, height=18)
        self.best.pack(side="right")
        self.title = fw.label(sel_row, "", bg=bg, fg=T.TEXT, font=T.FONT_BOLD)
        self.title.pack(side="left", fill="x", expand=True)

        meta = fw.frame(self.frame, bg=bg)
        meta.pack(fill="x", padx=T.SP_3, pady=(0, 2))
        self.consensus = fw.label(meta, "", bg=bg, fg=T.TEXT_MUTED, font=T.FONT_TINY)
        self.consensus.pack(side="left")
        self.edge = fw.label(meta, "", bg=bg, fg=T.TEXT_MUTED, font=T.FONT_TINY)
        self.edge.pack(side="left", padx=(6, 0))
        self.add = fw.Button(meta, "+ Add", self._add, bg=bg, fill=T.BG_ELEV_3, hover=T.ACCENT,
                             font=T.FONT_TINY, width=58, height=22)
        self.add.pack(side="right")

        cols = ("book", "point", "price") if show_point else ("book", "price")
        self.tree = ttk.Treeview(self.frame, columns=cols, show="headings", style="Markets.Treeview",
                                 selectmode="none", height=4)
        self.tree.heading("book", text="Book")
        self.tree.column("book", width=140, anchor="w", stretch=True)
        if show_point:
            self.tree.heading("point", text="Point")
            self.tree.column("point", width=60, anchor="e")
        self.tree.heading("price", text="Price")
        self.tree.column("price", width=80, anchor="e")
        self.tree.tag_configure("best", foreground=T.POSITIVE, font=T.FONT_BOLD)
        self.tree.tag_configure("mid", foreground=T.TEXT)
        self.tree.tag_configure("worst", foreground=T.TEXT_MUTED)
        self.tree.pack(fill="x", padx=T.SP_2, pady=(2, T.SP_2))

    def _add(self):
        if self.selection:
            self._on_add(self.market, self.selection)

    def bind(self, shop: LineShop, display: str, show_point: bool):
        self.selection = shop.selection
        point = shop.quotes[0].point if (show_point and shop.quotes) else None
        if show_point and point is not None:
            self.title.configure(text=f"{display}  {point:+g}" if shop.market == "spreads" else f"{display}  {point:g}")
        else:
            self.title.configure(text=display)

        if shop.quotes:
            variant = "positive" if shop.best_edge >= 0.02 else ("warning" if shop.best_edge >= 0.0 else "neutral")
            self.best.set(f"best {format_american(shop.best_price)} · {shop.best_book}", variant=variant)
            self.add.configure_state("normal")
        else:
            self.best.set("no lines", variant="neutral")
            self.add.configure_state("disabled")

        if shop.fair_prob > 0:
            self.consensus.configure(text=f"consensus {format_pct(shop.fair_prob, 1)}", fg=T.TEXT_MUTED)
            color = T.POSITIVE if shop.best_edge >= 0.02 else (T.NEGATIVE if shop.best_edge < 0 else T.TEXT_MUTED)
            self.edge.configure(text=f"·  edge {shop.best_edge*100:+.2f}%", fg=color)
        else:
            self.consensus.configure(text="consensus unavailable", fg=T.TEXT_DIM)
            self.edge.configure(text="")

        self.tree.delete(*self.tree.get_children())
        if not shop.quotes:
            self.tree.configure(height=1)
            return
        best_price = shop.quotes[0].price
        worst_price = shop.quotes[-1].price
        for q in shop.quotes[:8]:
            tag = "best" if q.price == best_price else (
                "worst" if (q.price == worst_price and len(shop.quotes) > 1) else "mid")
            if show_point:
                point_str = "" if q.point is None else (
                    f"{q.point:+g}" if shop.market == "spreads" else f"{q.point:g}")
                values = (q.bookmaker_title, point_str, format_american(q.price))
            else:
                values = (q.bookmaker_title, format_american(q.price))
            self.tree.insert("", "end", values=values, tags=(tag,))
        self.tree.configure(height=min(len(shop.quotes), 8))


class GameMarketsCard(Card):
    """One game; three stacked line-shop panels. Built once, rebound per game."""

    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master)
        self.state = state
        self.on_add_leg = on_add_leg
        self._game: Game | None = None
        bg = T.BG_ELEV_1

        outer = fw.frame(self, bg=bg)
        outer.pack(fill="x", padx=T.SP_4, pady=T.SP_3)
        header = fw.frame(outer, bg=bg)
        header.pack(fill="x")
        self.matchup = fw.label(header, "", bg=bg, fg=T.TEXT, font=T.FONT_HEAD)
        self.matchup.pack(side="left")
        self.meta = fw.label(header, "", bg=bg, fg=T.TEXT_MUTED, font=T.FONT_SMALL)
        self.meta.pack(side="left", padx=T.SP_4)

        self.panels: list[_SelectionPanel] = []
        for title, market_key, show_point in _BLOCKS:
            wrap = ctk.CTkFrame(outer, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD)
            wrap.pack(fill="x", pady=(T.SP_3, 0))
            fw.label(wrap, title.upper(), bg=T.BG_ELEV_2, fg=T.TEXT_MUTED,
                     font=T.FONT_LABEL).pack(anchor="w", padx=T.SP_4, pady=(T.SP_2, 0))
            grid = fw.frame(wrap, bg=T.BG_ELEV_2)
            grid.pack(fill="x", padx=T.SP_2, pady=(T.SP_1, T.SP_2))
            grid.grid_columnconfigure((0, 1), weight=1, uniform="sel")
            for col in range(2):
                panel = _SelectionPanel(grid, market_key, show_point, self._add_leg)
                panel.frame.grid(row=0, column=col, sticky="nsew", padx=4)
                self.panels.append(panel)

    def bind_game(self, game: Game):
        self._game = game
        self.matchup.configure(text=f"{game.away_team}  @  {game.home_team}")
        self.meta.configure(text=f"{format_game_time(game.commence_time)}   ·   {len(game.bookmakers)} books")
        sides = (
            (game.away_team, game.away_team), (game.home_team, game.home_team),
            (game.away_team, game.away_team), (game.home_team, game.home_team),
            ("Over", "Over"), ("Under", "Under"),
        )
        i = 0
        for _title, market_key, show_point in _BLOCKS:
            for _ in range(2):
                display, sel_key = sides[i]
                self.panels[i].bind(line_shop_for(game, market_key, sel_key), display, show_point)
                i += 1

    def _add_leg(self, market_key: str, selection: str):
        if self._game is None:
            return
        leg = build_leg_analysis(self._game, market_key, selection, factors=[])
        if leg is not None:
            self.on_add_leg(leg)
