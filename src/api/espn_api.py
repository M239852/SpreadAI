from __future__ import annotations
import requests
from typing import Any

from ..utils.storage import read_cache_json, write_cache_json

# ESPN's public (unofficial) site API. No key required.
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

SPORT_PATHS: dict[str, tuple[str, str]] = {
    "americanfootball_nfl": ("football", "nfl"),
    "americanfootball_ncaaf": ("football", "college-football"),
    "basketball_nba": ("basketball", "nba"),
    "basketball_ncaab": ("basketball", "mens-college-basketball"),
    "baseball_mlb": ("baseball", "mlb"),
    "icehockey_nhl": ("hockey", "nhl"),
    "soccer_epl": ("soccer", "eng.1"),
    "mma_mixed_martial_arts": ("mma", "ufc"),
}


def _sport_path(sport_key: str) -> tuple[str, str] | None:
    return SPORT_PATHS.get(sport_key)


def fetch_news(sport_key: str, limit: int = 15) -> list[dict[str, Any]]:
    path = _sport_path(sport_key)
    if not path:
        return []
    cache_key = f"news_{sport_key}.json"
    cached = read_cache_json(cache_key, max_age_seconds=600)
    if cached is not None:
        return cached[:limit]
    url = f"{ESPN_BASE}/{path[0]}/{path[1]}/news"
    try:
        r = requests.get(url, params={"limit": limit}, timeout=15)
        r.raise_for_status()
        articles = r.json().get("articles", [])
    except Exception:
        return []
    trimmed = [
        {
            "headline": a.get("headline", ""),
            "description": a.get("description", ""),
            "published": a.get("published", ""),
            "link": ((a.get("links") or {}).get("web") or {}).get("href", ""),
            "type": a.get("type", ""),
        }
        for a in articles
    ]
    write_cache_json(cache_key, trimmed)
    return trimmed[:limit]


def fetch_scoreboard(sport_key: str) -> dict[str, Any]:
    path = _sport_path(sport_key)
    if not path:
        return {}
    cache_key = f"scoreboard_{sport_key}.json"
    cached = read_cache_json(cache_key, max_age_seconds=300)
    if cached is not None:
        return cached
    url = f"{ESPN_BASE}/{path[0]}/{path[1]}/scoreboard"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {}
    write_cache_json(cache_key, data)
    return data


def fetch_team_index(sport_key: str) -> dict[str, dict[str, Any]]:
    """Return a map keyed by lowercased team display name."""
    path = _sport_path(sport_key)
    if not path:
        return {}
    cache_key = f"teams_{sport_key}.json"
    cached = read_cache_json(cache_key, max_age_seconds=3600 * 12)
    if cached is not None:
        return cached
    url = f"{ESPN_BASE}/{path[0]}/{path[1]}/teams"
    try:
        r = requests.get(url, params={"limit": 400}, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        leagues = data.get("sports", [{}])[0].get("leagues", [{}])[0]
        for t in leagues.get("teams", []):
            team = t.get("team", {})
            name = team.get("displayName", "")
            if not name:
                continue
            out[name.lower()] = {
                "id": team.get("id"),
                "displayName": name,
                "abbreviation": team.get("abbreviation", ""),
                "location": team.get("location", ""),
                "nickname": team.get("nickname", ""),
                "logo": (team.get("logos") or [{}])[0].get("href", ""),
            }
    except Exception:
        pass
    write_cache_json(cache_key, out)
    return out


def _team_id(sport_key: str, team_name: str) -> str | None:
    idx = fetch_team_index(sport_key)
    key = team_name.lower()
    if key in idx:
        return idx[key].get("id")
    # fuzzy: match on last word (nickname) or location
    for name, info in idx.items():
        if team_name.lower() in name or name in team_name.lower():
            return info.get("id")
        if info.get("nickname", "").lower() in team_name.lower():
            return info.get("id")
    return None


def fetch_team_record_and_form(sport_key: str, team_name: str) -> dict[str, Any]:
    """Fetch win-loss record and last-5 form for a team."""
    path = _sport_path(sport_key)
    if not path:
        return {}
    tid = _team_id(sport_key, team_name)
    if not tid:
        return {}
    cache_key = f"team_{sport_key}_{tid}.json"
    cached = read_cache_json(cache_key, max_age_seconds=3600)
    if cached is not None:
        return cached

    result: dict[str, Any] = {"team": team_name}

    # Record
    try:
        url = f"{ESPN_BASE}/{path[0]}/{path[1]}/teams/{tid}"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        team = r.json().get("team", {})
        records = team.get("record", {}).get("items", [])
        if records:
            summary = records[0].get("summary", "")
            result["record"] = summary
            stats = {s.get("name"): s.get("value") for s in records[0].get("stats", [])}
            result["win_pct"] = stats.get("winPercent")
            result["points_for"] = stats.get("pointsFor") or stats.get("avgPointsFor")
            result["points_against"] = stats.get("pointsAgainst") or stats.get("avgPointsAgainst")
    except Exception:
        pass

    # Recent schedule / last 5 results
    try:
        url = f"{ESPN_BASE}/{path[0]}/{path[1]}/teams/{tid}/schedule"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        events = r.json().get("events", [])
        last5: list[dict[str, Any]] = []
        wins = losses = 0
        for ev in events:
            comp = (ev.get("competitions") or [{}])[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue
            status = (ev.get("status") or {}).get("type", {}).get("completed", False)
            if not status:
                continue
            my = next((c for c in competitors if str(c.get("id")) == str(tid)), None)
            opp = next((c for c in competitors if str(c.get("id")) != str(tid)), None)
            if not my or not opp:
                continue
            won = my.get("winner", False)
            if won:
                wins += 1
            else:
                losses += 1
            last5.append({
                "date": ev.get("date", ""),
                "opponent": opp.get("team", {}).get("displayName", ""),
                "home": my.get("homeAway") == "home",
                "score": f"{my.get('score','')}-{opp.get('score','')}",
                "result": "W" if won else "L",
            })
        last5 = sorted(last5, key=lambda x: x["date"], reverse=True)[:5]
        result["last5"] = last5
        if last5:
            recent_w = sum(1 for g in last5 if g["result"] == "W")
            result["last5_summary"] = f"{recent_w}-{len(last5) - recent_w}"
    except Exception:
        pass

    write_cache_json(cache_key, result)
    return result


def fetch_team_injuries(sport_key: str, team_name: str) -> list[dict[str, Any]]:
    path = _sport_path(sport_key)
    if not path:
        return []
    tid = _team_id(sport_key, team_name)
    if not tid:
        return []
    cache_key = f"injuries_{sport_key}_{tid}.json"
    cached = read_cache_json(cache_key, max_age_seconds=1800)
    if cached is not None:
        return cached
    url = f"{ESPN_BASE}/{path[0]}/{path[1]}/teams/{tid}/injuries"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for inj in data.get("injuries", []):
        athlete = inj.get("athlete", {}) or {}
        out.append({
            "player": athlete.get("displayName", inj.get("displayName", "")),
            "position": (athlete.get("position") or {}).get("abbreviation", ""),
            "status": inj.get("status", ""),
            "description": (inj.get("details") or {}).get("type", "")
            or inj.get("shortComment", "")
            or inj.get("longComment", ""),
            "date": inj.get("date", ""),
        })
    write_cache_json(cache_key, out)
    return out


def fetch_head_to_head(
    sport_key: str, team_a: str, team_b: str, limit: int = 6
) -> list[dict[str, Any]]:
    """Return recent completed games between team_a and team_b, newest first.

    Looks at team_a's schedule (regular season + postseason) and filters for
    events where the opponent is team_b. Works for any ESPN-supported league.
    """
    path = _sport_path(sport_key)
    if not path:
        return []
    tid_a = _team_id(sport_key, team_a)
    tid_b = _team_id(sport_key, team_b)
    if not tid_a or not tid_b:
        return []

    cache_key = f"h2h_{sport_key}_{tid_a}_{tid_b}.json"
    cached = read_cache_json(cache_key, max_age_seconds=3600 * 6)
    if cached is not None:
        return cached[:limit]

    out: list[dict[str, Any]] = []

    # ESPN's /schedule endpoint returns recent+upcoming events.  We also try the
    # prior season to capture a longer history.
    schedule_urls = [
        f"{ESPN_BASE}/{path[0]}/{path[1]}/teams/{tid_a}/schedule",
    ]
    try:
        from datetime import datetime
        yr = datetime.utcnow().year
        for season in (yr, yr - 1, yr - 2):
            schedule_urls.append(
                f"{ESPN_BASE}/{path[0]}/{path[1]}/teams/{tid_a}/schedule?season={season}"
            )
    except Exception:
        pass

    seen_ids: set[str] = set()
    for url in schedule_urls:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                continue
            events = r.json().get("events", [])
        except Exception:
            continue
        for ev in events:
            ev_id = str(ev.get("id", ""))
            if ev_id in seen_ids:
                continue
            comp = (ev.get("competitions") or [{}])[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue
            ids = {str(c.get("id")) for c in competitors}
            if str(tid_b) not in ids:
                continue
            status = (ev.get("status") or {}).get("type", {}).get("completed", False)
            if not status:
                continue
            me = next((c for c in competitors if str(c.get("id")) == str(tid_a)), None)
            opp = next((c for c in competitors if str(c.get("id")) == str(tid_b)), None)
            if not me or not opp:
                continue
            seen_ids.add(ev_id)
            won = bool(me.get("winner", False))
            out.append({
                "date": ev.get("date", ""),
                "home": me.get("homeAway") == "home",
                "opponent": team_b,
                "score": f"{me.get('score','')}-{opp.get('score','')}",
                "result": "W" if won else "L",
                "season": ev.get("season", {}).get("year", ""),
            })

    out = sorted(out, key=lambda x: x.get("date", ""), reverse=True)[:limit]
    write_cache_json(cache_key, out)
    return out


def head_to_head_summary(h2h: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn a H2H list into wins/losses from team_a's perspective + a signed bias.

    `bias` is in [-1, 1]: +1 means team_a dominates, -1 means team_b dominates.
    """
    if not h2h:
        return {"played": 0, "wins_a": 0, "wins_b": 0, "bias": 0.0, "record": ""}
    wins_a = sum(1 for g in h2h if g.get("result") == "W")
    wins_b = len(h2h) - wins_a
    bias = (wins_a - wins_b) / len(h2h)
    return {
        "played": len(h2h),
        "wins_a": wins_a,
        "wins_b": wins_b,
        "bias": bias,
        "record": f"{wins_a}-{wins_b}",
    }


SEVERITY_WEIGHT: dict[str, float] = {
    "out": 1.0,
    "injured reserve": 1.0,
    "suspended": 1.0,
    "doubtful": 0.75,
    "questionable": 0.4,
    "probable": 0.15,
    "day-to-day": 0.25,
    "game-time decision": 0.4,
}


def injury_impact_score(injuries: list[dict[str, Any]]) -> float:
    """A rough 0..1 score for how much injuries may hurt a team.

    Weights by status severity. Positions deemed high-impact (QB, PG, C, Pitcher)
    are weighted more heavily.
    """
    if not injuries:
        return 0.0
    high_impact_pos = {"QB", "PG", "SG", "C", "G", "F", "SP", "CP", "RP"}
    total = 0.0
    for inj in injuries:
        status = (inj.get("status") or "").strip().lower()
        weight = SEVERITY_WEIGHT.get(status, 0.1)
        if (inj.get("position") or "").upper() in high_impact_pos:
            weight *= 2.0
        total += weight
    # saturate to 1.0
    return min(1.0, total / 5.0)
