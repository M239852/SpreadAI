"""Markets view — per-game line shop.

For each game on the board we show three stacked tables (Moneyline / Spread /
Total). Each table lists every bookmaker's price on both sides of the market
so the user can spot the best line at a glance.

The best price per selection is highlighted green and tagged with the book;
the consensus de-vigged fair probability is shown so you can see whether the
best price is actually +EV or just the prettiest of a bad lot.

Implementation notes
--------------------
* Line-shop math lives in `src.analysis.markets` — this file is all UI.
* Each market's book rows go into a ttk.Treeview to stay responsive when a
  slate has 20+ games × 3 markets × 10+ books.
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
from .widgets import Card, Pill, make_scroll
from .state import AppState


class MarketsView(ctk.CTkFrame):
    """Scrollable list of games, each with three market line-shop tables."""

    def __init__(
        self,
        master,
        state: AppState,
        on_add_leg: Callable[[LegAnalysis], None],
    ):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg

        self._install_tree_style()
        self._build_header()

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

    # ---------------- Styling ----------------

    def _install_tree_style(self):
        """Shared 'Markets.Treeview' style — tight, dark, matches the theme."""
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Markets.Treeview",
            background=T.BG_ELEV_2,
            fieldbackground=T.BG_ELEV_2,
            foreground=T.TEXT,
            rowheight=24,
            borderwidth=0,
            font=T.FONT_SMALL,
        )
        style.configure(
            "Markets.Treeview.Heading",
            background=T.BG_ELEV_3,
            foreground=T.TEXT_MUTED,
            relief="flat",
            font=T.FONT_TINY,
        )
        style.map(
            "Markets.Treeview",
            background=[("selected", T.ACCENT)],
            foreground=[("selected", T.BG)],
        )

    # ---------------- Header ----------------

    def _build_header(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(20, 6))

        col = ctk.CTkFrame(head, fg_color="transparent")
        col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(col, text="Markets", font=T.FONT_TITLE, text_color=T.TEXT).pack(anchor="w")
        self.subtitle = ctk.CTkLabel(
            col, text="Every book's line on every market, side by side.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        )
        self.subtitle.pack(anchor="w", pady=(2, 0))

        self.source_pill = Pill(head, "DEMO", color=T.BG_ELEV_3, text_color=T.TEXT_MUTED)
        self.source_pill.pack(side="right")

    def _on_state_event(self, event: str):
        if event in ("games", "sport"):
            self.render()

    # ---------------- Render ----------------

    def render(self):
        for child in self.list.winfo_children():
            child.destroy()

        games = self.state.games
        self.subtitle.configure(
            text=f"{len(games)} games  ·  {SPORT_LABELS.get(self.state.sport_key, self.state.sport_key)}"
        )
        self._render_source_banner()

        if not games:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
            return
        self.empty.place_forget()

        for g in games:
            GameMarketsCard(self.list, g, self.state, self.on_add_leg).pack(
                fill="x", pady=8, padx=8,
            )

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


class GameMarketsCard(Card):
    """One game; three stacked line-shop tables (ML / Spread / Total)."""

    def __init__(
        self,
        master,
        game: Game,
        state: AppState,
        on_add_leg: Callable[[LegAnalysis], None],
    ):
        super().__init__(master)
        self.game = game
        self.state = state
        self.on_add_leg = on_add_leg

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="x", padx=18, pady=16)

        # --- Game header ---
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text=f"{game.away_team}   @   {game.home_team}",
            font=T.FONT_HEAD, text_color=T.TEXT,
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text=f"{format_game_time(game.commence_time)}   ·   {len(game.bookmakers)} books",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        ).pack(side="left", padx=14)

        # --- Markets ---
        # Moneyline — one table, two selections (away / home)
        self._render_market_block(
            outer, "Moneyline",
            market_key="h2h",
            selections=[
                (game.away_team, game.away_team),
                (game.home_team, game.home_team),
            ],
            show_point=False,
        )
        # Spread
        self._render_market_block(
            outer, "Spread",
            market_key="spreads",
            selections=[
                (game.away_team, game.away_team),
                (game.home_team, game.home_team),
            ],
            show_point=True,
        )
        # Total
        self._render_market_block(
            outer, "Total",
            market_key="totals",
            selections=[
                ("Over", "Over"),
                ("Under", "Under"),
            ],
            show_point=True,
        )

    # ---------- One market block (heading + two line-shop tables) ----------

    def _render_market_block(
        self,
        parent,
        title: str,
        market_key: str,
        selections: list[tuple[str, str]],   # (display_label, selection_key)
        show_point: bool,
    ):
        wrap = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_2, corner_radius=10)
        wrap.pack(fill="x", pady=(14, 0))

        head = ctk.CTkFrame(wrap, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(head, text=title.upper(), font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")

        grid = ctk.CTkFrame(wrap, fg_color="transparent")
        grid.pack(fill="x", padx=10, pady=(6, 10))
        grid.grid_columnconfigure((0, 1), weight=1, uniform="sel")

        for col, (display, sel_key) in enumerate(selections):
            shop = line_shop_for(self.game, market_key, sel_key)
            self._render_selection_table(grid, col, shop, display, show_point)

    # ---------- One selection (e.g. "Home team -3.5") and its book quotes ----

    def _render_selection_table(
        self,
        parent,
        col: int,
        shop: LineShop,
        display: str,
        show_point: bool,
    ):
        frame = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_1, corner_radius=8)
        frame.grid(row=0, column=col, sticky="nsew", padx=4)

        # Row 1: selection name + best price pill + fair/edge
        sel_row = ctk.CTkFrame(frame, fg_color="transparent")
        sel_row.pack(fill="x", padx=12, pady=(10, 4))

        point = shop.quotes[0].point if (show_point and shop.quotes) else None
        if show_point and point is not None:
            if shop.market == "spreads":
                label_text = f"{display}  {point:+g}"
            else:
                label_text = f"{display}  {point}"
        else:
            label_text = display

        ctk.CTkLabel(
            sel_row, text=label_text, font=T.FONT_BOLD, text_color=T.TEXT,
        ).pack(side="left")

        # Best price + book pill on the right
        if shop.quotes:
            edge_color = T.POSITIVE if shop.best_edge >= 0.02 else (
                T.WARNING if shop.best_edge >= 0.0 else T.TEXT_MUTED
            )
            Pill(
                sel_row,
                f"best {format_american(shop.best_price)} · {shop.best_book}",
                color=T.BG_ELEV_3, text_color=edge_color,
            ).pack(side="right")
        else:
            Pill(sel_row, "no lines", color=T.BG_ELEV_3, text_color=T.TEXT_MUTED).pack(side="right")

        # Row 2: fair prob + edge
        meta = ctk.CTkFrame(frame, fg_color="transparent")
        meta.pack(fill="x", padx=12, pady=(0, 4))
        if shop.fair_prob > 0:
            fair_txt = f"fair {format_pct(shop.fair_prob, 1)}"
            edge_txt = f"edge {shop.best_edge*100:+.2f}%"
            edge_color = T.POSITIVE if shop.best_edge >= 0.02 else (
                T.NEGATIVE if shop.best_edge < 0 else T.TEXT_MUTED
            )
            ctk.CTkLabel(meta, text=fair_txt, font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")
            ctk.CTkLabel(meta, text="  ·  ", font=T.FONT_TINY, text_color=T.TEXT_DIM).pack(side="left")
            ctk.CTkLabel(meta, text=edge_txt, font=T.FONT_TINY, text_color=edge_color).pack(side="left")
        else:
            ctk.CTkLabel(meta, text="consensus unavailable", font=T.FONT_TINY, text_color=T.TEXT_DIM).pack(side="left")

        # Add-to-slip
        add_btn = ctk.CTkButton(
            meta, text="+ Add", width=60, height=22,
            corner_radius=6,
            fg_color=T.BG_ELEV_3, hover_color=T.ACCENT, text_color=T.TEXT,
            font=T.FONT_TINY,
            command=lambda: self._add_leg(shop.market, shop.selection),
        )
        add_btn.pack(side="right")

        # Row 3: per-book price table
        if not shop.quotes:
            return

        tbl_wrap = ctk.CTkFrame(frame, fg_color="transparent")
        tbl_wrap.pack(fill="x", padx=8, pady=(4, 10))

        cols = ("book", "point", "price") if show_point else ("book", "price")
        tree = ttk.Treeview(
            tbl_wrap, columns=cols, show="headings",
            style="Markets.Treeview", selectmode="none",
            height=min(len(shop.quotes), 8),
        )
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
            tag = "mid"
            if q.price == best_price:
                tag = "best"
            elif q.price == worst_price and len(shop.quotes) > 1:
                tag = "worst"
            if show_point:
                point_str = "" if q.point is None else (
                    f"{q.point:+g}" if shop.market == "spreads" else f"{q.point}"
                )
                values = (q.bookmaker_title, point_str, format_american(q.price))
            else:
                values = (q.bookmaker_title, format_american(q.price))
            tree.insert("", "end", values=values, tags=(tag,))

        tree.pack(fill="x")

    # ---------- Slip handoff ----------

    def _add_leg(self, market_key: str, selection: str):
        leg = build_leg_analysis(self.game, market_key, selection, factors=[])
        if leg is None:
            return
        self.on_add_leg(leg)
