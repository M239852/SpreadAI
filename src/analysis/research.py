from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from ..api import espn_api
from ..api.odds_api import Game
from .probability import (
    AdjustmentFactor,
    injury_factor,
    form_factor,
    home_field_factor,
)


@dataclass
class TeamResearch:
    name: str
    record: str = ""
    last5_summary: str = ""
    last5: list[dict[str, Any]] = field(default_factory=list)
    injuries: list[dict[str, Any]] = field(default_factory=list)
    injury_impact: float = 0.0
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


def research_game(game: Game) -> GameResearch:
    home_rec = espn_api.fetch_team_record_and_form(game.sport_key, game.home_team) or {}
    away_rec = espn_api.fetch_team_record_and_form(game.sport_key, game.away_team) or {}
    home_inj = espn_api.fetch_team_injuries(game.sport_key, game.home_team) or []
    away_inj = espn_api.fetch_team_injuries(game.sport_key, game.away_team) or []
    news = espn_api.fetch_news(game.sport_key, limit=12)
    h2h = espn_api.fetch_head_to_head(game.sport_key, game.home_team, game.away_team, limit=6)
    h2h_summary = espn_api.head_to_head_summary(h2h)

    # filter news mentioning either team
    def _team_relevant(article: dict[str, Any]) -> bool:
        text = (article.get("headline", "") + " " + article.get("description", "")).lower()
        for token in (game.home_team, game.away_team):
            parts = token.lower().split()
            if any(p in text for p in parts if len(p) > 3):
                return True
        return False

    relevant_news = [a for a in news if _team_relevant(a)] or news[:6]

    home = TeamResearch(
        name=game.home_team,
        record=home_rec.get("record", ""),
        last5_summary=home_rec.get("last5_summary", ""),
        last5=home_rec.get("last5", []),
        injuries=home_inj,
        injury_impact=espn_api.injury_impact_score(home_inj),
    )
    away = TeamResearch(
        name=game.away_team,
        record=away_rec.get("record", ""),
        last5_summary=away_rec.get("last5_summary", ""),
        last5=away_rec.get("last5", []),
        injuries=away_inj,
        injury_impact=espn_api.injury_impact_score(away_inj),
    )
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
    """Head-to-head bias becomes a mild adjustment factor.

    `research.h2h_summary['bias']` is from the HOME team's perspective.
    """
    summary = research.h2h_summary or {}
    played = int(summary.get("played") or 0)
    if played < 2:
        return None
    bias = float(summary.get("bias") or 0.0)  # home-team perspective
    sign = 1.0 if favors_home else -1.0
    weight = sign * bias * 0.7  # dampened
    label = (
        f"H2H last {played}: "
        f"{summary.get('record', '')} "
        f"({research.home.name} vs {research.away.name})"
    )
    return AdjustmentFactor(
        name="Head-to-head",
        weight=weight,
        description=label,
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
        # Totals: no team bias; skip directional factors.
        favors_home = None

    # Home field (directional)
    if favors_home is True:
        factors.append(home_field_factor(True, game.sport_key))
    elif favors_home is False:
        factors.append(home_field_factor(False, game.sport_key))

    # Injuries
    if favors_home is True:
        if research.home.injury_impact > 0.02:
            factors.append(injury_factor(research.home.injury_impact, research.home.name, favors_outcome=False))
        if research.away.injury_impact > 0.02:
            factors.append(injury_factor(research.away.injury_impact, research.away.name, favors_outcome=True))
    elif favors_home is False:
        if research.away.injury_impact > 0.02:
            factors.append(injury_factor(research.away.injury_impact, research.away.name, favors_outcome=False))
        if research.home.injury_impact > 0.02:
            factors.append(injury_factor(research.home.injury_impact, research.home.name, favors_outcome=True))
    else:
        # Totals: heavy injuries on either side slightly favor the under
        total_impact = (research.home.injury_impact + research.away.injury_impact) / 2.0
        if total_impact > 0.05:
            favors = not selection.lower().startswith("over")
            sign = 1.0 if favors else -1.0
            factors.append(AdjustmentFactor(
                name="Injury load (total)",
                weight=sign * total_impact * 0.6,
                description=f"Average injury impact across teams: {total_impact:.2f}",
            ))

    # Head-to-head (directional)
    if favors_home is True:
        f = h2h_factor(research, favors_home=True)
        if f:
            factors.append(f)
    elif favors_home is False:
        f = h2h_factor(research, favors_home=False)
        if f:
            factors.append(f)

    # Recent form (directional)
    if favors_home is True:
        f = form_factor({"last5": research.home.last5}, research.home.name, favors_outcome=True)
        if f:
            factors.append(f)
        f = form_factor({"last5": research.away.last5}, research.away.name, favors_outcome=False)
        if f:
            factors.append(f)
    elif favors_home is False:
        f = form_factor({"last5": research.away.last5}, research.away.name, favors_outcome=True)
        if f:
            factors.append(f)
        f = form_factor({"last5": research.home.last5}, research.home.name, favors_outcome=False)
        if f:
            factors.append(f)

    return factors
