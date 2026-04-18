"""PrizePicks projections client.

PrizePicks exposes a public JSON projections feed at /projections. No auth is
required. We parse the JSON:API-style payload (data + included) into flat
PlayerProp dataclasses, caching the raw response for a short window.
"""
from __future__ import annotations
import time
import requests
from dataclasses import dataclass, field
from typing import Any

from ..utils.storage import read_cache_json, write_cache_json

BASE_URL = "https://api.prizepicks.com"

# PrizePicks internal league IDs. Multiple IDs per sport let us cover regular
# season, postseason, series/futures, and 1H/1Q derivatives — whichever is
# currently active. Ordered by typical volume.
PP_LEAGUE_IDS: dict[str, list[int]] = {
    "americanfootball_nfl":  [9, 163, 44, 35, 25, 152, 245],   # NFL, NFLSZN, NFLP, 1H, 2H, 4Q, 1Q
    "basketball_nba":        [7, 250, 237, 192, 84, 173, 149], # NBA, SERIES, NBAP, 1Q, 1H, NBASZN, 4Q
    "baseball_mlb":          [2, 231, 190, 261, 41, 43],       # MLB, MLBLIVE, MLBSZN, SZN2, HRDERBY, MLBST
    "icehockey_nhl":         [8, 227, 282, 236, 251],          # NHL, NHL1P, NHLP, NHLSZN, NHL SERIES
    "americanfootball_ncaaf":[15, 150, 153, 172, 184],         # CFB, 2H, 4Q, CFBSZN, 1H
    "basketball_ncaab":      [20, 155, 189, 290, 176, 272],    # CBB, 2H, NCAAB, 1H, WCBB, WCBB2H
    "soccer_epl":            [82, 242, 243, 262, 241, 175, 287], # SOCCER, 1H, 2H, SZN, WC, WEURO, EURO
    "mma_mixed_martial_arts":[12, 42],                          # MMA, BOXING
}

# PrizePicks "power play" pick-em payout multipliers (approximate; varies by
# promos). These let us reason about DFS-style EV on a constructed slip.
POWER_PAYOUTS: dict[int, float] = {
    2: 3.0,
    3: 5.0,
    4: 10.0,
    5: 20.0,
    6: 25.0,
}


@dataclass
class PlayerProp:
    id: str
    player_id: str
    player_name: str
    team: str
    team_name: str
    position: str
    league: str              # e.g. "NBA"
    sport_key: str           # SpreadAI sport key
    stat_type: str           # "Points", "Rebounds+Assists", "Passing Yards", ...
    line: float              # the projection line
    opponent: str = ""
    description: str = ""
    start_time: str = ""
    source: str = "PrizePicks"
    market_multiplier: float | None = None  # optional (for discounted/boost lines)
    extra: dict[str, Any] = field(default_factory=dict)


def sport_label_from_league_id(league_id: int) -> str:
    for sk, lids in PP_LEAGUE_IDS.items():
        if league_id in lids:
            return sk
    return ""


class PrizePicksAPI:
    """Fetch PrizePicks projections for a given sport. Cached for 5 minutes.

    A sport can map to several PrizePicks leagues (regular season, postseason,
    series, 1H/1Q variants). We fetch each in turn, merge by projection id,
    and stop early once we have a healthy slate so we don't hammer the API
    during an active season.
    """

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def fetch_props(self, sport_key: str, per_page: int = 250) -> list[PlayerProp]:
        league_ids = PP_LEAGUE_IDS.get(sport_key)
        if not league_ids:
            return []

        cache_key = f"prizepicks_{sport_key}.json"
        cached = read_cache_json(cache_key, max_age_seconds=300)
        if cached is not None:
            return [self._hydrate(p) for p in cached]

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; SpreadAI/1.0)",
            "Accept": "application/json",
        }

        merged: dict[str, PlayerProp] = {}
        for i, lid in enumerate(league_ids):
            # PrizePicks aggressively rate-limits repeated /projections hits.
            # Pace requests when walking the derivative leagues.
            if i > 0:
                time.sleep(1.2)

            params = {"league_id": lid, "per_page": per_page, "single_stat": "true"}
            payload = self._fetch_with_retry(params, headers)
            if payload is None:
                continue

            for p in self._parse(payload, sport_key):
                if p.id and p.id not in merged:
                    merged[p.id] = p

            # Stop once the primary (or any) league gives us a real slate —
            # the derivatives (1H/1Q/series) are only fallbacks for offseason.
            if len(merged) >= 40:
                break

        props = list(merged.values())
        write_cache_json(cache_key, [self._dehydrate(p) for p in props])
        return props

    def _fetch_with_retry(self, params: dict, headers: dict, max_attempts: int = 3) -> dict | None:
        backoff = 1.5
        for attempt in range(max_attempts):
            try:
                r = requests.get(f"{BASE_URL}/projections", params=params, headers=headers, timeout=self.timeout)
                if r.status_code == 429:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                r.raise_for_status()
                return r.json()
            except Exception:
                return None
        return None

    # ---------- JSON:API parsing ----------

    @staticmethod
    def _parse(payload: dict[str, Any], sport_key: str) -> list[PlayerProp]:
        included_by = {}
        for inc in payload.get("included", []):
            key = (inc.get("type"), str(inc.get("id")))
            included_by[key] = inc

        out: list[PlayerProp] = []
        for item in payload.get("data", []):
            if item.get("type") != "projection":
                continue
            attrs = item.get("attributes", {}) or {}
            rels = item.get("relationships", {}) or {}

            player_ref = ((rels.get("new_player") or {}).get("data")) or {}
            player_id = str(player_ref.get("id", ""))
            player = included_by.get(("new_player", player_id), {}).get("attributes", {}) or {}

            league_ref = ((rels.get("league") or {}).get("data")) or {}
            league_id = str(league_ref.get("id", ""))
            league = included_by.get(("league", league_id), {}).get("attributes", {}) or {}

            line = attrs.get("line_score")
            try:
                line_val = float(line) if line is not None else 0.0
            except Exception:
                line_val = 0.0

            out.append(PlayerProp(
                id=str(item.get("id", "")),
                player_id=player_id,
                player_name=player.get("name", attrs.get("description", "Unknown")),
                team=player.get("team", ""),
                team_name=player.get("team_name", player.get("team", "")),
                position=player.get("position", ""),
                league=league.get("name", ""),
                sport_key=sport_key,
                stat_type=attrs.get("stat_type", ""),
                line=line_val,
                opponent=attrs.get("description", ""),
                description=attrs.get("description", ""),
                start_time=attrs.get("start_time", ""),
                source="PrizePicks",
            ))
        return out

    # ---------- cache (de)hydration ----------

    @staticmethod
    def _dehydrate(p: PlayerProp) -> dict[str, Any]:
        return p.__dict__.copy()

    @staticmethod
    def _hydrate(d: dict[str, Any]) -> PlayerProp:
        return PlayerProp(**d)
