"""Team Slip Builder — PrizePicks-style pick-em on team markets.

Every game on the slate shows six tappable pick tiles:

    SPREAD      Away +pts   |   Home -pts
    TOTAL       Over N      |   Under N
    MONEYLINE   Away win    |   Home win

Tapping a tile pushes a DFS-flavored `LegAnalysis` to the shared bet slip.
Because each tile's leg carries `bookmaker="PrizePicks"` and a `team_*`
market prefix, the slip panel automatically switches to PrizePicks power-play
payout math once every leg on the slip is DFS (2-leg=3x, 3-leg=5x, ...
6-leg=25x).

The tile shows the de-vigged consensus fair probability pulled from
sportsbook lines — i.e. the same math behind the Analyzer's fair %. A pick is
implicitly +EV when its fair prob is higher than the power-play break-even
(1 / multiplier for the current slip length).
"""
from __future__ import annotations
import customtkinter as ctk
from typing import Callable

from ..analysis.probability import (
    team_pick_to_leg, compute_fair_prob_from_game, LegAnalysis, _consensus_point,
    analyze_parlay,
)
from ..api.odds_api import Game, SPORT_LABELS
from ..api.prizepicks_api import POWER_PAYOUTS
from ..utils.formatters import format_pct, format_game_time, format_american
from . import theme as T
from .widgets import Card, Pill, make_scroll
from .state import AppState


# Pick-tile layout constants
TILE_MIN_W = 180
TILE_H = 78

# Platforms supported by the team-slip builder. The order is the order they
# appear in the segmented selector. PrizePicks first because it's the most-
# requested DFS book; Kalshi last because it's the prediction-market option
# whose payout math is different from the DFS books.
PLATFORMS: tuple[tuple[str, str, str], ...] = (
    # (key passed to team_pick_to_leg, label shown on the button, accent color token)
    ("prizepicks", "PrizePicks", T.ACCENT),
    ("underdog",   "Underdog",   "#E8553C"),
    ("kalshi",     "Kalshi",     "#0FB28C"),
)
_PLATFORM_LABEL = {k: lbl for k, lbl, _c in PLATFORMS}


class TeamSlipView(ctk.CTkFrame):
    """PrizePicks-style team pick-em builder."""

    def __init__(
        self,
        master,
        state: AppState,
        on_add_leg: Callable[[LegAnalysis], None],
    ):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        # Default to PrizePicks; users on the Underdog/Kalshi panels switch
        # explicitly. The platform stamps the bookmaker on every leg this view
        # creates, which is what the bet slip uses to pick the payout math.
        self.platform = "prizepicks"

        self._build_header()
        self._build_platform_selector()
        self._build_payout_strip()

        self.banner = ctk.CTkLabel(
            self, text="", font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
            fg_color=T.BG_ELEV_2, corner_radius=8, anchor="w", height=34,
        )
        self.empty = ctk.CTkLabel(
            self,
            text="No games loaded. Click Refresh in the sidebar.",
            font=T.FONT, text_color=T.TEXT_MUTED,
        )

        self.list = make_scroll(self)
        self.list.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        state.subscribe(self._on_state_event)

    # ---------------- Header ----------------

    def _build_header(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(20, 6))

        col = ctk.CTkFrame(head, fg_color="transparent")
        col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(col, text="Team Slip Builder", font=T.FONT_TITLE, text_color=T.TEXT).pack(anchor="w")
        self.subtitle = ctk.CTkLabel(
            col,
            text="",   # filled in by render() / _refresh_subtitle once platform is known
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        )
        self.subtitle.pack(anchor="w", pady=(2, 0))

        self.source_pill = Pill(head, "DEMO", color=T.BG_ELEV_3, text_color=T.TEXT_MUTED)
        self.source_pill.pack(side="right")

    def _build_platform_selector(self):
        """Segmented platform selector. Stamps each new leg with the chosen book."""
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(fill="x", padx=24, pady=(0, 6))

        ctk.CTkLabel(
            wrap, text="PLATFORM", font=T.FONT_TINY, text_color=T.TEXT_MUTED,
        ).pack(side="left", padx=(0, 10))

        self._platform_buttons: dict[str, ctk.CTkButton] = {}
        for key, label, accent in PLATFORMS:
            btn = ctk.CTkButton(
                wrap, text=label, height=30, width=110, corner_radius=8,
                fg_color=T.BG_ELEV_2, hover_color=T.BG_ELEV_3,
                text_color=T.TEXT, font=T.FONT_BOLD,
                command=lambda k=key: self._pick_platform(k),
            )
            btn.pack(side="left", padx=(0, 6))
            # Stash the accent so the active-state styling can use it.
            btn._accent_color = accent  # type: ignore[attr-defined]
            self._platform_buttons[key] = btn
        self._highlight_platform()

    def _pick_platform(self, key: str):
        if key not in _PLATFORM_LABEL:
            return
        self.platform = key
        self._highlight_platform()
        self._refresh_subtitle()
        self._update_payout_strip()

    def _highlight_platform(self):
        for k, btn in self._platform_buttons.items():
            if k == self.platform:
                btn.configure(
                    fg_color=getattr(btn, "_accent_color", T.ACCENT),
                    text_color=T.BG,
                )
            else:
                btn.configure(fg_color=T.BG_ELEV_2, text_color=T.TEXT)

    def _refresh_subtitle(self):
        sport_label = SPORT_LABELS.get(self.state.sport_key, self.state.sport_key)
        n_games = len(self.state.games)
        platform = _PLATFORM_LABEL.get(self.platform, self.platform)
        if self.platform == "kalshi":
            tail = "tap a tile to add — Kalshi parlay pays the no-vig product."
        else:
            tail = f"tap a tile to add, 2–6 legs for a {platform} power play."
        self.subtitle.configure(
            text=f"{n_games} games  ·  {sport_label}  ·  {tail}",
        )

    def _build_payout_strip(self):
        """Always-visible strip showing payout ladder + current slip length."""
        strip = ctk.CTkFrame(self, fg_color=T.BG_ELEV_1, corner_radius=10)
        strip.pack(fill="x", padx=16, pady=(2, 8))

        inner = ctk.CTkFrame(strip, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        # Stored on self so the platform-aware update can swap text between
        # "POWER PLAY" (PrizePicks/Underdog) and "KALSHI YES PARLAY" (Kalshi).
        self._payout_label = ctk.CTkLabel(
            inner, text="POWER PLAY", font=T.FONT_TINY, text_color=T.TEXT_MUTED,
        )
        self._payout_label.pack(side="left")

        # Ladder of all tiers — highlight whichever matches the current slip
        self._ladder: dict[int, ctk.CTkLabel] = {}
        for n in sorted(POWER_PAYOUTS.keys()):
            mult = POWER_PAYOUTS[n]
            pill = ctk.CTkLabel(
                inner, text=f"  {n}-leg · {mult:.0f}x  ",
                fg_color=T.BG_ELEV_2, text_color=T.TEXT_MUTED,
                corner_radius=999, font=T.FONT_SMALL,
            )
            pill.pack(side="left", padx=4)
            self._ladder[n] = pill

        self.slip_status = ctk.CTkLabel(
            inner, text="Pick 2+ legs for a slip.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        )
        self.slip_status.pack(side="right")

    # ---------------- State plumbing ----------------

    def _on_state_event(self, event: str):
        if event in ("games", "sport"):
            self.render()
        if event in ("betslip", "games", "sport"):
            self._update_payout_strip()

    def _update_payout_strip(self):
        """Update the payout strip — DFS books use the multiplier ladder;
        Kalshi falls back to fair-price parlay math because Kalshi has no
        fixed multiplier (each yes contract trades at its market price).
        """
        if self.platform == "kalshi":
            self._update_payout_strip_kalshi()
            return

        # DFS path: count team/prop legs that are PrizePicks/Underdog/demo.
        # Underdog and PrizePicks share the same power-play ladder, so we
        # bucket them together. Kalshi legs are intentionally excluded — if
        # you mix Kalshi and DFS legs in the same slip, none of the slip is
        # eligible for the multiplier and analyze_parlay correctly falls
        # through to standard parlay math.
        dfs_legs = [
            l for l in self.state.bet_slip
            if (l.market.startswith("team_") or l.market.startswith("prop_"))
            and l.bookmaker.lower() in ("prizepicks", "underdog", "demo")
        ]
        n = len(dfs_legs)

        for tier, pill in self._ladder.items():
            pill.pack(side="left", padx=4)
            if tier == n:
                pill.configure(fg_color=T.ACCENT, text_color=T.BG)
            else:
                pill.configure(fg_color=T.BG_ELEV_2, text_color=T.TEXT_MUTED)

        self._payout_label.configure(text="POWER PLAY")

        if n == 0:
            self.slip_status.configure(
                text="Pick 2+ legs for a slip.", text_color=T.TEXT_MUTED,
            )
        elif n == 1:
            self.slip_status.configure(
                text="1 leg · need at least one more.", text_color=T.WARNING,
            )
        elif n in POWER_PAYOUTS:
            mult = POWER_PAYOUTS[n]
            break_even = 1.0 / mult
            self.slip_status.configure(
                text=f"{n} legs · {mult:.0f}x payout · break-even {break_even * 100:.1f}%",
                text_color=T.ACCENT,
            )
        else:
            self.slip_status.configure(
                text=f"{n} legs · over max (6). Remove a leg to unlock payout.",
                text_color=T.NEGATIVE,
            )

    def _update_payout_strip_kalshi(self):
        """Kalshi path: hide the multiplier ladder, show combined fair price."""
        # Hide the DFS ladder pills — they don't apply to Kalshi.
        for pill in self._ladder.values():
            pill.pack_forget()

        kalshi_legs = [
            l for l in self.state.bet_slip
            if l.market.startswith("team_") and l.bookmaker.lower() == "kalshi"
        ]
        n = len(kalshi_legs)
        self._payout_label.configure(text="KALSHI YES PARLAY")

        if n == 0:
            self.slip_status.configure(
                text="Pick 2+ legs for a Kalshi parlay.", text_color=T.TEXT_MUTED,
            )
            return

        analysis = analyze_parlay(kalshi_legs)
        # Combined fair-price multiplier = product of (1/fair) per leg = combined_decimal.
        if n == 1:
            self.slip_status.configure(
                text=(
                    f"1 leg · fair price {format_american(kalshi_legs[0].price)}. "
                    "Add another for a parlay."
                ),
                text_color=T.WARNING,
            )
            return
        self.slip_status.configure(
            text=(
                f"{n} legs · combined fair price {format_american(analysis.combined_american)} "
                f"· hit {analysis.combined_prob * 100:.1f}%"
            ),
            text_color=T.ACCENT,
        )

    # ---------------- Render ----------------

    def render(self):
        for child in self.list.winfo_children():
            child.destroy()

        games = self.state.games
        self._refresh_subtitle()
        self._render_source_banner()
        self._update_payout_strip()

        if not games:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
            return
        self.empty.place_forget()

        # `platform_provider` is a closure rather than a snapshot so cards
        # built before a platform switch still pick up the new value when a
        # tile is tapped — re-rendering on every switch would be wasteful.
        for g in games:
            GamePickEmCard(
                self.list, g, self.state, self.on_add_leg,
                platform_provider=lambda: self.platform,
            ).pack(fill="x", pady=8, padx=8)

    def _render_source_banner(self):
        src = getattr(self.state, "games_source", "demo")
        if src == "live":
            self.source_pill.configure(text="LIVE", text_color=T.POSITIVE)
            self.banner.pack_forget()
        else:
            self.source_pill.configure(text="DEMO", text_color=T.TEXT_MUTED)
            self.banner.configure(
                text="  Showing demo odds. Add an Odds API key in Settings for live team lines.",
            )
            self.banner.pack(fill="x", padx=24, pady=(0, 8))


class GamePickEmCard(Card):
    """One game, three market rows (Spread / Total / Moneyline), two tiles each."""

    def __init__(
        self,
        master,
        game: Game,
        state: AppState,
        on_add_leg: Callable[[LegAnalysis], None],
        platform_provider: Callable[[], str] | None = None,
    ):
        super().__init__(master)
        self.game = game
        self.state = state
        self.on_add_leg = on_add_leg
        # Resolved at click time rather than card-build time so platform
        # switches don't require re-rendering every card.
        self.platform_provider = platform_provider or (lambda: "prizepicks")

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="x", padx=18, pady=16)

        # Game header
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

        # Three market rows
        self._render_market_row(outer, "SPREAD", [
            ("spreads", game.away_team, game.away_team),
            ("spreads", game.home_team, game.home_team),
        ])
        self._render_market_row(outer, "TOTAL", [
            ("totals", "Over", "Over"),
            ("totals", "Under", "Under"),
        ])
        self._render_market_row(outer, "MONEYLINE", [
            ("h2h", game.away_team, f"{game.away_team} to win"),
            ("h2h", game.home_team, f"{game.home_team} to win"),
        ])

    def _render_market_row(
        self,
        parent,
        label: str,
        picks: list[tuple[str, str, str]],   # (market_key, selection, display_name)
    ):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", pady=(14, 0))

        ctk.CTkLabel(
            wrap, text=label, font=T.FONT_TINY, text_color=T.TEXT_MUTED,
        ).pack(anchor="w", padx=2, pady=(0, 6))

        grid = ctk.CTkFrame(wrap, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure((0, 1), weight=1, uniform="picktile")

        for col, (market_key, selection, display_name) in enumerate(picks):
            tile = PickTile(
                grid,
                game=self.game,
                market_key=market_key,
                selection=selection,
                display_name=display_name,
                state=self.state,
                on_add_leg=self.on_add_leg,
                platform_provider=self.platform_provider,
            )
            tile.grid(row=0, column=col, sticky="ew", padx=4)


class PickTile(ctk.CTkFrame):
    """A single big tappable pick-em tile."""

    def __init__(
        self,
        master,
        *,
        game: Game,
        market_key: str,
        selection: str,
        display_name: str,
        state: AppState,
        on_add_leg: Callable[[LegAnalysis], None],
        platform_provider: Callable[[], str] | None = None,
    ):
        super().__init__(master, fg_color=T.BG_ELEV_2, corner_radius=10, height=TILE_H)
        self.grid_propagate(False)

        self.game = game
        self.market_key = market_key
        self.selection = selection
        self.state = state
        self.on_add_leg = on_add_leg
        self.platform_provider = platform_provider or (lambda: "prizepicks")

        # Derive the point + fair prob up front so the tile can render
        # informatively even before it's tapped.
        point = _consensus_point(game, market_key, selection) if market_key in ("spreads", "totals") else None
        fair = compute_fair_prob_from_game(game, market_key, selection)

        if market_key == "spreads" and point is not None:
            top_text = f"{display_name}  {_fmt_pt(point)}"
        elif market_key == "totals" and point is not None:
            top_text = f"{display_name} {point}"
        else:
            top_text = display_name

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top, text=top_text, font=T.FONT_BOLD, text_color=T.TEXT,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # Fair prob pill — colored by how far above break-even at the slip's
        # current tier. We use the 3-leg (5x → 20%) break-even as the default
        # reference since it's the most common PrizePicks tier.
        fair_color = T.POSITIVE if fair >= 0.55 else (
            T.WARNING if fair >= 0.45 else T.NEGATIVE
        ) if fair > 0 else T.TEXT_MUTED
        fair_text = format_pct(fair, 1) if fair > 0 else "—"
        Pill(
            top, fair_text, color=T.BG_ELEV_3, text_color=fair_color,
        ).pack(side="right")

        bot = ctk.CTkFrame(inner, fg_color="transparent")
        bot.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(
            bot, text="consensus fair", font=T.FONT_TINY, text_color=T.TEXT_DIM,
        ).pack(side="left")

        add_btn = ctk.CTkButton(
            bot, text="+ Add", width=70, height=24, corner_radius=6,
            fg_color=T.BG_ELEV_3, hover_color=T.ACCENT, text_color=T.TEXT,
            font=T.FONT_TINY, command=self._on_click,
        )
        add_btn.pack(side="right")

        # Whole tile tap = add (click on body, not just the button)
        for w in (self, inner, top, bot):
            w.bind("<Button-1>", lambda _e: self._on_click())

    def _on_click(self):
        leg = team_pick_to_leg(
            self.game, self.market_key, self.selection,
            platform=self.platform_provider(),
        )
        if leg is None:
            return
        self.on_add_leg(leg)


def _fmt_pt(p: float) -> str:
    return f"+{p}" if p > 0 else f"{p}"
