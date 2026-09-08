"""Team Slip Builder — PrizePicks-style pick-em on team markets.

Every game shows six tappable tiles (spread / total / moneyline, both sides).
Tapping a tile pushes a DFS-flavored `LegAnalysis` to the shared bet slip;
the slip switches to the power-play payout ladder once every leg is DFS.
Kalshi legs are priced at the model probability (no-vig yes contracts).
"""
from __future__ import annotations
import customtkinter as ctk
from typing import Callable

from ..analysis.probability import team_pick_to_leg, LegAnalysis, analyze_parlay
from ..api.odds_api import Game, SPORT_LABELS
from ..api.prizepicks_api import POWER_PAYOUTS
from ..utils.formatters import format_pct, format_game_time, format_american
from . import theme as T
from .widgets import Card, Pill, PageHeader, Toolbar, Segmented, EmptyState, ProbBar, Tooltip, make_scroll
from .state import AppState
from .runtime import LazyRenderMixin, render_chunked


PLATFORMS: tuple[tuple[str, str, str], ...] = (
    ("prizepicks", "PrizePicks", T.ACCENT),
    ("underdog",   "Underdog",   "#E8553C"),
    ("kalshi",     "Kalshi",     "#0FB28C"),
)
_PLATFORM_LABEL = {k: lbl for k, lbl, _c in PLATFORMS}
_PLATFORM_ACCENT = {k: c for k, _l, c in PLATFORMS}


class TeamSlipView(LazyRenderMixin, ctk.CTkFrame):
    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        self.platform = "prizepicks"

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
        self._ladder: dict[int, Pill] = {}
        for n in sorted(POWER_PAYOUTS):
            pill = Pill(self._ladder_frame, f"{n} · {POWER_PAYOUTS[n]:.0f}×", variant="neutral", font=T.FONT_TINY)
            pill.pack(side="left", padx=2)
            self._ladder[n] = pill
        self.slip_status = ctk.CTkLabel(self.header.actions, text="Pick 2+ legs for a slip.", font=T.FONT_SMALL, text_color=T.TEXT_MUTED)
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
            legs = [l for l in self.state.bet_slip if l.market.startswith("team_") and l.bookmaker.lower() == "kalshi"]
            n = len(legs)
            if n == 0:
                self.slip_status.configure(text="Pick 2+ legs for a Kalshi parlay.", text_color=T.TEXT_MUTED)
            elif n == 1:
                self.slip_status.configure(text=f"1 leg · fair price {format_american(legs[0].price)}. Add another for a parlay.", text_color=T.WARNING)
            else:
                a = analyze_parlay(legs)
                self.slip_status.configure(text=f"{n} legs · combined fair price {format_american(a.combined_american)} · hit {a.combined_prob*100:.1f}%",
                                           text_color=T.ACCENT)
            return

        self._payout_label.configure(text="POWER PLAY")
        dfs_legs = [l for l in self.state.bet_slip
                    if (l.market.startswith("team_") or l.market.startswith("prop_"))
                    and l.bookmaker.lower() in ("prizepicks", "underdog", "demo")]
        n = len(dfs_legs)
        for tier, pill in self._ladder.items():
            pill.pack(side="left", padx=2)
            pill.set(f"{tier} · {POWER_PAYOUTS[tier]:.0f}×", variant=("accent" if tier == n else "neutral"))
        if n == 0:
            self.slip_status.configure(text="Pick 2+ legs for a slip.", text_color=T.TEXT_MUTED)
        elif n == 1:
            self.slip_status.configure(text="1 leg · need at least one more.", text_color=T.WARNING)
        elif n in POWER_PAYOUTS:
            mult = POWER_PAYOUTS[n]
            self.slip_status.configure(text=f"{n} legs · {mult:.0f}× payout · break-even {100/mult:.1f}%", text_color=T.ACCENT)
        else:
            self.slip_status.configure(text=f"{n} legs · over max (6). Remove a leg to unlock payout.", text_color=T.NEGATIVE)

    # ---------------------------------------------------------------- render

    def render(self):
        for child in self.list.winfo_children():
            child.destroy()
        games = self.state.games
        self._refresh_subtitle()
        src = getattr(self.state, "games_source", "demo")
        self.header.set_source(src)
        if src == "live":
            self.header.hide_banner()
        else:
            self.header.show_banner("Showing demo odds. Add an Odds API key in Settings for live team lines.")
        self._update_strip()
        if not games:
            EmptyState(self.list, "No games loaded", "Hit Refresh in the top bar to pull the slate.").pack(pady=T.SP_6 * 2)
            return
        render_chunked(self.list, list(games),
                       lambda g: GamePickEmCard(self.list, g, self.state, self.on_add_leg,
                                                platform_provider=lambda: self.platform).pack(fill="x", pady=(0, T.SP_3), padx=2),
                       chunk=3)


class GamePickEmCard(Card):
    """One game, three market rows (Spread / Total / Moneyline), two tiles each."""

    def __init__(self, master, game: Game, state: AppState, on_add_leg, platform_provider=None):
        super().__init__(master)
        self.game = game
        self.state = state
        self.on_add_leg = on_add_leg
        self.platform_provider = platform_provider or (lambda: "prizepicks")
        outer = self.body(padx=T.SP_4, pady=T.SP_3)

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text=f"{game.away_team}  @  {game.home_team}", font=T.FONT_HEAD, text_color=T.TEXT).pack(side="left")
        ctk.CTkLabel(header, text=f"{format_game_time(game.commence_time)}   ·   {len(game.bookmakers)} books",
                     font=T.FONT_SMALL, text_color=T.TEXT_MUTED).pack(side="left", padx=T.SP_4)

        grid = ctk.CTkFrame(outer, fg_color="transparent")
        grid.pack(fill="x", pady=(T.SP_3, 0))
        grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="col")
        cols = [
            ("SPREAD", [("spreads", game.away_team, game.away_team), ("spreads", game.home_team, game.home_team)]),
            ("TOTAL", [("totals", "Over", "Over"), ("totals", "Under", "Under")]),
            ("MONEYLINE", [("h2h", game.away_team, f"{game.away_team} to win"), ("h2h", game.home_team, f"{game.home_team} to win")]),
        ]
        for c, (label, picks) in enumerate(cols):
            col = ctk.CTkFrame(grid, fg_color="transparent")
            col.grid(row=0, column=c, sticky="nsew", padx=(0 if c == 0 else T.SP_2, 0))
            ctk.CTkLabel(col, text=label, font=T.FONT_LABEL, text_color=T.TEXT_MUTED).pack(anchor="w", padx=2, pady=(0, 4))
            for market_key, selection, display in picks:
                PickTile(col, game=game, market_key=market_key, selection=selection, display_name=display,
                         on_add_leg=on_add_leg, platform_provider=self.platform_provider).pack(fill="x", pady=(0, T.SP_1))


class PickTile(ctk.CTkFrame):
    """A single tappable pick-em tile showing the model probability."""

    def __init__(self, master, *, game: Game, market_key: str, selection: str, display_name: str,
                 on_add_leg, platform_provider=None):
        super().__init__(master, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD)
        self.game, self.market_key, self.selection = game, market_key, selection
        self.on_add_leg = on_add_leg
        self.platform_provider = platform_provider or (lambda: "prizepicks")

        leg = team_pick_to_leg(game, market_key, selection, platform="prizepicks")
        prob = leg.model_prob if leg else 0.0
        if leg and market_key == "spreads" and leg.point is not None:
            top_text = f"{display_name}  {leg.point:+g}"
        elif leg and market_key == "totals" and leg.point is not None:
            top_text = f"{display_name} {leg.point:g}"
        else:
            top_text = display_name

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=T.SP_3, pady=T.SP_2)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=top_text, font=T.FONT_BOLD, text_color=T.TEXT, anchor="w").pack(side="left", fill="x", expand=True)
        Pill(top, format_pct(prob, 1) if prob > 0 else "—", variant="neutral",
             text_color=(T.prob_color(prob) if prob > 0 else T.TEXT_MUTED)).pack(side="right")

        bar = ProbBar(inner, height=12, bg=T.BG_ELEV_2)
        bar.pack(fill="x", pady=(2, 0))
        if leg:
            bar.set(prob, leg.prob_low, leg.prob_high, None)
            Tooltip(bar, "\n".join(leg.notes))
        bot = ctk.CTkFrame(inner, fg_color="transparent")
        bot.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(bot, text=(f"model · conf {leg.confidence*100:.0f}%" if leg else "no line"),
                     font=T.FONT_TINY, text_color=T.TEXT_DIM).pack(side="left")
        ctk.CTkButton(bot, text="+ Add", width=60, height=22, corner_radius=T.R_SM,
                      fg_color=T.BG_ELEV_3, hover_color=T.ACCENT, text_color=T.TEXT, font=T.FONT_TINY,
                      command=self._on_click).pack(side="right")
        for w in (self, inner, top, bot):
            w.bind("<Button-1>", lambda _e: self._on_click())

    def _on_click(self):
        leg = team_pick_to_leg(self.game, self.market_key, self.selection, platform=self.platform_provider())
        if leg is not None:
            self.on_add_leg(leg)
