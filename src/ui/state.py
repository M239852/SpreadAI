"""Shared app state: bet slip, current games, etc."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..analysis.probability import LegAnalysis
from ..api.odds_api import Game


@dataclass
class AppState:
    config: dict[str, Any] = field(default_factory=dict)
    sport_key: str = "americanfootball_nfl"
    games: list[Game] = field(default_factory=list)
    games_source: str = "demo"   # "live" | "demo" — which feed filled `games`
    props_source: str = "demo"   # "live" | "demo" — which feed filled player props
    selected_game_id: str | None = None
    bet_slip: list[LegAnalysis] = field(default_factory=list)
    stake: float = 10.0
    last_error: str = ""

    _observers: list[Callable[[str], None]] = field(default_factory=list)

    def subscribe(self, fn: Callable[[str], None]):
        self._observers.append(fn)

    def notify(self, event: str):
        for fn in list(self._observers):
            try:
                fn(event)
            except Exception:
                # Never let one view's failure block the others, but never
                # hide it either — a silent failure looks like a stuck screen.
                logging.getLogger("spreadai.state").exception(
                    "observer %r failed on event %r", getattr(fn, "__qualname__", fn), event)

    def add_leg(self, leg: LegAnalysis):
        for existing in self.bet_slip:
            if existing.game_id == leg.game_id and existing.market == leg.market and existing.selection == leg.selection:
                return
        self.bet_slip.append(leg)
        self.notify("betslip")

    def remove_leg(self, leg: LegAnalysis):
        self.bet_slip = [l for l in self.bet_slip if not (
            l.game_id == leg.game_id and l.market == leg.market and l.selection == leg.selection
        )]
        self.notify("betslip")

    def clear_slip(self):
        self.bet_slip.clear()
        self.notify("betslip")

    def find_game(self, game_id: str) -> Game | None:
        for g in self.games:
            if g.id == game_id:
                return g
        return None
