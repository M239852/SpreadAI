"""ESPN player roster lookup + gamelog fetcher.

Gives us an athlete ID from a player's name (via team roster), then pulls their
recent game-by-game stats so we can compute means/stdevs for prop lines.
"""
from __future__ import annotations
import re
import requests
from typing import Any

from ..utils.storage import read_cache_json, write_cache_json
from .espn_api import ESPN_BASE, SPORT_PATHS, fetch_team_index

# Gamelogs live on a different host than the team/roster endpoints. The
# public site.api.espn.com/apis/site/v2 route 404s on /athletes/{id}/gamelog;
# the real gamelog feed is under site.web.api.espn.com/apis/common/v3.
GAMELOG_BASE = "https://site.web.api.espn.com/apis/common/v3/sports"


# -- Name normalization -----------------------------------------------------

_STRIP = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _STRIP.sub("", (s or "").lower())


def _tokens(s: str) -> list[str]:
    return [_norm(p) for p in re.split(r"\s+", (s or "")) if p]


# -- Roster -> athlete id ---------------------------------------------------

def _team_id_by_abbrev_or_name(sport_key: str, needle: str) -> str | None:
    idx = fetch_team_index(sport_key)
    needle_l = (needle or "").lower().strip()
    if not needle_l:
        return None
    # Try exact displayName
    if needle_l in idx:
        return idx[needle_l].get("id")
    # Try abbreviation or nickname
    for name, info in idx.items():
        if info.get("abbreviation", "").lower() == needle_l:
            return info.get("id")
        if info.get("nickname", "").lower() == needle_l:
            return info.get("id")
        if needle_l in name:
            return info.get("id")
    return None


def _fetch_team_roster(sport_key: str, team_id: str) -> list[dict[str, Any]]:
    path = SPORT_PATHS.get(sport_key)
    if not path or not team_id:
        return []
    cache_key = f"roster_{sport_key}_{team_id}.json"
    cached = read_cache_json(cache_key, max_age_seconds=3600 * 12)
    if cached is not None:
        return cached
    url = f"{ESPN_BASE}/{path[0]}/{path[1]}/teams/{team_id}/roster"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    athletes: list[dict[str, Any]] = []
    # The roster response may group by position ("athletes": [{"position":..., "items":[...]}])
    # or return a flat list.
    groups = data.get("athletes", [])
    if groups and isinstance(groups, list) and groups and isinstance(groups[0], dict) and "items" in groups[0]:
        for g in groups:
            for a in g.get("items", []) or []:
                athletes.append(_athlete_brief(a))
    else:
        for a in groups:
            athletes.append(_athlete_brief(a))
    write_cache_json(cache_key, athletes)
    return athletes


def _athlete_brief(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": a.get("id"),
        "displayName": a.get("displayName", ""),
        "firstName": a.get("firstName", ""),
        "lastName": a.get("lastName", ""),
        "position": (a.get("position") or {}).get("abbreviation", ""),
    }


def find_athlete_id(sport_key: str, player_name: str, team_hint: str | None = None) -> str | None:
    """Resolve an ESPN athlete ID from a player's display name.

    Strategy: if a team hint is provided (abbrev or name), search that roster
    first. Otherwise scan all team rosters in the league.
    """
    cache_key = f"pid_{sport_key}_{_norm(team_hint or '')}_{_norm(player_name)}.json"
    cached = read_cache_json(cache_key, max_age_seconds=3600 * 24)
    if cached is not None and cached.get("id"):
        return str(cached["id"])

    target_norm = _norm(player_name)
    target_tokens = _tokens(player_name)

    teams_to_search: list[str] = []
    if team_hint:
        tid = _team_id_by_abbrev_or_name(sport_key, team_hint)
        if tid:
            teams_to_search.append(tid)
    if not teams_to_search:
        idx = fetch_team_index(sport_key)
        teams_to_search = [info.get("id") for info in idx.values() if info.get("id")]

    for tid in teams_to_search:
        roster = _fetch_team_roster(sport_key, tid)
        for a in roster:
            name = a.get("displayName", "")
            if _norm(name) == target_norm:
                write_cache_json(cache_key, {"id": a["id"]})
                return str(a["id"])
        # Second pass: looser match (all surname tokens present)
        if target_tokens:
            last = target_tokens[-1]
            for a in roster:
                at = _tokens(a.get("displayName", ""))
                if last in at and target_tokens[0] in at:
                    write_cache_json(cache_key, {"id": a["id"]})
                    return str(a["id"])
    return None


# -- Gamelog ----------------------------------------------------------------

# Map PrizePicks `stat_type` labels onto the ESPN gamelog column headers for
# each sport. ESPN uses short uppercase codes (PTS, REB, AST, …) — exactly
# what appears in the `labels` array of /athletes/{id}/gamelog.
#
# When a value is a list of ESPN codes with length > 1, the columns are
# summed — that's how we resolve combo stats like "Pts+Rebs+Asts" and covers
# NFL's duplicated YDS/TD columns (passing block + rushing block).
STAT_ALIASES: dict[str, list[str]] = {
    # NBA
    "points": ["PTS"],
    "rebounds": ["REB"],
    "assists": ["AST"],
    "3-pt made": ["3PT"],
    "3-pointers made": ["3PT"],
    "pts+rebs+asts": ["PTS", "REB", "AST"],
    "pts+rebs": ["PTS", "REB"],
    "pts+asts": ["PTS", "AST"],
    "rebs+asts": ["REB", "AST"],
    "steals": ["STL"],
    "blocks": ["BLK"],
    "turnovers": ["TO"],
    "blks+stls": ["BLK", "STL"],
    "steals+blocks": ["BLK", "STL"],
    # NFL (labels appear twice for passing/rushing — summing covers both)
    "passing yards": ["YDS"],
    "passing tds": ["TD"],
    "passing attempts": ["ATT"],
    "pass completions": ["CMP"],
    "pass attempts": ["ATT"],
    "interceptions": ["INT"],
    "rushing yards": ["YDS"],
    "rushing attempts": ["CAR"],
    "rushing tds": ["TD"],
    "receptions": ["REC"],
    "receiving yards": ["YDS"],
    "receiving tds": ["TD"],
    "pass+rush yards": ["YDS"],
    # MLB
    "hits": ["H"],
    "home runs": ["HR"],
    "total bases": ["TB"],
    "rbis": ["RBI"],
    "hitter strikeouts": ["SO"],
    "pitcher strikeouts": ["SO"],
    "strikeouts": ["SO"],
    "walks": ["BB"],
    "stolen bases": ["SB"],
    "runs": ["R"],
    "hits+runs+rbis": ["H", "R", "RBI"],
    # NHL
    "shots": ["S"],
    "shots on goal": ["S"],
    "goals": ["G"],
    "goalie saves": ["SV"],
    "saves": ["SV"],
    "points": ["PTS"],
    "power play points": ["PPP"],
}


def _stat_keys(stat_type: str) -> list[str]:
    """Return the ESPN gamelog label codes to SUM for this PrizePicks stat."""
    key = (stat_type or "").lower().strip()
    if key in STAT_ALIASES:
        return STAT_ALIASES[key]
    # Fall through: try normalized form (e.g. "3-PT Made" -> "3ptmade")
    nkey = _norm(key)
    for k, v in STAT_ALIASES.items():
        if _norm(k) == nkey:
            return v
    return []


def fetch_recent_stat_values(
    sport_key: str,
    athlete_id: str,
    stat_type: str,
    max_games: int = 10,
) -> list[float]:
    """Return recent game-by-game values for the requested stat, newest first."""
    if not athlete_id:
        return []
    path = SPORT_PATHS.get(sport_key)
    if not path:
        return []

    cache_key = f"gamelog_{sport_key}_{athlete_id}.json"
    gamelog = read_cache_json(cache_key, max_age_seconds=3600)
    if gamelog is None:
        url = f"{GAMELOG_BASE}/{path[0]}/{path[1]}/athletes/{athlete_id}/gamelog"
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            gamelog = r.json()
        except Exception:
            return []
        write_cache_json(cache_key, gamelog)

    values = _extract_stat_values(gamelog, stat_type)
    return values[:max_games]


def _extract_stat_values(gamelog: dict[str, Any], stat_type: str) -> list[float]:
    """Walk an ESPN gamelog and return per-event values for the requested stat.

    ESPN shape: top-level `labels` (short codes like ['MIN','FG','...','PTS']),
    `events` (dict id -> {gameDate, ...}), and `seasonTypes[*].categories[*].events[*].stats`
    where `stats` is a list of strings parallel to `labels`.

    For combo stats like Pts+Rebs+Asts, we sum the indices of PTS, REB, AST.
    """
    codes = _stat_keys(stat_type)
    if not codes:
        return []

    labels = gamelog.get("labels") or gamelog.get("names") or []

    # Find every column whose label matches any of our target codes. We collect
    # ALL matching indices per code (NFL duplicates YDS/TD between passing and
    # rushing blocks — summing them yields combined yardage).
    wanted_indices: list[int] = []
    upper_labels = [str(l).upper() for l in labels]
    for code in codes:
        c = code.upper()
        for i, lbl in enumerate(upper_labels):
            if lbl == c:
                wanted_indices.append(i)
    if not wanted_indices:
        return []

    events_by_id: dict[str, dict[str, Any]] = {}
    for ev_id, ev in (gamelog.get("events") or {}).items():
        events_by_id[str(ev_id)] = ev

    out: list[tuple[str, float]] = []  # (date, value)

    for st in gamelog.get("seasonTypes", []) or []:
        # Skip preseason — prop lines are keyed to regular/postseason form.
        disp = (st.get("displayName") or "").lower()
        if "preseason" in disp:
            continue
        for cat in st.get("categories", []) or []:
            for ev in cat.get("events", []) or []:
                stats = ev.get("stats")
                if not isinstance(stats, list):
                    continue
                total = 0.0
                got_any = False
                for idx in wanted_indices:
                    if idx >= len(stats):
                        continue
                    raw = stats[idx]
                    try:
                        # "12-23" (shot splits) -> take the made count
                        if isinstance(raw, str) and "-" in raw:
                            raw = raw.split("-", 1)[0]
                        total += float(raw)
                        got_any = True
                    except Exception:
                        continue
                if not got_any:
                    continue
                ev_id = str(ev.get("eventId", ev.get("id", "")))
                date = events_by_id.get(ev_id, {}).get("gameDate", "")
                out.append((date, total))

    out.sort(key=lambda x: x[0] or "", reverse=True)
    return [v for _, v in out]


def season_average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def sample_stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return var ** 0.5
