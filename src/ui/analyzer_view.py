"""Odds Analyzer — cross-book scanners (+EV / Arbitrage / Best Lines).

+EV        — selections whose best available price beats the consensus fair
             price by at least `min_edge`.
Arbitrage  — two-way markets where the best price on each side sums to
             <100% implied probability, with the stake split for equal return.
Best Lines — biggest price gap between the best and worst book for a
             single selection (pure line-shop wins).
"""
from __future__ import annotations
import customtkinter as ctk
from typing import Callable

from ..analysis.markets import (
    positive_ev_picks, arb_opportunities, biggest_line_spreads,
    EdgePick, ArbOpportunity, BestLineRow,
)
from ..analysis.probability import build_leg_analysis, LegAnalysis
from ..utils.formatters import format_american, format_pct, format_money
from . import theme as T
from .widgets import PageHeader, Toolbar, Segmented, GhostButton, entry, slider, make_tree
from .state import AppState
from .runtime import LazyRenderMixin


DEFAULT_MIN_EDGE = 0.02
DEFAULT_MIN_ARB_PROFIT = 0.005
MAX_ROWS = 400


class AnalyzerView(LazyRenderMixin, ctk.CTkFrame):
    """Tabbed cross-book scanner."""

    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        self._min_edge = DEFAULT_MIN_EDGE
        self._min_arb = DEFAULT_MIN_ARB_PROFIT
        self._stake = 100.0

        self.header = PageHeader(self, "Odds Analyzer",
                                 "Cross-book scanner — +EV, arbitrage, and the best line-shop wins on the slate.")
        self.header.pack(fill="x")
        self._build_toolbar()
        self._build_tabs()
        state.subscribe(self._on_state_event)

    # ---------------------------------------------------------------- toolbar

    def _build_toolbar(self):
        bar = Toolbar(self)
        bar.pack(fill="x", padx=T.SP_4, pady=(0, T.SP_2))
        self.count_lbl = ctk.CTkLabel(bar.inner, text="0 games scanned", font=T.FONT_SMALL, text_color=T.TEXT_MUTED)
        self.count_lbl.pack(side="left", padx=(0, T.SP_4))

        bar.label("Min edge")
        self.edge_lbl = ctk.CTkLabel(bar.inner, text="2.0%", font=T.FONT_MONO_SMALL, text_color=T.ACCENT, width=44)
        self.edge_lbl.pack(side="left")
        s = slider(bar.inner, 0.0, 0.10, 20, command=self._on_edge_change, width=100)
        s.set(DEFAULT_MIN_EDGE)
        s.pack(side="left", padx=(4, T.SP_4))

        bar.label("Min arb")
        self.arb_lbl = ctk.CTkLabel(bar.inner, text="0.50%", font=T.FONT_MONO_SMALL, text_color=T.ACCENT, width=48)
        self.arb_lbl.pack(side="left")
        s2 = slider(bar.inner, 0.0, 0.05, 25, command=self._on_arb_change, width=100)
        s2.set(DEFAULT_MIN_ARB_PROFIT)
        s2.pack(side="left", padx=(4, T.SP_4))

        bar.label("Stake $")
        self.stake_var = ctk.StringVar(value="100")
        entry(bar.inner, self.stake_var, width=64, justify="right", height=26).pack(side="left")
        self.stake_var.trace_add("write", lambda *_: self._on_stake_change())

        GhostButton(self.header.actions, "↻ Rescan", command=self._rerender_all, width=96, height=28).pack(side="right", padx=(0, T.SP_2))

    # ---------------------------------------------------------------- tabs

    def _build_tabs(self):
        self._tab_frames: dict[str, ctk.CTkFrame] = {}
        seg_row = ctk.CTkFrame(self, fg_color="transparent")
        seg_row.pack(fill="x", padx=T.SP_4, pady=(0, T.SP_2))
        self.tabs_seg = Segmented(seg_row, [("ev", "+EV picks"), ("arb", "Arbitrage"), ("spread", "Best lines")],
                                  value="ev", command=self._show_tab, height=28)
        self.tabs_seg.pack(side="left")
        self.tab_hint = ctk.CTkLabel(seg_row, text="", font=T.FONT_TINY, text_color=T.TEXT_DIM)
        self.tab_hint.pack(side="left", padx=T.SP_3)

        self.tab_host = ctk.CTkFrame(self, fg_color="transparent")
        self.tab_host.pack(fill="both", expand=True, padx=T.SP_4, pady=(0, T.SP_4))
        self.tab_host.grid_rowconfigure(0, weight=1)
        self.tab_host.grid_columnconfigure(0, weight=1)

        for key, builder in (("ev", self._build_ev_tab), ("arb", self._build_arb_tab), ("spread", self._build_spread_tab)):
            f = ctk.CTkFrame(self.tab_host, fg_color="transparent")
            f.grid(row=0, column=0, sticky="nsew")
            builder(f)
            self._tab_frames[key] = f
        self._show_tab("ev")

    def _show_tab(self, key: str):
        for k, f in self._tab_frames.items():
            if k == key:
                f.tkraise()
        self.tab_hint.configure(text={
            "ev": "best price beats the sharpness-weighted consensus by ≥ min edge",
            "arb": "opposite sides at two books summing to < 100% implied — both bets must be placed",
            "spread": "largest price gap between the best and worst book on one selection",
        }.get(key, ""))

    def _detail_bar(self, parent, text: str, add_text: str | None, add_cmd) -> tuple[ctk.CTkLabel, ctk.CTkButton | None]:
        bar = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_1, corner_radius=T.R_MD)
        bar.pack(fill="x", pady=(T.SP_2, 0))
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=T.SP_3, pady=T.SP_2)
        lbl = ctk.CTkLabel(inner, text=text, font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w", justify="left")
        lbl.pack(side="left", fill="x", expand=True)
        btn = None
        if add_text:
            btn = GhostButton(inner, add_text, command=add_cmd, width=130, height=30, state="disabled", hover_color=T.ACCENT)
            btn.pack(side="right")
        return lbl, btn

    def _build_ev_tab(self, parent):
        self.ev_tree, wrap = make_tree(parent, {
            "matchup": ("Matchup", 200, "w"), "market": ("Market", 80, "w"), "selection": ("Selection", 150, "w"),
            "book": ("Book", 100, "w"), "price": ("Price", 64, "e"), "fair": ("Consensus", 84, "e"),
            "edge": ("Edge", 72, "e"), "ev": ("EV / $1", 72, "e"),
        }, stretch_col="matchup")
        wrap.pack(fill="both", expand=True)
        self.ev_tree.bind("<<TreeviewSelect>>", self._on_ev_select)
        self.ev_tree.bind("<Double-1>", lambda e: self._add_selected_ev())
        self.ev_detail, self.ev_add_btn = self._detail_bar(parent, "Select a row — double-click or use Add to push it to the slip.",
                                                           "+ Add to slip", self._add_selected_ev)
        self._ev_picks: list[EdgePick] = []
        self._ev_selected: EdgePick | None = None

    def _build_arb_tab(self, parent):
        self.arb_tree, wrap = make_tree(parent, {
            "matchup": ("Matchup", 180, "w"), "market": ("Market", 70, "w"), "side_a": ("Side A", 190, "w"),
            "side_b": ("Side B", 190, "w"), "sum": ("Imp sum", 66, "e"), "profit": ("Profit %", 70, "e"),
            "stake_a": ("Stake A", 70, "e"), "stake_b": ("Stake B", 70, "e"), "net": ("Net $", 64, "e"),
        }, stretch_col="side_a")
        wrap.pack(fill="both", expand=True)
        self.arb_tree.bind("<<TreeviewSelect>>", self._on_arb_select)
        self.arb_detail, _ = self._detail_bar(parent, "Arb opportunities require placing both sides on the listed books simultaneously.", None, None)
        self._arbs: list[ArbOpportunity] = []

    def _build_spread_tab(self, parent):
        self.spread_tree, wrap = make_tree(parent, {
            "matchup": ("Matchup", 200, "w"), "market": ("Market", 80, "w"), "selection": ("Selection", 150, "w"),
            "best_book": ("Best book", 100, "w"), "best_price": ("Best", 64, "e"), "worst_book": ("Worst book", 100, "w"),
            "worst_price": ("Worst", 64, "e"), "spread": ("Δ pts", 60, "e"),
        }, stretch_col="matchup")
        wrap.pack(fill="both", expand=True)
        self.spread_tree.bind("<<TreeviewSelect>>", self._on_spread_select)
        self.spread_tree.bind("<Double-1>", lambda e: self._add_selected_spread())
        self.spread_detail, self.spread_add_btn = self._detail_bar(parent, "Biggest price spreads across books — bet the best line or shop your existing ones.",
                                                                   "+ Add best line", self._add_selected_spread)
        self._spreads: list[BestLineRow] = []
        self._spread_selected: BestLineRow | None = None

    # ---------------------------------------------------------------- state

    def _on_state_event(self, event: str):
        if event in ("games", "sport"):
            self.request_render()

    def render(self):
        self._rerender_all()

    def _on_edge_change(self, value):
        self._min_edge = float(value)
        self.edge_lbl.configure(text=f"{self._min_edge*100:.1f}%")
        self._rerender_ev()

    def _on_arb_change(self, value):
        self._min_arb = float(value)
        self.arb_lbl.configure(text=f"{self._min_arb*100:.2f}%")
        self._rerender_arb()

    def _on_stake_change(self):
        try:
            self._stake = float(self.stake_var.get() or "0")
        except ValueError:
            self._stake = 0.0
        self._rerender_arb()

    # ---------------------------------------------------------------- render

    def _rerender_all(self):
        games = self.state.games
        self.count_lbl.configure(text=f"{len(games)} games scanned")
        self.header.set_source(getattr(self.state, "games_source", "demo"))
        self._rerender_ev()
        self._rerender_arb()
        self._rerender_spread()

    def _rerender_ev(self):
        self.ev_tree.delete(*self.ev_tree.get_children())
        self._ev_picks = positive_ev_picks(self.state.games, min_edge=self._min_edge)[:MAX_ROWS]
        for i, p in enumerate(self._ev_picks):
            shop = p.shop
            point = shop.quotes[0].point if shop.quotes else None
            self.ev_tree.insert("", "end", iid=str(i), values=(
                shop.matchup, _market_label(shop.market), _sel_label(shop.market, shop.selection, point),
                shop.best_book, format_american(shop.best_price), format_pct(shop.fair_prob, 1),
                f"{p.edge*100:+.2f}%", f"{p.ev_per_dollar:+.3f}",
            ), tags=("hot" if p.edge >= 0.04 else "warm",))
        self.tabs_seg._buttons["ev"].configure(text=f"+EV picks ({len(self._ev_picks)})")

    def _rerender_arb(self):
        self.arb_tree.delete(*self.arb_tree.get_children())
        self._arbs = arb_opportunities(self.state.games, min_profit=self._min_arb)[:MAX_ROWS]
        stake = max(self._stake, 0.0)
        for i, o in enumerate(self._arbs):
            side_a = f"{_sel_label(o.market, o.selection_a, o.point_a)} @ {o.book_a_title} {format_american(o.price_a)}"
            side_b = f"{_sel_label(o.market, o.selection_b, o.point_b)} @ {o.book_b_title} {format_american(o.price_b)}"
            self.arb_tree.insert("", "end", iid=str(i), values=(
                o.matchup, _market_label(o.market), side_a, side_b, f"{o.implied_sum*100:.2f}%",
                f"+{o.profit_pct*100:.2f}%", format_money(stake * o.stake_a_pct, 2),
                format_money(stake * o.stake_b_pct, 2), format_money(stake * o.profit_pct, 2),
            ), tags=("hot" if o.profit_pct >= 0.015 else "warm",))
        self.tabs_seg._buttons["arb"].configure(text=f"Arbitrage ({len(self._arbs)})")

    def _rerender_spread(self):
        self.spread_tree.delete(*self.spread_tree.get_children())
        self._spreads = biggest_line_spreads(self.state.games, limit=MAX_ROWS)
        for i, r in enumerate(self._spreads):
            shop = r.shop
            point = shop.quotes[0].point if shop.quotes else None
            self.spread_tree.insert("", "end", iid=str(i), values=(
                shop.matchup, _market_label(shop.market), _sel_label(shop.market, shop.selection, point),
                shop.best_book, format_american(shop.best_price), r.worst_book, format_american(r.worst_price),
                str(r.price_spread),
            ), tags=("hot" if r.price_spread >= 25 else "warm",))

    # ---------------------------------------------------------------- selection

    def _on_ev_select(self, _evt=None):
        sel = self.ev_tree.selection()
        if not sel:
            self._ev_selected = None
            self.ev_add_btn.configure(state="disabled")
            return
        idx = int(sel[0])
        if idx >= len(self._ev_picks):
            return
        p = self._ev_picks[idx]
        self._ev_selected = p
        self.ev_detail.configure(text=f"{p.summary}  ·  consensus {format_pct(p.shop.fair_prob, 1)} / edge {p.edge*100:+.2f}%",
                                 text_color=T.TEXT)
        self.ev_add_btn.configure(state="normal")

    def _add_selected_ev(self):
        if not self._ev_selected:
            return
        shop = self._ev_selected.shop
        game = self.state.find_game(shop.game_id)
        if game is None:
            return
        leg = build_leg_analysis(game, shop.market, shop.selection, factors=[])
        if leg is not None:
            self.on_add_leg(leg)

    def _on_arb_select(self, _evt=None):
        sel = self.arb_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx >= len(self._arbs):
            return
        o = self._arbs[idx]
        stake = max(self._stake, 0.0)
        self.arb_detail.configure(
            text=(f"{o.summary} — on ${stake:.2f}: {format_money(stake * o.stake_a_pct)} A / "
                  f"{format_money(stake * o.stake_b_pct)} B → locked {format_money(stake * o.profit_pct)}"),
            text_color=T.TEXT,
        )

    def _on_spread_select(self, _evt=None):
        sel = self.spread_tree.selection()
        if not sel:
            self._spread_selected = None
            self.spread_add_btn.configure(state="disabled")
            return
        idx = int(sel[0])
        if idx >= len(self._spreads):
            return
        r = self._spreads[idx]
        self._spread_selected = r
        shop = r.shop
        self.spread_detail.configure(
            text=(f"{shop.matchup} · {_market_label(shop.market)} {shop.selection} — best {format_american(shop.best_price)} "
                  f"@ {shop.best_book}, worst {format_american(r.worst_price)} @ {r.worst_book} (Δ {r.price_spread} pts)"),
            text_color=T.TEXT,
        )
        self.spread_add_btn.configure(state="normal")

    def _add_selected_spread(self):
        if not self._spread_selected:
            return
        shop = self._spread_selected.shop
        game = self.state.find_game(shop.game_id)
        if game is None:
            return
        leg = build_leg_analysis(game, shop.market, shop.selection, factors=[])
        if leg is not None:
            self.on_add_leg(leg)


def _market_label(market: str) -> str:
    return {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}.get(market, market)


def _sel_label(market: str, selection: str, point: float | None) -> str:
    if market == "spreads" and point is not None:
        return f"{selection} {point:+g}"
    if market == "totals" and point is not None:
        return f"{selection} {point:g}"
    return selection
