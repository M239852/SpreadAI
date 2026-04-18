"""Probability model for player prop bets (Over / Under a line).

Strategy:
    1. Pull the player's last N game values for the stat via ESPN.
    2. Estimate mean & stdev.
    3. P(Over line) via a Gaussian tail approximation, with sport-aware
       fallback volatility when we only have a few samples.
    4. Apply small adjustments (e.g. 'player flagged as questionable') when
       available; these are layered into the factor list so the UI can show
       the WHY.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from ..api.prizepicks_api import PlayerProp, POWER_PAYOUTS
from ..api.player_stats import (
    find_athlete_id, fetch_recent_stat_values, season_average, sample_stdev,
)
from .probability import AdjustmentFactor, LegAnalysis
from ..utils.formatters import american_to_decimal


# Sport-aware default stdev as a fraction of the line — used when we don't have
# enough recent samples to compute a real one.
_DEFAULT_CV: dict[str, float] = {
    "basketball_nba": 0.22,
    "basketball_ncaab": 0.25,
    "americanfootball_nfl": 0.30,
    "americanfootball_ncaaf": 0.32,
    "baseball_mlb": 0.55,      # props like H, HR are binary-ish -> volatile
    "icehockey_nhl": 0.45,
    "soccer_epl": 0.50,
    "default": 0.30,
}


@dataclass
class PropAnalysis:
    prop: PlayerProp
    samples: list[float] = field(default_factory=list)
    season_avg: float = 0.0
    stdev: float = 0.0
    recent_trend: str = ""          # e.g. "4 of 5 games over 25.5"

    over_prob: float = 0.5
    under_prob: float = 0.5
    over_edge_vs_line: float = 0.0  # season_avg - line (positive -> over leans favored)

    factors: list[AdjustmentFactor] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def analyze_prop(
    prop: PlayerProp,
    team_hint_for_espn: str | None = None,
    *,
    max_games: int = 10,
    skip_network: bool = False,
) -> PropAnalysis:
    """Build a PropAnalysis for a single PlayerProp.

    `team_hint_for_espn` is the ESPN-friendly team name/abbrev. If omitted we
    try the PrizePicks `team` field.
    """
    samples: list[float] = []
    if not skip_network:
        try:
            aid = find_athlete_id(prop.sport_key, prop.player_name, team_hint_for_espn or prop.team)
            if aid:
                samples = fetch_recent_stat_values(prop.sport_key, aid, prop.stat_type, max_games=max_games)
        except Exception:
            samples = []

    mean = season_average(samples) if samples else prop.line
    stdev = sample_stdev(samples) if len(samples) >= 3 else 0.0
    if stdev == 0.0:
        cv = _DEFAULT_CV.get(prop.sport_key, _DEFAULT_CV["default"])
        stdev = max(0.75, abs(prop.line) * cv)

    # P(stat > line) under a Gaussian assumption.  Uses the continuity-
    # corrected boundary (line + 0.5) for discrete stats like counts.
    line_eff = prop.line
    boundary = line_eff + 0.0001
    z = (boundary - mean) / max(stdev, 1e-6)
    over_prob = 1.0 - _phi(z)
    over_prob = min(0.97, max(0.03, over_prob))
    under_prob = 1.0 - over_prob

    recent_trend = ""
    if samples:
        hits = sum(1 for v in samples[:5] if v > prop.line)
        recent_trend = f"{hits} of last {min(5, len(samples))} games over {prop.line}"

    factors = _factors_for_prop(prop, samples, mean, stdev)

    return PropAnalysis(
        prop=prop,
        samples=samples,
        season_avg=mean,
        stdev=stdev,
        recent_trend=recent_trend,
        over_prob=over_prob,
        under_prob=under_prob,
        over_edge_vs_line=mean - prop.line,
        factors=factors,
    )


def _phi(x: float) -> float:
    """Standard normal CDF via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _factors_for_prop(prop, samples: list[float], mean: float, stdev: float) -> list[AdjustmentFactor]:
    factors: list[AdjustmentFactor] = []
    if samples:
        factors.append(AdjustmentFactor(
            name=f"Last {len(samples)} avg",
            weight=0.0,  # informational (no swing)
            description=f"{mean:.1f} (σ {stdev:.1f}) vs line {prop.line}",
        ))
        if len(samples) >= 5:
            recent = samples[:5]
            hits = sum(1 for v in recent if v > prop.line)
            factors.append(AdjustmentFactor(
                name="Last-5 trend",
                weight=(hits - 2.5) / 5.0 * 0.3,
                description=f"{hits}/5 games cleared {prop.line}",
            ))
    else:
        factors.append(AdjustmentFactor(
            name="No recent data",
            weight=0.0,
            description=f"Using sport default volatility (σ {stdev:.1f})",
        ))
    return factors


# -- Convert a prop selection into a LegAnalysis --------------------------

# PrizePicks has no explicit price; we model it at -110 equivalent so it fits
# the existing parlay math. Users can still see the DFS pick-em multiplier
# separately via `power_payout()`.
_PROP_DEFAULT_AMERICAN = -110


def prop_to_leg(
    prop: PlayerProp,
    analysis: PropAnalysis,
    pick: str,   # "Over" or "Under"
) -> LegAnalysis:
    prob = analysis.over_prob if pick == "Over" else analysis.under_prob
    price = _PROP_DEFAULT_AMERICAN
    dec = american_to_decimal(price)

    # Book implied at -110 is ~52.4%; this is just for the parlay math shape.
    book_implied = 1.0 / dec
    edge = prob - book_implied
    ev = prob * (dec - 1.0) - (1.0 - prob)
    b = dec - 1.0
    kelly = max(0.0, (b * prob - (1.0 - prob)) / b) if b > 0 else 0.0

    selection = f"{prop.player_name} {pick} {prop.line} {prop.stat_type}"
    matchup = f"{prop.team or '?'}  ·  {prop.opponent or ''}".strip()

    leg = LegAnalysis(
        game_id=f"prop:{prop.id}",
        sport_title=prop.league,
        matchup=matchup,
        market=f"prop_{pick.lower()}",
        selection=selection,
        price=price,
        bookmaker=prop.source,
        point=prop.line,
        book_implied=book_implied,
        fair_implied=book_implied,   # no alternate books to de-vig
        model_prob=prob,
        edge=edge,
        decimal_price=dec,
        ev_per_dollar=ev,
        kelly_fraction=kelly,
        adjustments=list(analysis.factors),
    )
    return leg


def power_payout(leg_count: int) -> float:
    """Return the PrizePicks 'power play' multiplier for an all-or-nothing pick-em."""
    return POWER_PAYOUTS.get(leg_count, 0.0)
