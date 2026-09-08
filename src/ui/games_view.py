"""Board: today's games with the best price, the model probability and the edge per line."""
from __future__ import annotations
import customtkinter as ctk
from typing import Callable

from ..api.odds_api import Game, SPORT_LABELS
from ..analysis.probability import build_leg_analysis, LegAnalysis
from ..utils.formatters import format_american, format_pct, format_game_time
from . import theme as T
from .widgets import Card, Pill, PageHeader, Toolbar, Segmented, EmptyState, GhostButton, entry, switch, make_scroll, Tooltip
from .state import AppState
from .runtime import LazyRenderMixin, render_chunked


MARKET_LABELS = {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}


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
        self._cards: dict[str, "GameCard"] = {}

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
        self.empty = EmptyState(self.list, "No games loaded", "Hit Refresh in the top bar to pull the slate.")

        state.subscribe(self._on_state_event)

    # ---------------------------------------------------------------- events

    def _on_state_event(self, event: str):
        if event in ("games", "sport", "settings"):
            self.request_render()

    def _on_query(self):
        self._query = (self.query_var.get() or "").strip().lower()
        self.render()

    def _on_sort(self, key: str):
        self._sort = key
        self.render()

    def _on_ev_toggle(self):
        self._ev_only = bool(self.ev_var.get())
        self.render()

    # ---------------------------------------------------------------- render

    def render(self):
        for child in self.list.winfo_children():
            child.destroy()

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

        # Pre-compute the legs per game once (shared by sorting, filtering and cards).
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

        if not summaries:
            self.empty = EmptyState(self.list, "Nothing to show",
                                    "No games match the current filters." if self.state.games else "Hit Refresh in the top bar to pull the slate.")
            self.empty.pack(pady=T.SP_6 * 2)
            return

        # Cards are built a few per event-loop tick so a 60-game slate never
        # freezes the window; a new render() cancels an in-flight one.
        def build(item):
            g, legs = item
            GameCard(self.list, g, legs, self.state, self.on_add_leg, self.on_view_analysis,
                     ev_only=self._ev_only).pack(fill="x", pady=(0, T.SP_3), padx=2)
        render_chunked(self.list, summaries, build, chunk=3)


def _selections(game: Game) -> list[tuple[str, str]]:
    return [
        ("h2h", game.away_team), ("h2h", game.home_team),
        ("spreads", game.away_team), ("spreads", game.home_team),
        ("totals", "Over"), ("totals", "Under"),
    ]


class GameCard(Card):
    def __init__(
        self,
        master,
        game: Game,
        legs: dict[tuple[str, str], LegAnalysis | None],
        state: AppState,
        on_add_leg: Callable[[LegAnalysis], None],
        on_view_analysis: Callable[[str], None],
        *,
        ev_only: bool = False,
    ):
        super().__init__(master)
        self.game = game
        self.legs = legs
        self.state = state
        self.on_add_leg = on_add_leg
        self.on_view_analysis = on_view_analysis
        self.ev_only = ev_only

        outer = self.body(padx=T.SP_4, pady=T.SP_3)

        # --- Header row ---
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x")
        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(left, text=f"{game.away_team}  @  {game.home_team}", font=T.FONT_HEAD, text_color=T.TEXT,
                     anchor="w").pack(anchor="w")
        ctk.CTkLabel(
            left,
            text=f"{game.sport_title}   ·   {format_game_time(game.commence_time)}   ·   {len(game.bookmakers)} books",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right")
        GhostButton(right, "Analysis →", command=lambda: self.on_view_analysis(game.id),
                    width=110, height=30, text_color=T.ACCENT).pack(side="right")
        best = max((l for l in legs.values() if l), key=lambda l: l.edge, default=None)
        if best is not None:
            variant = T.edge_variant(best.edge)
            pill = Pill(right, f"best edge {best.edge*100:+.1f}%  ·  {best.selection}", variant=variant)
            pill.pack(side="right", padx=(0, T.SP_2))
            Tooltip(pill, f"{MARKET_LABELS.get(best.market, best.market)} · {best.bookmaker} {format_american(best.price)}\n"
                          f"model {best.model_prob*100:.1f}% vs book {best.book_implied*100:.1f}%")

        # --- Markets ---
        markets = ctk.CTkFrame(outer, fg_color="transparent")
        markets.pack(fill="x", pady=(T.SP_3, 0))
        markets.grid_columnconfigure((0, 1, 2), weight=1, uniform="m")
        self._cell(markets, 0, "Moneyline", [("h2h", game.away_team, game.away_team), ("h2h", game.home_team, game.home_team)])
        self._cell(markets, 1, "Spread", [("spreads", game.away_team, game.away_team), ("spreads", game.home_team, game.home_team)])
        self._cell(markets, 2, "Total", [("totals", "Over", "Over"), ("totals", "Under", "Under")])

    def _cell(self, parent, col: int, title: str, rows: list[tuple[str, str, str]]):
        cell = ctk.CTkFrame(parent, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD)
        cell.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else T.SP_2, 0))
        head = ctk.CTkFrame(cell, fg_color="transparent")
        head.pack(fill="x", padx=T.SP_3, pady=(T.SP_2, 2))
        ctk.CTkLabel(head, text=title.upper(), font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(side="left")
        ctk.CTkLabel(head, text="model · price", font=T.FONT_TINY, text_color=T.TEXT_DIM).pack(side="right")

        for market_key, selection, display in rows:
            leg = self.legs.get((market_key, selection))
            row = ctk.CTkFrame(cell, fg_color="transparent")
            row.pack(fill="x", padx=T.SP_2, pady=(0, T.SP_1))
            row.grid_columnconfigure(0, weight=1)

            if leg is None:
                ctk.CTkLabel(row, text=display, font=T.FONT_SMALL, text_color=T.TEXT_DIM, anchor="w").grid(row=0, column=0, sticky="w", padx=4)
                ctk.CTkLabel(row, text="—", font=T.FONT_BOLD, text_color=T.TEXT_DIM).grid(row=0, column=3, padx=4)
                continue

            dimmed = self.ev_only and leg.edge <= 0
            name_color = T.TEXT_DIM if dimmed else T.TEXT
            short = _short_selection(market_key, display, leg.point)
            name_lbl = ctk.CTkLabel(row, text=short, font=T.FONT_SMALL, text_color=name_color, anchor="w")
            name_lbl.grid(row=0, column=0, sticky="w", padx=(4, 2))
            if short != leg.selection:
                Tooltip(name_lbl, leg.selection)

            book_short = _short_book(leg.bookmaker)
            book_pill = Pill(row, book_short, variant="neutral", font=T.FONT_TINY, height=18, width=48)
            book_pill.grid(row=0, column=1, padx=2)
            Tooltip(book_pill, f"Best price at {leg.bookmaker}")

            model_lbl = ctk.CTkLabel(row, text=format_pct(leg.model_prob, 0), font=T.FONT_MONO_SMALL,
                                     text_color=(T.TEXT_DIM if dimmed else T.edge_color(leg.edge)), width=36, anchor="e")
            model_lbl.grid(row=0, column=2, padx=2)
            Tooltip(model_lbl, f"Model {leg.model_prob*100:.1f}% (80% band {leg.prob_low*100:.0f}–{leg.prob_high*100:.0f}%)\n"
                               f"Book implied {leg.book_implied*100:.1f}% · edge {leg.edge*100:+.1f}%\n"
                               + "\n".join(leg.notes))

            ctk.CTkLabel(row, text=format_american(leg.price), font=T.FONT_BOLD,
                         text_color=(T.TEXT_DIM if dimmed else T.TEXT), width=48, anchor="e").grid(row=0, column=3, padx=2)

            ctk.CTkButton(
                row, text="+", width=26, height=24, corner_radius=T.R_SM,
                fg_color=T.BG_ELEV_3, hover_color=T.ACCENT, text_color=T.TEXT, font=T.FONT_BOLD,
                command=lambda l=leg: self.on_add_leg(l),
            ).grid(row=0, column=4, padx=(2, 4))


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
