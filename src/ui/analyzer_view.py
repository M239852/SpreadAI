"""Odds Analyzer — cross-book scanners (+EV / Arbitrage / Best Lines).

Three tabs, each backed by a ttk.Treeview for scroll-smooth rendering on
slates with thousands of combined book-quote rows.

+EV        — selections whose best available price beats the de-vigged
             consensus fair price by at least `min_edge`.
Arbitrage  — two-way markets where the best price on each side sums to
             <100% implied probability (guaranteed profit across two books).
             Includes the stake split for equalized return.
Best Lines — biggest price gap between the best and worst book for a
             single selection (pure line-shop wins).

Auto-refreshes off the `state.games` feed; the main app pulls new odds on
a timer.
"""
from __future__ import annotations
import customtkinter as ctk
from tkinter import ttk
from typing import Callable

from ..analysis.markets import (
    positive_ev_picks, arb_opportunities, biggest_line_spreads,
    EdgePick, ArbOpportunity, BestLineRow,
)
from ..analysis.probability import build_leg_analysis, LegAnalysis
from ..utils.formatters import format_american, format_pct, format_money
from . import theme as T
from .widgets import Pill
from .state import AppState


# Min thresholds wired to the UI — the sliders tweak these at runtime.
DEFAULT_MIN_EDGE = 0.02        # 2% fair-vs-best edge
DEFAULT_MIN_ARB_PROFIT = 0.005  # 0.5% locked return
MAX_ROWS = 400                  # cap per tab — Treeview can handle more but
                                #   anything past this is noise anyway.


class AnalyzerView(ctk.CTkFrame):
    """Tabbed cross-book scanner."""

    def __init__(
        self,
        master,
        state: AppState,
        on_add_leg: Callable[[LegAnalysis], None],
    ):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg

        self._min_edge = DEFAULT_MIN_EDGE
        self._min_arb = DEFAULT_MIN_ARB_PROFIT
        self._stake = 100.0  # per-row arb stake reference

        self._install_tree_style()
        self._build_header()
        self._build_tabs()

        state.subscribe(self._on_state_event)

    # ---------------- Styling ----------------

    def _install_tree_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Analyzer.Treeview",
            background=T.BG_ELEV_1,
            fieldbackground=T.BG_ELEV_1,
            foreground=T.TEXT,
            rowheight=26,
            borderwidth=0,
            font=T.FONT_SMALL,
        )
        style.configure(
            "Analyzer.Treeview.Heading",
            background=T.BG_ELEV_2,
            foreground=T.TEXT_MUTED,
            relief="flat",
            font=T.FONT_BOLD,
        )
        style.map(
            "Analyzer.Treeview",
            background=[("selected", T.ACCENT)],
            foreground=[("selected", T.BG)],
        )

    # ---------------- Header ----------------

    def _build_header(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(20, 6))

        col = ctk.CTkFrame(head, fg_color="transparent")
        col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(col, text="Odds Analyzer", font=T.FONT_TITLE, text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(
            col, text="Cross-book scanner — +EV, arbitrage, and the best line-shop wins on the slate.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 0))

        self.source_pill = Pill(head, "DEMO", color=T.BG_ELEV_3, text_color=T.TEXT_MUTED)
        self.source_pill.pack(side="right")

        # Shared summary strip
        summary = ctk.CTkFrame(self, fg_color=T.BG_ELEV_1, corner_radius=10)
        summary.pack(fill="x", padx=16, pady=(4, 6))
        inner = ctk.CTkFrame(summary, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=8)

        self.count_lbl = ctk.CTkLabel(
            inner, text="0 games scanned", font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        )
        self.count_lbl.pack(side="left")

        # Min-edge slider (controls +EV tab threshold)
        ctk.CTkLabel(inner, text="MIN EDGE", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left", padx=(24, 6))
        self.edge_lbl = ctk.CTkLabel(inner, text="2.0%", font=T.FONT_BOLD, text_color=T.ACCENT, width=46)
        self.edge_lbl.pack(side="left")
        self.edge_slider = ctk.CTkSlider(
            inner, from_=0.0, to=0.10, number_of_steps=20,
            width=140, command=self._on_edge_change,
        )
        self.edge_slider.set(DEFAULT_MIN_EDGE)
        self.edge_slider.pack(side="left", padx=(8, 0))

        # Min-arb slider
        ctk.CTkLabel(inner, text="MIN ARB", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left", padx=(24, 6))
        self.arb_lbl = ctk.CTkLabel(inner, text="0.50%", font=T.FONT_BOLD, text_color=T.ACCENT, width=46)
        self.arb_lbl.pack(side="left")
        self.arb_slider = ctk.CTkSlider(
            inner, from_=0.0, to=0.05, number_of_steps=25,
            width=140, command=self._on_arb_change,
        )
        self.arb_slider.set(DEFAULT_MIN_ARB_PROFIT)
        self.arb_slider.pack(side="left", padx=(8, 0))

        # Stake entry for arb split display
        ctk.CTkLabel(inner, text="STAKE $", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left", padx=(24, 6))
        self.stake_var = ctk.StringVar(value="100")
        stake_entry = ctk.CTkEntry(
            inner, textvariable=self.stake_var, width=70, height=26,
            fg_color=T.BG_ELEV_2, border_width=0, text_color=T.TEXT,
            justify="right",
        )
        stake_entry.pack(side="left")
        self.stake_var.trace_add("write", lambda *_: self._on_stake_change())

        self.refresh_btn = ctk.CTkButton(
            inner, text="↻ Rescan", width=100, height=28,
            fg_color=T.BG_ELEV_3, hover_color=T.ACCENT, text_color=T.TEXT,
            font=T.FONT_BOLD, corner_radius=6,
            command=self._rerender_all,
        )
        self.refresh_btn.pack(side="right")

    # ---------------- Tabs ----------------

    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(
            self, fg_color=T.BG, segmented_button_fg_color=T.BG_ELEV_1,
            segmented_button_selected_color=T.ACCENT,
            segmented_button_selected_hover_color=T.ACCENT_HOVER,
            segmented_button_unselected_color=T.BG_ELEV_1,
            segmented_button_unselected_hover_color=T.BG_ELEV_2,
            text_color=T.TEXT,
        )
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(4, 16))

        self.tab_ev = self.tabs.add("+EV picks")
        self.tab_arb = self.tabs.add("Arbitrage")
        self.tab_spread = self.tabs.add("Best Lines")

        self._build_ev_tab(self.tab_ev)
        self._build_arb_tab(self.tab_arb)
        self._build_spread_tab(self.tab_spread)

    # -------- +EV tab --------

    def _build_ev_tab(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_1, corner_radius=10)
        wrap.pack(fill="both", expand=True, pady=6)

        cols = ("matchup", "market", "selection", "book", "price", "fair", "edge", "ev")
        self.ev_tree = ttk.Treeview(
            wrap, columns=cols, show="headings",
            style="Analyzer.Treeview", selectmode="browse",
        )
        headings = {
            "matchup":   ("Matchup",   240, "w"),
            "market":    ("Market",    90,  "w"),
            "selection": ("Selection", 180, "w"),
            "book":      ("Book",      120, "w"),
            "price":     ("Price",     70,  "e"),
            "fair":      ("Fair %",    80,  "e"),
            "edge":      ("Edge",      80,  "e"),
            "ev":        ("EV / $1",   80,  "e"),
        }
        for c, (label, w, anchor) in headings.items():
            self.ev_tree.heading(c, text=label)
            self.ev_tree.column(c, width=w, anchor=anchor, stretch=(c == "matchup"))

        self.ev_tree.tag_configure("hot", foreground=T.POSITIVE, font=T.FONT_BOLD)
        self.ev_tree.tag_configure("warm", foreground=T.TEXT)

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.ev_tree.yview)
        self.ev_tree.configure(yscrollcommand=vsb.set)
        self.ev_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.ev_tree.bind("<<TreeviewSelect>>", self._on_ev_select)
        self.ev_tree.bind("<Double-1>", lambda e: self._add_selected_ev())

        bar = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_1, corner_radius=10)
        bar.pack(fill="x", pady=(0, 0))
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)
        self.ev_detail = ctk.CTkLabel(
            inner, text="Select a row — double-click or use Add to push it to the slip.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w",
        )
        self.ev_detail.pack(side="left", fill="x", expand=True)
        self.ev_add_btn = ctk.CTkButton(
            inner, text="+ Add to slip", width=130, height=32,
            fg_color=T.BG_ELEV_3, hover_color=T.ACCENT, text_color=T.TEXT,
            font=T.FONT_BOLD, corner_radius=6, state="disabled",
            command=self._add_selected_ev,
        )
        self.ev_add_btn.pack(side="right")

        self._ev_picks: list[EdgePick] = []
        self._ev_selected: EdgePick | None = None

    # -------- Arb tab --------

    def _build_arb_tab(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_1, corner_radius=10)
        wrap.pack(fill="both", expand=True, pady=6)

        cols = ("matchup", "market", "side_a", "side_b", "sum", "profit", "stake_a", "stake_b", "net")
        self.arb_tree = ttk.Treeview(
            wrap, columns=cols, show="headings",
            style="Analyzer.Treeview", selectmode="browse",
        )
        headings = {
            "matchup": ("Matchup",    220, "w"),
            "market":  ("Market",     80,  "w"),
            "side_a":  ("Side A",     220, "w"),
            "side_b":  ("Side B",     220, "w"),
            "sum":     ("Imp Sum",    70,  "e"),
            "profit":  ("Profit %",   80,  "e"),
            "stake_a": ("Stake A",    80,  "e"),
            "stake_b": ("Stake B",    80,  "e"),
            "net":     ("Net $",      70,  "e"),
        }
        for c, (label, w, anchor) in headings.items():
            self.arb_tree.heading(c, text=label)
            self.arb_tree.column(c, width=w, anchor=anchor, stretch=(c in ("side_a", "side_b")))

        self.arb_tree.tag_configure("hot", foreground=T.POSITIVE, font=T.FONT_BOLD)
        self.arb_tree.tag_configure("warm", foreground=T.TEXT)

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.arb_tree.yview)
        self.arb_tree.configure(yscrollcommand=vsb.set)
        self.arb_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.arb_tree.bind("<<TreeviewSelect>>", self._on_arb_select)

        bar = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_1, corner_radius=10)
        bar.pack(fill="x")
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)
        self.arb_detail = ctk.CTkLabel(
            inner,
            text="Arb opportunities require placing both sides on the listed books simultaneously.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w",
        )
        self.arb_detail.pack(side="left", fill="x", expand=True)

        self._arbs: list[ArbOpportunity] = []
        self._arb_selected: ArbOpportunity | None = None

    # -------- Best-Lines tab --------

    def _build_spread_tab(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_1, corner_radius=10)
        wrap.pack(fill="both", expand=True, pady=6)

        cols = ("matchup", "market", "selection", "best_book", "best_price", "worst_book", "worst_price", "spread")
        self.spread_tree = ttk.Treeview(
            wrap, columns=cols, show="headings",
            style="Analyzer.Treeview", selectmode="browse",
        )
        headings = {
            "matchup":     ("Matchup",     240, "w"),
            "market":      ("Market",      90,  "w"),
            "selection":   ("Selection",   180, "w"),
            "best_book":   ("Best book",   130, "w"),
            "best_price":  ("Best",        70,  "e"),
            "worst_book":  ("Worst book",  130, "w"),
            "worst_price": ("Worst",       70,  "e"),
            "spread":      ("Δ points",    70,  "e"),
        }
        for c, (label, w, anchor) in headings.items():
            self.spread_tree.heading(c, text=label)
            self.spread_tree.column(c, width=w, anchor=anchor, stretch=(c == "matchup"))

        self.spread_tree.tag_configure("hot", foreground=T.POSITIVE, font=T.FONT_BOLD)
        self.spread_tree.tag_configure("warm", foreground=T.TEXT)

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.spread_tree.yview)
        self.spread_tree.configure(yscrollcommand=vsb.set)
        self.spread_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.spread_tree.bind("<<TreeviewSelect>>", self._on_spread_select)
        self.spread_tree.bind("<Double-1>", lambda e: self._add_selected_spread())

        bar = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_1, corner_radius=10)
        bar.pack(fill="x")
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)
        self.spread_detail = ctk.CTkLabel(
            inner,
            text="Biggest price spreads across books — bet the best line or shop your existing ones.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w",
        )
        self.spread_detail.pack(side="left", fill="x", expand=True)
        self.spread_add_btn = ctk.CTkButton(
            inner, text="+ Add best line", width=140, height=32,
            fg_color=T.BG_ELEV_3, hover_color=T.ACCENT, text_color=T.TEXT,
            font=T.FONT_BOLD, corner_radius=6, state="disabled",
            command=self._add_selected_spread,
        )
        self.spread_add_btn.pack(side="right")

        self._spreads: list[BestLineRow] = []
        self._spread_selected: BestLineRow | None = None

    # ---------------- State ----------------

    def _on_state_event(self, event: str):
        if event in ("games", "sport"):
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

    # ---------------- Rendering ----------------

    def _rerender_all(self):
        games = self.state.games
        self.count_lbl.configure(text=f"{len(games)} games scanned")
        src = getattr(self.state, "games_source", "demo")
        if src == "live":
            self.source_pill.configure(text="LIVE", text_color=T.POSITIVE)
        else:
            self.source_pill.configure(text="DEMO", text_color=T.TEXT_MUTED)

        self._rerender_ev()
        self._rerender_arb()
        self._rerender_spread()

    def _rerender_ev(self):
        self.ev_tree.delete(*self.ev_tree.get_children())
        self._ev_picks = positive_ev_picks(self.state.games, min_edge=self._min_edge)[:MAX_ROWS]
        for i, p in enumerate(self._ev_picks):
            shop = p.shop
            point = shop.quotes[0].point if shop.quotes else None
            sel_text = _sel_label(shop.market, shop.selection, point)
            tag = "hot" if p.edge >= 0.04 else "warm"
            self.ev_tree.insert(
                "", "end", iid=str(i),
                values=(
                    shop.matchup,
                    _market_label(shop.market),
                    sel_text,
                    shop.best_book,
                    format_american(shop.best_price),
                    format_pct(shop.fair_prob, 1),
                    f"{p.edge*100:+.2f}%",
                    f"{p.ev_per_dollar:+.3f}",
                ),
                tags=(tag,),
            )

    def _rerender_arb(self):
        self.arb_tree.delete(*self.arb_tree.get_children())
        self._arbs = arb_opportunities(self.state.games, min_profit=self._min_arb)[:MAX_ROWS]
        stake = max(self._stake, 0.0)
        for i, o in enumerate(self._arbs):
            side_a = (f"{_sel_label(o.market, o.selection_a, o.point_a)} "
                      f"@ {o.book_a_title} {format_american(o.price_a)}")
            side_b = (f"{_sel_label(o.market, o.selection_b, o.point_b)} "
                      f"@ {o.book_b_title} {format_american(o.price_b)}")
            stake_a = stake * o.stake_a_pct
            stake_b = stake * o.stake_b_pct
            net = stake * o.profit_pct
            tag = "hot" if o.profit_pct >= 0.015 else "warm"
            self.arb_tree.insert(
                "", "end", iid=str(i),
                values=(
                    o.matchup,
                    _market_label(o.market),
                    side_a,
                    side_b,
                    f"{o.implied_sum*100:.2f}%",
                    f"+{o.profit_pct*100:.2f}%",
                    format_money(stake_a, 2),
                    format_money(stake_b, 2),
                    format_money(net, 2),
                ),
                tags=(tag,),
            )

    def _rerender_spread(self):
        self.spread_tree.delete(*self.spread_tree.get_children())
        self._spreads = biggest_line_spreads(self.state.games, limit=MAX_ROWS)
        for i, r in enumerate(self._spreads):
            shop = r.shop
            point = shop.quotes[0].point if shop.quotes else None
            sel_text = _sel_label(shop.market, shop.selection, point)
            tag = "hot" if r.price_spread >= 25 else "warm"
            self.spread_tree.insert(
                "", "end", iid=str(i),
                values=(
                    shop.matchup,
                    _market_label(shop.market),
                    sel_text,
                    shop.best_book,
                    format_american(shop.best_price),
                    r.worst_book,
                    format_american(r.worst_price),
                    str(r.price_spread),
                ),
                tags=(tag,),
            )

    # ---------------- Selection handlers ----------------

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
        self.ev_detail.configure(
            text=f"{p.summary}  ·  fair {format_pct(p.shop.fair_prob, 1)} / edge {p.edge*100:+.2f}%",
            text_color=T.TEXT,
        )
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
            self._arb_selected = None
            return
        idx = int(sel[0])
        if idx >= len(self._arbs):
            return
        o = self._arbs[idx]
        self._arb_selected = o
        stake = max(self._stake, 0.0)
        stake_a = stake * o.stake_a_pct
        stake_b = stake * o.stake_b_pct
        net = stake * o.profit_pct
        self.arb_detail.configure(
            text=(
                f"{o.summary} — on ${stake:.2f}: "
                f"{format_money(stake_a)} A / {format_money(stake_b)} B → locked {format_money(net)}"
            ),
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
            text=(
                f"{shop.matchup} · {_market_label(shop.market)} "
                f"{shop.selection} — best {format_american(shop.best_price)} @ {shop.best_book}, "
                f"worst {format_american(r.worst_price)} @ {r.worst_book} "
                f"(Δ {r.price_spread} pts)"
            ),
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


# ---------------- Formatting helpers ----------------

def _market_label(market: str) -> str:
    return {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}.get(market, market)


def _sel_label(market: str, selection: str, point: float | None) -> str:
    if market == "spreads" and point is not None:
        return f"{selection} {point:+g}"
    if market == "totals" and point is not None:
        return f"{selection} {point}"
    return selection
