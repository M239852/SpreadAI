"""Team Slip Builder — PrizePicks-style pick-em on team markets.

Every game shows six tappable tiles (spread / total / moneyline, both sides).
Tapping a tile pushes a DFS-flavored `LegAnalysis` to the shared bet slip; the
slip switches to the power-play payout ladder once every leg is DFS. Kalshi
legs are priced at the model probability (no-vig yes contracts).

Cards are pooled and rebound like the Board's, and only the rounded tile
shells stay on CustomTkinter.
"""
from __future__ import annotations
import customtkinter as ctk
from typing import Callable

from ..analysis.probability import team_pick_to_leg, LegAnalysis, analyze_parlay
from ..api.odds_api import Game, SPORT_LABELS
from ..api.prizepicks_api import POWER_PAYOUTS
from ..utils.formatters import format_pct, format_game_time, format_american
from . import theme as T
from . import fastwidgets as fw
from .widgets import Card, PageHeader, Toolbar, Segmented, EmptyState, make_scroll
from .state import AppState
from .runtime import LazyRenderMixin, render_chunked
from .games_view import short_team


PLATFORMS: tuple[tuple[str, str, str], ...] = (
    ("prizepicks", "PrizePicks", T.ACCENT),
    ("underdog",   "Underdog",   "#E8553C"),
    ("kalshi",     "Kalshi",     "#0FB28C"),
)
_PLATFORM_LABEL = {k: lbl for k, lbl, _c in PLATFORMS}
_PLATFORM_ACCENT = {k: c for k, _l, c in PLATFORMS}

# Column layout: (heading, [(market_key, side)]) where side is away/home/over/under.
_COLUMNS = (("SPREAD", "spreads"), ("TOTAL", "totals"), ("MONEYLINE", "h2h"))


class TeamSlipView(LazyRenderMixin, ctk.CTkFrame):
    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        self.platform = "prizepicks"
        self._pool: list["GamePickEmCard"] = []
        self._empty: EmptyState | None = None

        self.header = PageHeader(self, "Team Slip Builder", "")
        self.header.pack(fill="x")
        self._build_strip()

        self.list = make_scroll(self)
        self.list.pack(fill="both", expand=True, padx=T.SP_4, pady=(0, T.SP_4))
        state.subscribe(self._on_state_event)

    def _build_strip(self):
        bar = Toolbar(self)
        bar.pack(fill="x", padx=T.SP_4, pady=(0, T.SP_2))
        bar.label("Platform")
        self.platform_seg = Segmented(bar.inner, [(k, l) for k, l, _c in PLATFORMS], value="prizepicks",
                                      command=self._pick_platform, height=26, font=T.FONT_SMALL)
        self.platform_seg.pack(side="left", padx=(0, T.SP_5))
        self._payout_label = bar.label("Power play")
        self._ladder_frame = ctk.CTkFrame(bar.inner, fg_color="transparent")
        self._ladder_frame.pack(side="left")
        self._ladder: dict[int, fw.Pill] = {}
        for n in sorted(POWER_PAYOUTS):
            pill = fw.Pill(self._ladder_frame, f"{n} · {POWER_PAYOUTS[n]:.0f}×",
                           bg=T.BG_ELEV_1, variant="neutral", font=T.FONT_TINY, height=18)
            pill.pack(side="left", padx=2)
            self._ladder[n] = pill
        self.slip_status = ctk.CTkLabel(self.header.actions, text="Pick 2+ legs for a slip.",
                                        font=T.FONT_SMALL, text_color=T.TEXT_MUTED)
        self.slip_status.pack(side="right", padx=(0, T.SP_3))

    # ---------------------------------------------------------------- platform

    def _pick_platform(self, key: str):
        if key not in _PLATFORM_LABEL:
            return
        self.platform = key
        self.platform_seg.set_accent(_PLATFORM_ACCENT[key])
        self._refresh_subtitle()
        self._update_strip()

    def _refresh_subtitle(self):
        sport_label = SPORT_LABELS.get(self.state.sport_key, self.state.sport_key)
        platform = _PLATFORM_LABEL.get(self.platform, self.platform)
        tail = ("tap a tile to add — Kalshi parlays pay the no-vig product of the model prices."
                if self.platform == "kalshi" else f"tap a tile to add, 2–6 legs for a {platform} power play.")
        self.header.set_subtitle(f"{len(self.state.games)} games  ·  {sport_label}  ·  {tail}")

    # ---------------------------------------------------------------- state

    def _on_state_event(self, event: str):
        if event in ("games", "sport"):
            self.request_render()
        if event in ("betslip", "games", "sport"):
            self._update_strip()

    def _update_strip(self):
        if self.platform == "kalshi":
            for pill in self._ladder.values():
                pill.pack_forget()
            self._payout_label.configure(text="KALSHI YES PARLAY")
            legs = [l for l in self.state.bet_slip
                    if l.market.startswith("team_") and l.bookmaker.lower() == "kalshi"]
            n = len(legs)
            if n == 0:
                self.slip_status.configure(text="Pick 2+ legs for a Kalshi parlay.", text_color=T.TEXT_MUTED)
            elif n == 1:
                self.slip_status.configure(
                    text=f"1 leg · fair price {format_american(legs[0].price)}. Add another for a parlay.",
                    text_color=T.WARNING)
            else:
                a = analyze_parlay(legs)
                self.slip_status.configure(
                    text=f"{n} legs · combined fair price {format_american(a.combined_american)} "
                         f"· hit {a.combined_prob*100:.1f}%", text_color=T.ACCENT)
            return

        self._payout_label.configure(text="POWER PLAY")
        dfs_legs = [l for l in self.state.bet_slip
                    if (l.market.startswith("team_") or l.market.startswith("prop_"))
                    and l.bookmaker.lower() in ("prizepicks", "underdog", "demo")]
        n = len(dfs_legs)
        for tier, pill in self._ladder.items():
            if not pill.winfo_manager():
                pill.pack(side="left", padx=2)
            pill.set(f"{tier} · {POWER_PAYOUTS[tier]:.0f}×", variant=("accent" if tier == n else "neutral"))
        if n == 0:
            self.slip_status.configure(text="Pick 2+ legs for a slip.", text_color=T.TEXT_MUTED)
        elif n == 1:
            self.slip_status.configure(text="1 leg · need at least one more.", text_color=T.WARNING)
        elif n in POWER_PAYOUTS:
            mult = POWER_PAYOUTS[n]
            self.slip_status.configure(text=f"{n} legs · {mult:.0f}× payout · break-even {100/mult:.1f}%",
                                       text_color=T.ACCENT)
        else:
            self.slip_status.configure(text=f"{n} legs · over max (6). Remove a leg to unlock payout.",
                                       text_color=T.NEGATIVE)

    # ---------------------------------------------------------------- render

    def render(self):
        games = list(self.state.games)
        self._refresh_subtitle()
        src = getattr(self.state, "games_source", "demo")
        self.header.set_source(src)
        if src == "live":
            self.header.hide_banner()
        else:
            self.header.show_banner("Showing demo odds. Add an Odds API key in Settings for live team lines.")
        self._update_strip()

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
                card = GamePickEmCard(self.list, self.on_add_leg, lambda: self.platform)
                self._pool.append(card)
                card.bind_game(game)
                card.pack(fill="x", pady=(0, T.SP_3), padx=2)
            render_chunked(self.list, pending, build, chunk=3)


class PickTile:
    """One tappable pick-em tile. Built once, rebound per game."""

    __slots__ = ("frame", "title", "prob", "bar", "note", "add", "_game", "_market", "_selection", "_provider", "_on_add")

    def __init__(self, parent, on_add_leg, platform_provider):
        bg = T.BG_ELEV_2
        self._on_add = on_add_leg
        self._provider = platform_provider
        self._game: Game | None = None
        self._market = ""
        self._selection = ""

        self.frame = ctk.CTkFrame(parent, fg_color=bg, corner_radius=T.R_MD)
        inner = fw.frame(self.frame, bg=bg)
        inner.pack(fill="both", expand=True, padx=T.SP_3, pady=T.SP_2)

        top = fw.frame(inner, bg=bg)
        top.pack(fill="x")
        self.prob = fw.Pill(top, "—", bg=bg, variant="neutral")
        self.prob.pack(side="right")
        self.title = fw.label(top, "", bg=bg, fg=T.TEXT, font=T.FONT_BOLD)
        self.title.pack(side="left", fill="x", expand=True)

        self.bar = fw.ProbBar(inner, height=12, bg=bg)
        self.bar.pack(fill="x", pady=(2, 0))

        bot = fw.frame(inner, bg=bg)
        bot.pack(fill="x", pady=(2, 0))
        self.note = fw.label(bot, "", bg=bg, fg=T.TEXT_DIM, font=T.FONT_TINY)
        self.note.pack(side="left")
        self.add = fw.Button(bot, "+ Add", self._click, bg=bg, fill=T.BG_ELEV_3, hover=T.ACCENT,
                             font=T.FONT_TINY, width=60, height=22)
        self.add.pack(side="right")
        for w in (inner, top, bot, self.title):
            w.bind("<Button-1>", lambda _e: self._click(), add="+")

    def bind(self, game: Game, market_key: str, selection: str, display: str):
        self._game, self._market, self._selection = game, market_key, selection
        leg = team_pick_to_leg(game, market_key, selection, platform="prizepicks")
        if leg is None:
            self.title.configure(text=display)
            self.prob.set("—", variant="neutral")
            self.bar.set(0.5, 0.5, 0.5, None)
            self.note.configure(text="no line")
            self.add.configure_state("disabled")
            return
        prob = leg.model_prob
        if market_key == "spreads" and leg.point is not None:
            self.title.configure(text=f"{display}  {leg.point:+g}")
        elif market_key == "totals" and leg.point is not None:
            self.title.configure(text=f"{display} {leg.point:g}")
        else:
            self.title.configure(text=display)
        self.prob.set(format_pct(prob, 1), variant="neutral", fg=T.prob_color(prob))
        self.bar.set(prob, leg.prob_low, leg.prob_high, None)
        self.note.configure(text=f"model · conf {leg.confidence*100:.0f}%")
        self.add.configure_state("normal")
        fw.tip(self.bar, "\n".join(leg.notes))

    def _click(self):
        if self._game is None or not self._selection:
            return
        leg = team_pick_to_leg(self._game, self._market, self._selection, platform=self._provider())
        if leg is not None:
            self._on_add(leg)


class GamePickEmCard(Card):
    """One game, three market columns (Spread / Total / Moneyline), two tiles each."""

    def __init__(self, master, on_add_leg, platform_provider):
        super().__init__(master)
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

        grid = fw.frame(outer, bg=bg)
        grid.pack(fill="x", pady=(T.SP_3, 0))
        grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="col")
        self.tiles: list[PickTile] = []
        for c, (heading, _market) in enumerate(_COLUMNS):
            col = fw.frame(grid, bg=bg)
            col.grid(row=0, column=c, sticky="nsew", padx=(0 if c == 0 else T.SP_2, 0))
            fw.label(col, heading, bg=bg, fg=T.TEXT_MUTED, font=T.FONT_LABEL).pack(anchor="w", padx=2, pady=(0, 4))
            for _ in range(2):
                tile = PickTile(col, on_add_leg, platform_provider)
                tile.frame.pack(fill="x", pady=(0, T.SP_1))
                self.tiles.append(tile)

    def bind_game(self, game: Game):
        self._game = game
        self.matchup.configure(text=f"{game.away_team}  @  {game.home_team}")
        self.meta.configure(text=f"{format_game_time(game.commence_time)}   ·   {len(game.bookmakers)} books")
        away, home = short_team(game.away_team), short_team(game.home_team)
        picks = (
            ("spreads", game.away_team, away), ("spreads", game.home_team, home),
            ("totals", "Over", "Over"), ("totals", "Under", "Under"),
            ("h2h", game.away_team, f"{away} to win"),
            ("h2h", game.home_team, f"{home} to win"),
        )
        for tile, (market_key, selection, display) in zip(self.tiles, picks):
            tile.bind(game, market_key, selection, display)
