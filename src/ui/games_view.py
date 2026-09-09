"""Board: today's games with the best price, the model probability and the edge per line.

Performance notes
-----------------
This is the densest screen in the app, so it is built to two rules:

* **Recycle, don't rebuild.** Game cards live in a pool. A re-render (new
  odds, a filter change, a sort change) rebinds existing widgets to new data
  and only creates a card when the pool runs dry. Destroying and recreating
  ~1500 widgets on every refresh was the single biggest stall on low-end
  machines and on macOS, where each Tcl round-trip is expensive.
* **CustomTkinter only where it shows.** The rounded card shell and the three
  market panels stay CTk; every label, row, pill and button inside them is a
  plain Tk widget from `fastwidgets`, which creates no canvas and draws no
  rounded rectangle.
"""
from __future__ import annotations
import customtkinter as ctk
from typing import Callable

from ..api.odds_api import Game, SPORT_LABELS
from ..analysis.probability import build_leg_analysis, LegAnalysis
from ..utils.formatters import format_american, format_pct, format_game_time
from . import theme as T
from . import fastwidgets as fw
from .widgets import Card, PageHeader, Toolbar, Segmented, EmptyState, entry, switch, make_scroll
from .state import AppState
from .runtime import LazyRenderMixin, render_chunked


MARKET_LABELS = {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}

# Rows per market panel, as (market_key, selection_getter, label_getter).
_ROWS: tuple[tuple[str, str, str], ...] = (
    ("h2h", "away", "away"), ("h2h", "home", "home"),
    ("spreads", "away", "away"), ("spreads", "home", "home"),
    ("totals", "Over", "Over"), ("totals", "Under", "Under"),
)


class GamesView(LazyRenderMixin, ctk.CTkFrame):
    """Scrollable list of game cards with inline odds and quick add-to-slip buttons."""

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
        self._sort = "time"
        self._ev_only = False
        self._query = ""
        self._pool: list["GameCard"] = []
        self._empty: EmptyState | None = None

        self.header = PageHeader(self, "Board", "Best price per line, model probability and edge at a glance.")
        self.header.pack(fill="x")

        bar = Toolbar(self)
        bar.pack(fill="x", padx=T.SP_4, pady=(0, T.SP_2))
        bar.label("Search")
        self.query_var = ctk.StringVar()
        entry(bar.inner, self.query_var, width=200, placeholder="Team…").pack(side="left", padx=(0, T.SP_4))
        self.query_var.trace_add("write", lambda *_: self._on_query())
        bar.label("Sort")
        Segmented(bar.inner, [("time", "Time"), ("edge", "Best edge"), ("conf", "Confidence")],
                  value="time", command=self._on_sort, height=26, font=T.FONT_SMALL).pack(side="left", padx=(0, T.SP_4))
        self.ev_var = ctk.BooleanVar(value=False)
        switch(bar.inner, "+EV lines only", self.ev_var, command=self._on_ev_toggle).pack(side="left")
        self.count_lbl = ctk.CTkLabel(bar.inner, text="", font=T.FONT_SMALL, text_color=T.TEXT_MUTED)
        self.count_lbl.pack(side="right")

        self.list = make_scroll(self)
        self.list.pack(fill="both", expand=True, padx=T.SP_4, pady=(0, T.SP_4))

        state.subscribe(self._on_state_event)

    # ---------------------------------------------------------------- events

    def _on_state_event(self, event: str):
        if event in ("games", "sport", "settings"):
            self.request_render()

    def _on_query(self):
        self._query = (self.query_var.get() or "").strip().lower()
        self.request_render()

    def _on_sort(self, key: str):
        self._sort = key
        self.request_render()

    def _on_ev_toggle(self):
        self._ev_only = bool(self.ev_var.get())
        self.request_render()

    # ---------------------------------------------------------------- render

    def render(self):
        games = list(self.state.games)
        sport = SPORT_LABELS.get(self.state.sport_key, self.state.sport_key)
        src = getattr(self.state, "games_source", "demo")
        self.header.set_source(src)
        if src == "live":
            self.header.hide_banner()
        else:
            self.header.show_banner("Showing demo odds. Add an Odds API key in Settings for live sportsbook lines.")

        if self._query:
            games = [g for g in games if self._query in g.home_team.lower() or self._query in g.away_team.lower()]

        # Price every selection once; sorting, filtering and the cards share it.
        summaries: list[tuple[Game, dict[tuple[str, str], LegAnalysis | None]]] = []
        for g in games:
            legs = {(mk, sel): build_leg_analysis(g, mk, sel, []) for mk, sel in _selections(g)}
            if self._ev_only and not any(l and l.edge > 0.0 for l in legs.values()):
                continue
            summaries.append((g, legs))

        if self._sort == "edge":
            summaries.sort(key=lambda gl: max((l.edge for l in gl[1].values() if l), default=-1), reverse=True)
        elif self._sort == "conf":
            summaries.sort(key=lambda gl: max((l.confidence for l in gl[1].values() if l), default=0), reverse=True)
        else:
            summaries.sort(key=lambda gl: gl[0].commence_time)

        self.header.set_subtitle(f"{len(self.state.games)} games  ·  {sport}  ·  model v2 with cross-market fusion")
        self.count_lbl.configure(text=f"{len(summaries)} shown")

        if self._empty is not None:
            self._empty.destroy()
            self._empty = None

        if not summaries:
            for card in self._pool:
                card.pack_forget()
            self._empty = EmptyState(
                self.list, "Nothing to show",
                "No games match the current filters." if self.state.games
                else "Hit Refresh in the top bar to pull the slate.")
            self._empty.pack(pady=T.SP_6 * 2)
            return

        # Park surplus cards; they stay alive for the next slate.
        for card in self._pool[len(summaries):]:
            card.pack_forget()

        # Rebind what already exists straight away, then build any shortfall a
        # few cards per event-loop tick so the window never locks up.
        reuse = min(len(self._pool), len(summaries))
        for i in range(reuse):
            card = self._pool[i]
            card.bind_game(*summaries[i], ev_only=self._ev_only)
            if not card.winfo_manager():
                card.pack(fill="x", pady=(0, T.SP_3), padx=2)

        pending = summaries[reuse:]
        if pending:
            def build(item):
                card = GameCard(self.list, self.state, self.on_add_leg, self.on_view_analysis)
                self._pool.append(card)
                card.bind_game(*item, ev_only=self._ev_only)
                card.pack(fill="x", pady=(0, T.SP_3), padx=2)
            render_chunked(self.list, pending, build, chunk=3)


def _selections(game: Game) -> list[tuple[str, str]]:
    return [
        ("h2h", game.away_team), ("h2h", game.home_team),
        ("spreads", game.away_team), ("spreads", game.home_team),
        ("totals", "Over"), ("totals", "Under"),
    ]


class _Row:
    """One price line inside a market panel. Built once, rebound many times."""

    __slots__ = ("frame", "name", "book", "model", "price", "add", "leg")

    def __init__(self, parent, on_add):
        bg = T.BG_ELEV_2
        self.leg: LegAnalysis | None = None
        self.frame = fw.frame(parent, bg=bg)
        self.frame.grid_columnconfigure(0, weight=1)
        self.name = fw.label(self.frame, "", bg=bg, fg=T.TEXT, font=T.FONT_SMALL)
        self.name.grid(row=0, column=0, sticky="ew", padx=(4, 1))
        self.book = fw.Pill(self.frame, "", bg=bg, variant="neutral", font=T.FONT_TINY, height=18, padx=4)
        self.book.grid(row=0, column=1, padx=1)
        # No fixed `width` here: on a plain Tk label it counts characters, and
        # padding the numbers out squeezed the weighted name column until
        # "Over 48.5" clipped. Percentages and prices are the same length in
        # practice, so natural sizing still lines up.
        self.model = fw.label(self.frame, "", bg=bg, fg=T.TEXT_MUTED, font=T.FONT_MONO_SMALL, anchor="e")
        self.model.grid(row=0, column=2, padx=(3, 2))
        self.price = fw.label(self.frame, "", bg=bg, fg=T.TEXT, font=T.FONT_BOLD, anchor="e")
        self.price.grid(row=0, column=3, padx=1)
        self.add = fw.Button(self.frame, "+", lambda: self._add(on_add), bg=bg, fill=T.BG_ELEV_3,
                             hover=T.ACCENT, font=T.FONT_BOLD, width=24, height=24)
        self.add.grid(row=0, column=4, padx=(1, 3))

    def _add(self, on_add):
        if self.leg is not None:
            on_add(self.leg)

    def bind(self, leg: LegAnalysis | None, display: str, market_key: str, dimmed: bool):
        self.leg = leg
        if leg is None:
            self.name.configure(text=_short_selection(market_key, display, None), fg=T.TEXT_DIM)
            self.book.set("—", variant="neutral")
            self.model.configure(text="")
            self.price.configure(text="—", fg=T.TEXT_DIM)
            self.add.configure_state("disabled")
            return
        self.add.configure_state("normal")
        self.name.configure(text=_short_selection(market_key, display, leg.point),
                            fg=(T.TEXT_DIM if dimmed else T.TEXT))
        self.book.set(_short_book(leg.bookmaker), variant="neutral")
        self.model.configure(text=format_pct(leg.model_prob, 0),
                             fg=(T.TEXT_DIM if dimmed else T.edge_color(leg.edge)))
        self.price.configure(text=format_american(leg.price), fg=(T.TEXT_DIM if dimmed else T.TEXT))
        fw.tip(self.model,
               f"Model {leg.model_prob*100:.1f}% (80% band {leg.prob_low*100:.0f}–{leg.prob_high*100:.0f}%)\n"
               f"Book implied {leg.book_implied*100:.1f}% · edge {leg.edge*100:+.1f}%\n" + "\n".join(leg.notes))
        fw.tip(self.book, f"Best price at {leg.bookmaker}")


class GameCard(Card):
    """One game. Widgets are created once; `bind_game` swaps in new data."""

    def __init__(self, master, state: AppState, on_add_leg, on_view_analysis):
        super().__init__(master)
        self.state = state
        self.on_add_leg = on_add_leg
        self.on_view_analysis = on_view_analysis
        self._game: Game | None = None
        bg = T.BG_ELEV_1

        outer = fw.frame(self, bg=bg)
        outer.pack(fill="x", padx=T.SP_3, pady=T.SP_3)

        header = fw.frame(outer, bg=bg)
        header.pack(fill="x")
        # Pack the fixed right-hand controls first: with `pack`, whatever is
        # placed first wins contested space, so an expanding title packed
        # first would overrun the pill instead of yielding to it.
        right = fw.frame(header, bg=bg)
        right.pack(side="right")
        left = fw.frame(header, bg=bg)
        left.pack(side="left", fill="x", expand=True)
        self.matchup = fw.label(left, "", bg=bg, fg=T.TEXT, font=T.FONT_HEAD)
        self.matchup.pack(anchor="w")
        self.meta = fw.label(left, "", bg=bg, fg=T.TEXT_MUTED, font=T.FONT_SMALL)
        self.meta.pack(anchor="w", pady=(2, 0))
        self.analysis_btn = fw.Button(right, "Analysis →", self._open_analysis, bg=bg,
                                      fill=T.BG_ELEV_3, hover=T.BG_ELEV_4, fg=T.ACCENT,
                                      width=110, height=30)
        self.analysis_btn.pack(side="right")
        self.edge_pill = fw.Pill(right, "", bg=bg, variant="neutral")
        self.edge_pill.pack(side="right", padx=(0, T.SP_2))

        markets = fw.frame(outer, bg=bg)
        markets.pack(fill="x", pady=(T.SP_3, 0))
        markets.grid_columnconfigure((0, 1, 2), weight=1, uniform="m")
        self.rows: list[_Row] = []
        for col, title in enumerate(("Moneyline", "Spread", "Total")):
            cell = ctk.CTkFrame(markets, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD)
            cell.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0))
            head = fw.frame(cell, bg=T.BG_ELEV_2)
            head.pack(fill="x", padx=T.SP_3, pady=(T.SP_2, 2))
            fw.label(head, title.upper(), bg=T.BG_ELEV_2, fg=T.TEXT_MUTED, font=T.FONT_LABEL).pack(side="left")
            fw.label(head, "model · price", bg=T.BG_ELEV_2, fg=T.TEXT_DIM, font=T.FONT_TINY).pack(side="right")
            for _ in range(2):
                row = _Row(cell, self.on_add_leg)
                row.frame.pack(fill="x", padx=T.SP_2, pady=(0, T.SP_1))
                self.rows.append(row)

    def _open_analysis(self):
        if self._game is not None:
            self.on_view_analysis(self._game.id)

    def bind_game(self, game: Game, legs: dict[tuple[str, str], LegAnalysis | None], *,
                  ev_only: bool = False):
        self._game = game
        self.matchup.configure(text=f"{game.away_team}  @  {game.home_team}")
        self.meta.configure(
            text=f"{game.sport_title}   ·   {format_game_time(game.commence_time)}   ·   {len(game.bookmakers)} books")

        best = max((l for l in legs.values() if l), key=lambda l: l.edge, default=None)
        if best is None:
            self.edge_pill.set("no lines", variant="neutral")
            fw.tip(self.edge_pill, "")
        else:
            self.edge_pill.set(f"best edge {best.edge*100:+.1f}%  ·  "
                               f"{_short_selection(best.market, best.selection, None)}",
                               variant=T.edge_variant(best.edge))
            fw.tip(self.edge_pill,
                   f"{MARKET_LABELS.get(best.market, best.market)} · {best.bookmaker} "
                   f"{format_american(best.price)}\nmodel {best.model_prob*100:.1f}% vs "
                   f"book {best.book_implied*100:.1f}%")

        order = ((("h2h", game.away_team), game.away_team, "h2h"),
                 (("h2h", game.home_team), game.home_team, "h2h"),
                 (("spreads", game.away_team), game.away_team, "spreads"),
                 (("spreads", game.home_team), game.home_team, "spreads"),
                 (("totals", "Over"), "Over", "totals"),
                 (("totals", "Under"), "Under", "totals"))
        # Panels are laid out column-major (ML | Spread | Total), two rows each.
        for i, (key, display, market_key) in enumerate(order):
            leg = legs.get(key)
            dimmed = ev_only and leg is not None and leg.edge <= 0
            self.rows[i].bind(leg, display, market_key, dimmed)


_GENERIC_SUFFIX = {"city", "united", "town", "fc", "state", "tech", "sox", "jays", "wings", "devils", "blue"}


def short_team(name: str) -> str:
    """Compact team label for tight cells: 'Kansas City Chiefs' → 'Chiefs'."""
    parts = (name or "").split()
    if len(parts) <= 1 or len(name) <= 12:
        return name
    if parts[-1].lower() in _GENERIC_SUFFIX and len(parts) >= 2:
        return " ".join(parts[-2:])
    return parts[-1]


def _short_selection(market_key: str, display: str, point: float | None) -> str:
    base = display if market_key == "totals" else short_team(display)
    if market_key == "spreads" and point is not None:
        return f"{base} {point:+g}"
    if market_key == "totals" and point is not None:
        return f"{base} {point:g}"
    return base


def _short_book(title: str) -> str:
    t = title or ""
    aliases = {"DraftKings": "DK", "FanDuel": "FD", "BetMGM": "MGM", "Caesars": "CZR", "PointsBet": "PB",
               "BetRivers": "BR", "Pinnacle": "PIN", "Bovada": "BOV", "BetOnline.ag": "BOL", "ESPN BET": "ESPN",
               "Fanatics": "FAN", "Unibet": "UNI", "Circa Sports": "CIRCA"}
    return aliases.get(t, t[:6])
