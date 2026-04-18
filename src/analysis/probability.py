from __future__ import annotations
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from ..api.odds_api import Game
from ..utils.formatters import american_to_implied, devig_two_way, american_to_decimal


@dataclass
class AdjustmentFactor:
    name: str
    weight: float        # -1..+1 shift in favor of the outcome (before scaling)
    description: str = ""


@dataclass
class LegAnalysis:
    # Identifying data
    game_id: str
    sport_title: str
    matchup: str
    market: str                 # h2h | spreads | totals
    selection: str              # "Kansas City Chiefs" / "Over 48.5" / ...
    price: int                  # Best American price available
    bookmaker: str              # Bookmaker offering the best price
    point: float | None = None

    # Implied probabilities
    book_implied: float = 0.0   # With vig (from the best price)
    fair_implied: float = 0.0   # Two-way devigged market consensus
    model_prob: float = 0.0     # After external adjustments (injuries, form, etc.)

    # Derived
    edge: float = 0.0           # model_prob - book_implied (positive = +EV)
    decimal_price: float = 0.0
    ev_per_dollar: float = 0.0  # Expected value per $1 staked
    kelly_fraction: float = 0.0 # Full-Kelly suggested stake fraction

    # Explanation
    adjustments: list[AdjustmentFactor] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def compute_fair_prob_from_game(game: Game, market_key: str, selection: str) -> float:
    """Compute the de-vigged fair probability for `selection` in `market_key`.

    Uses the median of all bookmakers' implied probabilities, then removes the vig
    against the opposing outcome's median.
    """
    opp_name = _opposite_selection(game, market_key, selection)
    sel_imps = [american_to_implied(p) for p in game.consensus_prices(market_key, selection)]
    opp_imps = [american_to_implied(p) for p in game.consensus_prices(market_key, opp_name)] if opp_name else []
    if not sel_imps:
        return 0.0
    med_sel = median(sel_imps)
    if not opp_imps:
        return med_sel
    med_opp = median(opp_imps)
    fair_sel, _ = devig_two_way(med_sel, med_opp)
    return fair_sel


def _opposite_selection(game: Game, market_key: str, selection: str) -> str | None:
    if market_key == "h2h":
        if selection == game.home_team:
            return game.away_team
        if selection == game.away_team:
            return game.home_team
        return None
    if market_key == "spreads":
        if selection == game.home_team:
            return game.away_team
        if selection == game.away_team:
            return game.home_team
        return None
    if market_key == "totals":
        if selection.lower().startswith("over"):
            return "Under"
        return "Over"
    return None


def best_price(
    game: Game,
    market_key: str,
    selection: str,
    bookmaker_filter: str | None = None,
) -> tuple[int, str, float | None] | None:
    """Return (price, bookmaker_title, point) for the best available price.

    When `bookmaker_filter` is given, restrict to that book only — used when
    the caller wants every leg of a slip to come from the same sportsbook.
    """
    best = game.best_price(market_key, selection, bookmaker_filter=bookmaker_filter)
    if best is None:
        return None
    bk, outcome = best
    return outcome.price, bk.title, outcome.point


def apply_adjustments(
    fair_prob: float,
    factors: list[AdjustmentFactor],
    max_swing: float = 0.12,
) -> float:
    """Combine adjustment factors into a single probability delta.

    Each factor has a weight in [-1, 1]. Their weighted sum is clamped and
    mapped into a bounded probability shift (default ±12%).
    """
    if not factors:
        return fair_prob
    raw = sum(f.weight for f in factors)
    # Soft-clamp using tanh for diminishing returns
    import math
    shift = math.tanh(raw / 2.0) * max_swing
    return max(0.01, min(0.99, fair_prob + shift))


def expected_value_per_dollar(prob: float, american: int | float) -> float:
    dec = american_to_decimal(american)
    return prob * (dec - 1.0) - (1.0 - prob)


def kelly_stake_fraction(prob: float, american: int | float) -> float:
    """Full-Kelly fraction; returns 0 for negative-EV bets."""
    dec = american_to_decimal(american)
    b = dec - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - prob
    f = (b * prob - q) / b
    return max(0.0, f)


def build_leg_analysis(
    game: Game,
    market_key: str,
    selection: str,
    factors: list[AdjustmentFactor],
    bookmaker_filter: str | None = None,
) -> LegAnalysis | None:
    bp = best_price(game, market_key, selection, bookmaker_filter=bookmaker_filter)
    if bp is None:
        return None
    price, book_title, point = bp
    fair = compute_fair_prob_from_game(game, market_key, selection)
    book_imp = american_to_implied(price)
    model = apply_adjustments(fair, factors)
    edge = model - book_imp
    dec = american_to_decimal(price)
    ev = model * (dec - 1.0) - (1.0 - model)
    kelly = kelly_stake_fraction(model, price)

    selection_label = selection
    if market_key == "spreads" and point is not None:
        selection_label = f"{selection} {_fmt_point(point)}"
    elif market_key == "totals" and point is not None:
        selection_label = f"{selection} {point}"

    return LegAnalysis(
        game_id=game.id,
        sport_title=game.sport_title,
        matchup=f"{game.away_team} @ {game.home_team}",
        market=market_key,
        selection=selection_label,
        price=price,
        bookmaker=book_title,
        point=point,
        book_implied=book_imp,
        fair_implied=fair,
        model_prob=model,
        edge=edge,
        decimal_price=dec,
        ev_per_dollar=ev,
        kelly_fraction=kelly,
        adjustments=factors,
    )


def _fmt_point(p: float) -> str:
    return f"+{p}" if p > 0 else f"{p}"


# --- Parlay math -----------------------------------------------------------

@dataclass
class ParlayAnalysis:
    legs: list[LegAnalysis]
    combined_prob: float = 0.0
    combined_decimal: float = 1.0
    combined_american: int = 0
    implied_prob: float = 0.0     # Parlay payout's implied probability
    edge: float = 0.0
    ev_per_dollar: float = 0.0
    risk_reward: float = 0.0      # Payout per $1 risked (decimal - 1)
    kelly_fraction: float = 0.0
    # DFS (pick-em) mode — populated when every leg is a PrizePicks-style prop.
    is_dfs: bool = False
    dfs_multiplier: float = 0.0
    dfs_ev_per_dollar: float = 0.0


def analyze_parlay(legs: list[LegAnalysis]) -> ParlayAnalysis:
    if not legs:
        return ParlayAnalysis(legs=[])
    combined_prob = 1.0
    combined_dec = 1.0
    for l in legs:
        combined_prob *= l.model_prob
        combined_dec *= l.decimal_price
    implied_from_price = 1.0 / combined_dec if combined_dec > 0 else 0.0
    # American conversion
    from ..utils.formatters import decimal_to_american
    american = decimal_to_american(combined_dec)
    ev = combined_prob * (combined_dec - 1.0) - (1.0 - combined_prob)
    b = combined_dec - 1.0
    kelly = max(0.0, (b * combined_prob - (1.0 - combined_prob)) / b) if b > 0 else 0.0
    # DFS mode detection: triggers when every leg is a pick-em style prop.
    is_dfs = bool(legs) and all(
        l.market.startswith("prop_") and l.bookmaker.lower() in ("prizepicks", "underdog", "demo")
        for l in legs
    )
    dfs_mult = 0.0
    dfs_ev = 0.0
    if is_dfs:
        from ..api.prizepicks_api import POWER_PAYOUTS
        dfs_mult = POWER_PAYOUTS.get(len(legs), 0.0)
        if dfs_mult > 0:
            dfs_ev = combined_prob * (dfs_mult - 1.0) - (1.0 - combined_prob)

    return ParlayAnalysis(
        legs=legs,
        combined_prob=combined_prob,
        combined_decimal=combined_dec,
        combined_american=american,
        implied_prob=implied_from_price,
        edge=combined_prob - implied_from_price,
        ev_per_dollar=ev,
        risk_reward=b,
        kelly_fraction=kelly,
        is_dfs=is_dfs,
        dfs_multiplier=dfs_mult,
        dfs_ev_per_dollar=dfs_ev,
    )


# --- Factor construction from research -------------------------------------

def injury_factor(impact_score: float, team_label: str, favors_outcome: bool) -> AdjustmentFactor:
    """Turn an injury impact score (0..1) into a signed adjustment factor.

    `favors_outcome=True` means the injury hurts the opposing team, boosting
    the probability of our selection.
    """
    sign = 1.0 if favors_outcome else -1.0
    return AdjustmentFactor(
        name=f"Injuries ({team_label})",
        weight=sign * impact_score,
        description=f"Aggregated injury severity for {team_label}: {impact_score:.2f}",
    )


def form_factor(record_info: dict[str, Any], team_label: str, favors_outcome: bool) -> AdjustmentFactor | None:
    """Last-5 form relative to .500 becomes a mild adjustment."""
    last5 = record_info.get("last5") or []
    if not last5:
        return None
    wins = sum(1 for g in last5 if g.get("result") == "W")
    diff = (wins / len(last5)) - 0.5   # -0.5..+0.5
    sign = 1.0 if favors_outcome else -1.0
    return AdjustmentFactor(
        name=f"Recent form ({team_label})",
        weight=sign * diff,
        description=f"{team_label} last-5: {wins}-{len(last5) - wins}",
    )


def home_field_factor(is_home: bool, sport_key: str) -> AdjustmentFactor:
    base = {
        "americanfootball_nfl": 0.12,
        "americanfootball_ncaaf": 0.18,
        "basketball_nba": 0.10,
        "basketball_ncaab": 0.15,
        "baseball_mlb": 0.06,
        "icehockey_nhl": 0.08,
        "soccer_epl": 0.15,
    }.get(sport_key, 0.08)
    weight = base if is_home else -base
    return AdjustmentFactor(
        name="Home field",
        weight=weight,
        description=("Home advantage" if is_home else "Road disadvantage"),
    )
