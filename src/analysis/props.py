"""Probability model for player prop bets (Over / Under a line).

Strategy (see `src.analysis.model.estimate_prop` for the math):
    1. Pull the player's last N game values for the stat via ESPN.
    2. Recency-weight them and shrink the mean toward the posted line — the
       book's projection is a sharp prior, a 10-game sample is not.
    3. Pick a distribution by the shape of the stat (normal for yardage /
       points, Poisson or negative binomial for low-count stats).
    4. Blend in the empirical over-rate, attach an 80% interval and a
       confidence score, and expose the factors so the UI can show WHY.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from ..api.prizepicks_api import PlayerProp, POWER_PAYOUTS
from ..api.player_stats import find_athlete_id, fetch_recent_stat_values
from .probability import AdjustmentFactor, LegAnalysis
from . import model as M
from ..utils.formatters import american_to_decimal


@dataclass
class PropAnalysis:
    prop: PlayerProp
    samples: list[float] = field(default_factory=list)
    season_avg: float = 0.0         # raw mean of the samples (0 when none)
    projection: float = 0.0         # shrunk, recency-weighted projection
    stdev: float = 0.0              # model σ
    distribution: str = "normal"
    recent_trend: str = ""          # e.g. "4 of last 5 games over 25.5"
    hit_rate: float | None = None

    over_prob: float = 0.5
    under_prob: float = 0.5
    prob_low: float = 0.5           # 80% interval on the OVER probability
    prob_high: float = 0.5
    confidence: float = 0.0
    over_edge_vs_line: float = 0.0  # projection - line (positive -> over leans favored)

    factors: list[AdjustmentFactor] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def analyze_prop(
    prop: PlayerProp,
    team_hint_for_espn: str | None = None,
    *,
    max_games: int = 10,
    skip_network: bool = False,
) -> PropAnalysis:
    """Build a PropAnalysis for a single PlayerProp."""
    samples: list[float] = []
    if not skip_network:
        try:
            aid = find_athlete_id(prop.sport_key, prop.player_name, team_hint_for_espn or prop.team)
            if aid:
                samples = fetch_recent_stat_values(prop.sport_key, aid, prop.stat_type, max_games=max_games)
        except Exception:
            samples = []

    est = M.estimate_prop(prop.sport_key, prop.stat_type, prop.line, samples)

    recent_trend = ""
    if samples:
        hits = sum(1 for v in samples[:5] if v > prop.line)
        recent_trend = f"{hits} of last {min(5, len(samples))} games over {prop.line:g}"

    factors = _factors_for_prop(prop, samples, est)

    return PropAnalysis(
        prop=prop,
        samples=samples,
        season_avg=est.sample_mean if samples else prop.line,
        projection=est.projection,
        stdev=est.sd,
        distribution=est.distribution,
        recent_trend=recent_trend,
        hit_rate=est.hit_rate,
        over_prob=est.prob_over,
        under_prob=est.prob_under,
        prob_low=est.prob_low,
        prob_high=est.prob_high,
        confidence=est.confidence,
        over_edge_vs_line=est.projection - prop.line,
        factors=factors,
        notes=list(est.notes),
    )


def _factors_for_prop(prop: PlayerProp, samples: list[float], est: M.PropEstimate) -> list[AdjustmentFactor]:
    factors: list[AdjustmentFactor] = []
    if samples:
        factors.append(AdjustmentFactor(
            name=f"Last {len(samples)} avg",
            weight=0.0, scale=0.0, confidence=1.0, category="context",
            description=f"{est.sample_mean:.1f} raw · projection {est.projection:.1f} (σ {est.sd:.1f}) vs line {prop.line:g}",
        ))
        if len(samples) >= 5:
            recent = samples[:5]
            hits = sum(1 for v in recent if v > prop.line)
            factors.append(AdjustmentFactor(
                name="Last-5 trend",
                weight=(hits - 2.5) / 2.5,
                scale=0.0, confidence=1.0, category="form",
                description=f"{hits}/5 games cleared {prop.line:g} — blended as the empirical over-rate",
            ))
    else:
        factors.append(AdjustmentFactor(
            name="No recent data",
            weight=0.0, scale=0.0, confidence=1.0, category="context",
            description=f"Projection anchored on the line with sport default σ {est.sd:.1f}",
        ))
    factors.append(AdjustmentFactor(
        name="Distribution",
        weight=0.0, scale=0.0, confidence=1.0, category="model",
        description=f"{est.distribution} · confidence {est.confidence*100:.0f}%",
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
    is_over = pick == "Over"
    prob = analysis.over_prob if is_over else analysis.under_prob
    lo, hi = (analysis.prob_low, analysis.prob_high) if is_over else (1.0 - analysis.prob_high, 1.0 - analysis.prob_low)
    price = _PROP_DEFAULT_AMERICAN
    dec = american_to_decimal(price)
    book_implied = 1.0 / dec

    selection = f"{prop.player_name} {pick} {prop.line:g} {prop.stat_type}"
    matchup = f"{prop.team or '?'}  ·  {prop.opponent or ''}".strip()
    team_tag = (prop.team or "").upper()

    return LegAnalysis(
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
        prob_low=min(lo, prob),
        prob_high=max(hi, prob),
        confidence=analysis.confidence,
        prior_prob=book_implied,
        edge=prob - book_implied,
        decimal_price=dec,
        ev_per_dollar=M.expected_value(prob, dec),
        kelly_fraction=M.kelly_fraction(prob, dec),
        kelly_conservative=M.kelly_fraction(min(lo, prob), dec),
        adjustments=list(analysis.factors),
        notes=list(analysis.notes),
        corr_group=f"props:{prop.sport_key}",
        corr_tags=("prop", f"dir:{pick.lower()}", f"team:{team_tag}") if team_tag else ("prop", f"dir:{pick.lower()}"),
        sport_key=prop.sport_key,
    )


def power_payout(leg_count: int) -> float:
    """Return the PrizePicks 'power play' multiplier for an all-or-nothing pick-em."""
    return POWER_PAYOUTS.get(leg_count, 0.0)
