from __future__ import annotations
import requests
from dataclasses import dataclass, field
from typing import Any

from ..utils.storage import read_cache_json, write_cache_json

BASE_URL = "https://api.the-odds-api.com/v4"

SPORT_LABELS: dict[str, str] = {
    "americanfootball_nfl": "NFL",
    "americanfootball_ncaaf": "NCAAF",
    "basketball_nba": "NBA",
    "basketball_ncaab": "NCAAB",
    "baseball_mlb": "MLB",
    "icehockey_nhl": "NHL",
    "soccer_epl": "EPL",
    "mma_mixed_martial_arts": "MMA",
}


@dataclass
class Outcome:
    name: str
    price: int  # American odds
    point: float | None = None


@dataclass
class Market:
    key: str  # h2h | spreads | totals
    outcomes: list[Outcome] = field(default_factory=list)


@dataclass
class Bookmaker:
    key: str
    title: str
    last_update: str
    markets: list[Market] = field(default_factory=list)

    def market(self, key: str) -> Market | None:
        for m in self.markets:
            if m.key == key:
                return m
        return None


@dataclass
class Game:
    id: str
    sport_key: str
    sport_title: str
    commence_time: str
    home_team: str
    away_team: str
    bookmakers: list[Bookmaker] = field(default_factory=list)

    def best_price(
        self,
        market_key: str,
        team_or_name: str,
        bookmaker_filter: str | None = None,
    ) -> tuple[Bookmaker, Outcome] | None:
        """Find the bookmaker offering the highest price for a specific outcome.

        If `bookmaker_filter` is set (matching bookmaker key OR title, case-insensitive),
        only prices from that book are considered — this lets callers build
        single-book slips instead of mixing lines across books.
        """
        target = (bookmaker_filter or "").strip().lower()
        best: tuple[Bookmaker, Outcome] | None = None
        for bk in self.bookmakers:
            if target and target not in (bk.key.lower(), bk.title.lower()):
                continue
            mk = bk.market(market_key)
            if not mk:
                continue
            for o in mk.outcomes:
                if o.name.lower() != team_or_name.lower():
                    continue
                if best is None or o.price > best[1].price:
                    best = (bk, o)
        return best

    def consensus_prices(self, market_key: str, outcome_name: str) -> list[int]:
        out: list[int] = []
        for bk in self.bookmakers:
            mk = bk.market(market_key)
            if not mk:
                continue
            for o in mk.outcomes:
                if o.name.lower() == outcome_name.lower():
                    out.append(o.price)
        return out


class OddsAPI:
    """Client for the-odds-api.com v4."""

    def __init__(self, api_key: str | None):
        self.api_key = (api_key or "").strip()

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    def list_sports(self) -> list[dict[str, Any]]:
        if not self.has_key:
            return []
        r = requests.get(f"{BASE_URL}/sports", params={"apiKey": self.api_key}, timeout=15)
        r.raise_for_status()
        return r.json()

    def get_odds(
        self,
        sport_key: str,
        regions: str = "us",
        markets: str = "h2h,spreads,totals",
        odds_format: str = "american",
        use_cache: bool = True,
    ) -> list[Game]:
        if not self.has_key:
            raise RuntimeError("Odds API key is not configured.")

        cache_key = f"odds_{sport_key}_{regions}_{markets}.json"
        if use_cache:
            cached = read_cache_json(cache_key, max_age_seconds=120)
            if cached is not None:
                return [self._parse_game(g) for g in cached]

        url = f"{BASE_URL}/sports/{sport_key}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": odds_format,
            "dateFormat": "iso",
        }
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        write_cache_json(cache_key, data)
        return [self._parse_game(g) for g in data]

    @staticmethod
    def _parse_game(g: dict[str, Any]) -> Game:
        bookmakers: list[Bookmaker] = []
        for bk in g.get("bookmakers", []):
            markets: list[Market] = []
            for m in bk.get("markets", []):
                outcomes = [
                    Outcome(
                        name=o.get("name", ""),
                        price=int(o.get("price", 0)),
                        point=o.get("point"),
                    )
                    for o in m.get("outcomes", [])
                ]
                markets.append(Market(key=m.get("key", ""), outcomes=outcomes))
            bookmakers.append(Bookmaker(
                key=bk.get("key", ""),
                title=bk.get("title", ""),
                last_update=bk.get("last_update", ""),
                markets=markets,
            ))
        return Game(
            id=g.get("id", ""),
            sport_key=g.get("sport_key", ""),
            sport_title=g.get("sport_title", ""),
            commence_time=g.get("commence_time", ""),
            home_team=g.get("home_team", ""),
            away_team=g.get("away_team", ""),
            bookmakers=bookmakers,
        )
