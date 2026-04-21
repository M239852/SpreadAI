"""Underdog Fantasy projections client.

Underdog exposes a public over/under projections feed at
`/beta/v5/over_under_lines`. We parse it into the same `PlayerProp` dataclass
that PrizePicks produces, so the downstream generator/analyzer can treat both
books uniformly — a slip built from Underdog props is indistinguishable from
one built from PrizePicks props except for the `source` field.

No auth is required to read the projections board; Underdog gates actual pick
placement, not the listing.
"""
from __future__ import annotations
import time
import requests
from typing import Any

from ..utils.storage import read_cache_json, write_cache_json
from .prizepicks_api import PlayerProp

BASE_URL = "https://api.underdogfantasy.com"

# SpreadAI sport key → Underdog sport tags (the value that shows up in
# `sport_id` on a player or match record). The /over_under_lines feed returns
# every sport in one payload, so we filter client-side by these tags.
UD_SPORT_TAGS: dict[str, tuple[str, ...]] = {
    "americanfootball_nfl":   ("NFL",),
    "basketball_nba":         ("NBA",),
    "baseball_mlb":           ("MLB",),
    "icehockey_nhl":          ("NHL",),
    "americanfootball_ncaaf": ("CFB", "NCAAF"),
    "basketball_ncaab":       ("CBB", "NCAAB"),
    "soccer_epl":             ("EPL", "SOCCER"),
    "mma_mixed_martial_arts": ("MMA", "UFC"),
}

# Book choices surfaced in the UI — kept here so the API layer owns the list
# of books SpreadAI can verify against.
BOOK_CHOICES: tuple[str, ...] = ("All books", "PrizePicks only", "Underdog only")


class UnderdogAPI:
    """Fetch Underdog Fantasy over/under lines. Cached for 5 minutes.

    Underdog returns one combined payload for every sport, so unlike the
    PrizePicks client we only make one HTTP call and filter by sport_id in
    the parser.
    """

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def fetch_props(self, sport_key: str) -> list[PlayerProp]:
        if sport_key not in UD_SPORT_TAGS:
            return []

        cache_key = f"underdog_{sport_key}.json"
        cached = read_cache_json(cache_key, max_age_seconds=300)
        if cached is not None:
            return [self._hydrate(p) for p in cached]

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; SpreadAI/1.0)",
            "Accept": "application/json",
        }

        payload = self._fetch_with_retry(headers)
        if payload is None:
            return []

        props = self._parse(payload, sport_key)
        if props:
            write_cache_json(cache_key, [self._dehydrate(p) for p in props])
        return props

    def _fetch_with_retry(self, headers: dict, max_attempts: int = 3) -> dict | None:
        backoff = 1.5
        for _ in range(max_attempts):
            try:
                r = requests.get(
                    f"{BASE_URL}/beta/v5/over_under_lines",
                    headers=headers, timeout=self.timeout,
                )
                if r.status_code == 429:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                r.raise_for_status()
                return r.json()
            except Exception:
                return None
        return None

    # ---------- Payload walk ----------

    @staticmethod
    def _parse(payload: dict[str, Any], sport_key: str) -> list[PlayerProp]:
        allowed = {t.upper() for t in UD_SPORT_TAGS.get(sport_key, ())}

        appearances = {str(a.get("id")): a for a in payload.get("appearances", []) if a.get("id")}
        players     = {str(p.get("id")): p for p in payload.get("players",     []) if p.get("id")}
        teams       = {str(t.get("id")): t for t in payload.get("teams",       []) if t.get("id")}
        matches_src = (
            payload.get("games")
            or payload.get("matches")
            or payload.get("solo_games")
            or []
        )
        matches = {str(m.get("id")): m for m in matches_src if m.get("id")}

        out: list[PlayerProp] = []
        for line in payload.get("over_under_lines", []):
            ou = line.get("over_under", {}) or {}
            astat = ou.get("appearance_stat", {}) or {}
            stat_type = astat.get("display_stat") or astat.get("stat") or ""
            app_id = str(astat.get("appearance_id", "") or "")

            appearance = appearances.get(app_id, {})
            player_id = str(appearance.get("player_id", "") or "")
            match_id = str(
                appearance.get("match_id", "")
                or appearance.get("game_id", "")
                or ""
            )
            team_id = str(appearance.get("team_id", "") or "")

            player = players.get(player_id, {})
            team = teams.get(team_id, {})
            match = matches.get(match_id, {})

            sport = (player.get("sport_id") or match.get("sport_id") or "").upper()
            if allowed and sport not in allowed:
                continue

            first = player.get("first_name", "") or ""
            last = player.get("last_name", "") or ""
            full = (first + " " + last).strip() or player.get("name", "") or "Unknown"

            try:
                line_val = float(line.get("stat_value", 0) or 0)
            except Exception:
                line_val = 0.0

            # Opposing team, inferred from home/away on the match.
            opp = ""
            try:
                home_id = str(match.get("home_team_id", "") or "")
                away_id = str(match.get("away_team_id", "") or "")
                if team_id and team_id == home_id:
                    opp = teams.get(away_id, {}).get("abbr", "")
                elif team_id and team_id == away_id:
                    opp = teams.get(home_id, {}).get("abbr", "")
            except Exception:
                pass

            prop_id = str(line.get("id", "")) or f"ud-{player_id}-{stat_type}"
            out.append(PlayerProp(
                id=prop_id,
                player_id=player_id,
                player_name=full,
                team=team.get("abbr", team.get("name", "")) or "",
                team_name=team.get("name", team.get("abbr", "")) or "",
                position=player.get("position", "") or "",
                league=sport,
                sport_key=sport_key,
                stat_type=stat_type,
                line=line_val,
                opponent=opp,
                description=ou.get("title", stat_type) or stat_type,
                start_time=match.get("scheduled_at", "") or "",
                source="Underdog",
            ))
        return out

    # ---------- cache (de)hydration ----------

    @staticmethod
    def _dehydrate(p: PlayerProp) -> dict[str, Any]:
        return p.__dict__.copy()

    @staticmethod
    def _hydrate(d: dict[str, Any]) -> PlayerProp:
        return PlayerProp(**d)


# --------------------------- Multi-book fetch --------------------------------

def fetch_props_from_books(
    sport_key: str,
    books: list[str] | None = None,
) -> list[PlayerProp]:
    """Fetch projections from every requested book and merge into one list.

    Parameters
    ----------
    sport_key : SpreadAI sport key.
    books : canonical book names to pull from — e.g. ["PrizePicks"],
            ["Underdog"], or None/both for everything we support.

    Each returned `PlayerProp` keeps its `source` field set to the book it
    came from, so downstream filtering and per-leg badging works without
    further lookups. Props that appear on both books are kept from whichever
    book returned them first (PrizePicks wins ties because it tends to have
    the fuller board), but the loser's copy is available if the first-hand
    call failed.
    """
    from .prizepicks_api import PrizePicksAPI

    wanted = {b.lower() for b in (books or ["PrizePicks", "Underdog"])}
    merged: dict[tuple, PlayerProp] = {}

    if "prizepicks" in wanted:
        try:
            for p in PrizePicksAPI().fetch_props(sport_key):
                key = (
                    (p.player_id or p.player_name).lower(),
                    (p.stat_type or "").lower(),
                    round(p.line, 2),
                )
                merged.setdefault(key, p)
        except Exception:
            pass

    if "underdog" in wanted:
        try:
            for p in UnderdogAPI().fetch_props(sport_key):
                key = (
                    (p.player_id or p.player_name).lower(),
                    (p.stat_type or "").lower(),
                    round(p.line, 2),
                )
                merged.setdefault(key, p)
        except Exception:
            pass

    return list(merged.values())


def book_from_ui_choice(choice: str) -> list[str] | None:
    """Map a UI dropdown value → canonical book names for fetching/filtering.

    "All books" → None (fetch all books, no filter).
    "PrizePicks only" / "Underdog only" → a single-entry list.
    """
    c = (choice or "").strip().lower()
    if c in ("", "all books", "all"):
        return None
    if "prizepicks" in c or "prize picks" in c:
        return ["PrizePicks"]
    if "underdog" in c:
        return ["Underdog"]
    return None
