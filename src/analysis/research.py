"""Game research (ESPN) → adjustment factors for the probability model."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..api import espn_api
from ..api.odds_api import Game
from .probability import (
    AdjustmentFactor,
    injury_factor,
    form_factor,
    home_field_factor,
    rest_factor,
)


@dataclass
class TeamResearch:
    name: str
    record: str = ""
    win_pct: float | None = None
    last5_summary: str = ""
    last5: list[dict[str, Any]] = field(default_factory=list)
    injuries: list[dict[str, Any]] = field(default_factory=list)
    injury_impact: float = 0.0
    days_rest: float | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class GameResearch:
    game_id: str
    sport_key: str
    matchup: str
    home: TeamResearch
    away: TeamResearch
    news: list[dict[str, Any]] = field(default_factory=list)
    # Head-to-head: list is from the HOME team's perspective (W/L)
    h2h: list[dict[str, Any]] = field(default_factory=list)
    h2h_summary: dict[str, Any] = field(default_factory=dict)


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _days_rest(last5: list[dict[str, Any]], commence_time: str) -> float | None:
    """Days between the team's most recent completed game and this game."""
    if not last5:
        return None
    last = _parse_iso(last5[0].get("date", ""))
    start = _parse_iso(commence_time) or datetime.now(timezone.utc)
    if last is None:
        return None
    delta = (start - last).total_seconds() / 86400.0
    if delta < 0 or delta > 30:
        return None
    return round(delta)


def _team_research(game: Game, name: str) -> TeamResearch:
    rec = espn_api.fetch_team_record_and_form(game.sport_key, name) or {}
    inj = espn_api.fetch_team_injuries(game.sport_key, name) or []
    win_pct = rec.get("win_pct")
    try:
        win_pct = float(win_pct) if win_pct is not None else None
        if win_pct is not None and win_pct > 1.0:
            win_pct /= 100.0
    except (TypeError, ValueError):
        win_pct = None
    last5 = rec.get("last5", []) or []
    return TeamResearch(
        name=name,
        record=rec.get("record", ""),
        win_pct=win_pct,
        last5_summary=rec.get("last5_summary", ""),
        last5=last5,
        injuries=inj,
        injury_impact=espn_api.injury_impact_score(inj),
        days_rest=_days_rest(last5, game.commence_time),
    )


def empty_research(game: Game) -> GameResearch:
    return GameResearch(
        game_id=game.id, sport_key=game.sport_key,
        matchup=f"{game.away_team} @ {game.home_team}",
        home=TeamResearch(name=game.home_team),
        away=TeamResearch(name=game.away_team),
    )


def research_game(game: Game) -> GameResearch:
    home = _team_research(game, game.home_team)
    away = _team_research(game, game.away_team)
    news = espn_api.fetch_news(game.sport_key, limit=12)
    h2h = espn_api.fetch_head_to_head(game.sport_key, game.home_team, game.away_team, limit=6)
    h2h_summary = espn_api.head_to_head_summary(h2h)

    def _team_relevant(article: dict[str, Any]) -> bool:
        text = (article.get("headline", "") + " " + article.get("description", "")).lower()
        for token in (game.home_team, game.away_team):
            parts = token.lower().split()
            if any(p in text for p in parts if len(p) > 3):
                return True
        return False

    relevant_news = [a for a in news if _team_relevant(a)] or news[:6]

    return GameResearch(
        game_id=game.id,
        sport_key=game.sport_key,
        matchup=f"{game.away_team} @ {game.home_team}",
        home=home,
        away=away,
        news=relevant_news,
        h2h=h2h,
        h2h_summary=h2h_summary,
    )


def h2h_factor(research: GameResearch, favors_home: bool) -> AdjustmentFactor | None:
    """Head-to-head history is a weak, heavily-shrunk signal.

    `research.h2h_summary['bias']` is from the HOME team's perspective.
    """
    summary = research.h2h_summary or {}
    played = int(summary.get("played") or 0)
    if played < 2:
        return None
    bias = float(summary.get("bias") or 0.0)
    sign = 1.0 if favors_home else -1.0
    label = (
        f"H2H last {played}: {summary.get('record', '')} "
        f"({research.home.name} vs {research.away.name})"
    )
    return AdjustmentFactor(
        name="Head-to-head",
        weight=sign * bias,
        description=label,
        scale=0.15,
        confidence=0.5 * min(1.0, played / 8.0),
        category="history",
    )


def build_factors_for_selection(
    game: Game,
    research: GameResearch,
    market_key: str,
    selection: str,
) -> list[AdjustmentFactor]:
    """Translate team research into AdjustmentFactors for a specific selection."""
    factors: list[AdjustmentFactor] = []

    favors_home: bool | None
    if market_key in ("h2h", "spreads"):
        if selection == game.home_team:
            favors_home = True
        elif selection == game.away_team:
            favors_home = False
        else:
            favors_home = None
    else:
        favors_home = None

    if favors_home is None and market_key != "totals":
        return factors

    if favors_home is not None:
        mine = research.home if favors_home else research.away
        foe = research.away if favors_home else research.home

        # Context only — priced into the line, zero scale.
        factors.append(home_field_factor(favors_home, game.sport_key))

        # Injuries
        if mine.injury_impact > 0.02:
            factors.append(injury_factor(mine.injury_impact, mine.name, favors_outcome=False))
        if foe.injury_impact > 0.02:
            factors.append(injury_factor(foe.injury_impact, foe.name, favors_outcome=True))

        # Rest / schedule
        f = rest_factor(mine.days_rest, mine.name, favors_outcome=True, sport_key=game.sport_key)
        if f:
            factors.append(f)
        f = rest_factor(foe.days_rest, foe.name, favors_outcome=False, sport_key=game.sport_key)
        if f:
            factors.append(f)

        # Head-to-head
        f = h2h_factor(research, favors_home=favors_home)
        if f:
            factors.append(f)

        # Recent form relative to baseline
        f = form_factor({"last5": mine.last5, "win_pct": mine.win_pct}, mine.name, favors_outcome=True)
        if f:
            factors.append(f)
        f = form_factor({"last5": foe.last5, "win_pct": foe.win_pct}, foe.name, favors_outcome=False)
        if f:
            factors.append(f)
        return factors

    # Totals: heavy injuries on either side lean under; back-to-backs lean under too.
    total_impact = (research.home.injury_impact + research.away.injury_impact) / 2.0
    is_over = selection.lower().startswith("over")
    if total_impact > 0.05:
        factors.append(AdjustmentFactor(
            name="Injury load (total)",
            weight=(-1.0 if is_over else 1.0) * total_impact,
            description=f"Average injury impact across teams: {total_impact:.2f}",
            scale=0.30,
            confidence=0.6,
            category="injuries",
        ))
    tired = [t for t in (research.home, research.away) if t.days_rest is not None and t.days_rest <= 1.0]
    if tired:
        f = rest_factor(1.0, " & ".join(t.name for t in tired), favors_outcome=not is_over, sport_key=game.sport_key)
        if f:
            f.name = "Rest (total)"
            factors.append(f)
    return factors
