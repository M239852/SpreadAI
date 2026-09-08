"""User-facing probability dataclasses for team markets and parlays.

The estimation itself lives in `src.analysis.model`; this module turns a
`Game` + market + selection into a `LegAnalysis` the UI can render, and a list
of legs into a `ParlayAnalysis`.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from ..api.odds_api import Game
from ..utils.formatters import american_to_implied, american_to_decimal, decimal_to_american
from . import model as M


@dataclass
class AdjustmentFactor:
    """A research signal that nudges the market prior.

    `weight` is the signed signal in [-1, 1] (kept for backwards compatibility
    with the original API), `scale` is the maximum logit shift the factor can
    apply, and `confidence` discounts it. `contribution` is the resulting
    logit shift before the sport's market-efficiency discount.
    """
    name: str
    weight: float
    description: str = ""
    scale: float = 0.25
    confidence: float = 0.6
    category: str = "research"

    @property
    def contribution(self) -> float:
        return max(-1.0, min(1.0, self.weight)) * self.scale * max(0.0, min(1.0, self.confidence))

    def to_evidence(self) -> M.Evidence:
        return M.Evidence(
            name=self.name, signal=self.weight, scale=self.scale,
            confidence=self.confidence, description=self.description, category=self.category,
        )


@dataclass
class LegAnalysis:
    # Identifying data
    game_id: str
    sport_title: str
    matchup: str
    market: str                 # h2h | spreads | totals | prop_* | team_*
    selection: str              # "Kansas City Chiefs -2.5" / "Over 48.5" / ...
    price: int                  # Best American price available
    bookmaker: str              # Bookmaker offering the best price
    point: float | None = None

    # Probabilities
    book_implied: float = 0.0   # With vig (from the best price)
    fair_implied: float = 0.0   # De-vigged weighted consensus
    model_prob: float = 0.0     # Final model probability
    prob_low: float = 0.0       # 80% interval
    prob_high: float = 0.0
    confidence: float = 0.0     # 0..1
    prior_prob: float = 0.0     # Market prior after cross-market fusion / line shift
    cross_prob: float | None = None
    push_prob: float = 0.0

    # Derived
    edge: float = 0.0           # model_prob - book_implied (positive = +EV)
    decimal_price: float = 0.0
    ev_per_dollar: float = 0.0  # Expected value per $1 staked
    kelly_fraction: float = 0.0 # Full-Kelly at the point estimate
    kelly_conservative: float = 0.0  # Full-Kelly at the interval's low end

    # Explanation
    adjustments: list[AdjustmentFactor] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    model_version: str = M.MODEL_VERSION

    # Correlation metadata (same-game parlay math)
    corr_group: str = ""
    corr_tags: tuple[str, ...] = ()
    sport_key: str = ""

    @property
    def interval_width(self) -> float:
        return max(0.0, self.prob_high - self.prob_low)

    def corr_leg(self) -> M.CorrLeg:
        return M.CorrLeg(prob=self.model_prob, group=self.corr_group, tags=self.corr_tags, sport_key=self.sport_key)


# --- Market helpers ---------------------------------------------------------

def compute_fair_prob_from_game(game: Game, market_key: str, selection: str) -> float:
    """De-vigged, sharpness-weighted consensus probability for `selection`."""
    cons = M.consensus(game, market_key, selection)
    return cons.fair_prob if cons else 0.0


def _opposite_selection(game: Game, market_key: str, selection: str) -> str | None:
    if market_key in ("h2h", "spreads"):
        if selection == game.home_team:
            return game.away_team
        if selection == game.away_team:
            return game.home_team
        return None
    if market_key == "totals":
        return "Under" if selection.lower().startswith("over") else "Over"
    return None


def best_price(
    game: Game,
    market_key: str,
    selection: str,
    bookmaker_filter: str | None = None,
) -> tuple[int, str, float | None] | None:
    """Return (price, bookmaker_title, point) for the best available price."""
    best = game.best_price(market_key, selection, bookmaker_filter=bookmaker_filter)
    if best is None:
        return None
    bk, outcome = best
    return outcome.price, bk.title, outcome.point


def apply_adjustments(
    fair_prob: float,
    factors: list[AdjustmentFactor],
    max_swing: float = 0.12,
    sport_key: str | None = None,
) -> float:
    """Fuse factors into a probability in logit space (legacy helper).

    Prefer `build_leg_analysis`, which also does cross-market fusion and line
    shifting. Kept so older call sites keep working; `max_swing` is mapped onto
    the engine's logit cap.
    """
    if not factors:
        return fair_prob
    sp = M.sport_params(sport_key)
    cap = max(0.05, M.logit(0.5 + max_swing) - M.logit(0.5))
    fusion = M.fuse_evidence(M.logit(fair_prob), 0.0, [f.to_evidence() for f in factors], sp, cap=cap)
    return max(0.01, min(0.99, M.expit(fusion.logit)))


def expected_value_per_dollar(prob: float, american: int | float) -> float:
    return M.expected_value(prob, american_to_decimal(american))


def kelly_stake_fraction(prob: float, american: int | float) -> float:
    """Full-Kelly fraction; returns 0 for negative-EV bets."""
    return M.kelly_fraction(prob, american_to_decimal(american))


def _fmt_point(p: float) -> str:
    return f"+{p:g}" if p > 0 else f"{p:g}"


def _selection_label(market_key: str, selection: str, point: float | None) -> str:
    if market_key == "spreads" and point is not None:
        return f"{selection} {_fmt_point(point)}"
    if market_key == "totals" and point is not None:
        return f"{selection} {point:g}"
    return selection


def _corr_meta(game: Game, market_key: str, selection: str, prob: float) -> tuple[str, tuple[str, ...]]:
    """Correlation group + tags so the parlay math knows about same-game legs."""
    tags: list[str] = []
    is_home, _ = M._side_info(game, selection)
    if market_key in ("h2h", "spreads") and is_home is not None:
        tags.append("side:home" if is_home else "side:away")
        tags.append("fav" if prob >= 0.5 else "dog")
    elif market_key == "totals":
        tags.append("total:over" if selection.lower().startswith("over") else "total:under")
    return game.id, tuple(tags)


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
    est = M.estimate_leg(game, market_key, selection, [f.to_evidence() for f in factors], leg_point=point)
    if est is None:
        return None

    book_imp = american_to_implied(price)
    dec = american_to_decimal(price)
    prob = est.prob
    group, tags = _corr_meta(game, market_key, selection, est.consensus.fair_prob)

    return LegAnalysis(
        game_id=game.id,
        sport_title=game.sport_title,
        matchup=f"{game.away_team} @ {game.home_team}",
        market=market_key,
        selection=_selection_label(market_key, selection, point),
        price=price,
        bookmaker=book_title,
        point=point,
        book_implied=book_imp,
        fair_implied=est.market_prob,
        model_prob=prob,
        prob_low=est.prob_low,
        prob_high=est.prob_high,
        confidence=est.confidence,
        prior_prob=est.prior_prob,
        cross_prob=est.cross_prob,
        push_prob=est.push_prob,
        edge=prob - book_imp,
        decimal_price=dec,
        ev_per_dollar=M.expected_value(prob, dec),
        kelly_fraction=M.kelly_fraction(prob, dec),
        kelly_conservative=M.kelly_fraction(est.prob_low, dec),
        adjustments=list(factors),
        notes=list(est.notes),
        corr_group=group,
        corr_tags=tags,
        sport_key=game.sport_key,
    )


# --- DFS / prediction-market team pick-em ---------------------------------

# DFS pick-em (PrizePicks / Underdog) has no explicit price; we stamp -110 so
# parlay math still computes a combined probability, but the slip UI switches
# to the power-play multiplier once the slip is pure-DFS. Kalshi is a
# prediction-market exchange where each "yes" contract trades at a market
# price; we use the model probability AS the implied yes price (fair, no-vig).
_TEAM_PICK_DEFAULT_AMERICAN = -110

TEAM_PLATFORMS: tuple[str, ...] = ("prizepicks", "underdog", "kalshi")
_PLATFORM_LABEL: dict[str, str] = {
    "prizepicks": "PrizePicks",
    "underdog":   "Underdog",
    "kalshi":     "Kalshi",
}


def _consensus_point(game: Game, market_key: str, selection: str) -> float | None:
    """Sharpness-weighted median point across books — spreads/totals only."""
    if market_key not in ("spreads", "totals"):
        return None
    cons = M.consensus(game, market_key, selection)
    return cons.point if cons else None


def team_pick_to_leg(
    game: Game,
    market_key: str,     # "h2h" | "spreads" | "totals"
    selection: str,      # team name OR "Over"/"Under"
    platform: str = "prizepicks",
) -> LegAnalysis | None:
    """Build a DFS-flavored LegAnalysis for a team pick-em entry.

    The probability is the model's market prior (weighted consensus with
    cross-market fusion) at the consensus line.
    """
    platform_key = (platform or "prizepicks").strip().lower()
    if platform_key not in TEAM_PLATFORMS:
        platform_key = "prizepicks"
    bookmaker_label = _PLATFORM_LABEL[platform_key]

    est = M.estimate_leg(game, market_key, selection, [])
    if est is None or est.prob <= 0.0:
        return None
    prob = est.prob
    point = est.point if market_key in ("spreads", "totals") else None

    if platform_key == "kalshi":
        dec = 1.0 / max(0.01, min(0.99, prob))
        price = decimal_to_american(dec)
    else:
        price = _TEAM_PICK_DEFAULT_AMERICAN
        dec = american_to_decimal(price)
    book_imp = 1.0 / dec
    group, tags = _corr_meta(game, market_key, selection, est.consensus.fair_prob)

    return LegAnalysis(
        game_id=f"team:{game.id}:{market_key}:{selection.lower()}",
        sport_title=game.sport_title,
        matchup=f"{game.away_team} @ {game.home_team}",
        market=f"team_{market_key}",
        selection=_selection_label(market_key, selection, point),
        price=price,
        bookmaker=bookmaker_label,
        point=point,
        book_implied=book_imp,
        fair_implied=est.market_prob,
        model_prob=prob,
        prob_low=est.prob_low,
        prob_high=est.prob_high,
        confidence=est.confidence,
        prior_prob=est.prior_prob,
        cross_prob=est.cross_prob,
        push_prob=est.push_prob,
        edge=prob - book_imp,
        decimal_price=dec,
        ev_per_dollar=M.expected_value(prob, dec),
        kelly_fraction=M.kelly_fraction(prob, dec),
        kelly_conservative=M.kelly_fraction(est.prob_low, dec),
        notes=list(est.notes),
        corr_group=group,
        corr_tags=tags,
        sport_key=game.sport_key,
    )


# --- Parlay math -----------------------------------------------------------

DFS_BOOKS: tuple[str, ...] = ("prizepicks", "underdog", "demo")


@dataclass
class ParlayAnalysis:
    legs: list[LegAnalysis]
    combined_prob: float = 0.0
    independent_prob: float = 0.0   # product of leg probabilities (no correlation)
    combined_decimal: float = 1.0
    combined_american: int = 0
    implied_prob: float = 0.0       # Parlay payout's implied probability
    edge: float = 0.0
    ev_per_dollar: float = 0.0
    risk_reward: float = 0.0        # Payout per $1 risked (decimal - 1)
    kelly_fraction: float = 0.0
    kelly_conservative: float = 0.0
    confidence: float = 0.0         # weakest leg's confidence
    prob_low: float = 0.0           # product of the legs' interval lows (rough)
    prob_high: float = 0.0
    correlation_note: str = ""
    max_abs_corr: float = 0.0
    # DFS (pick-em) mode — populated when every leg is a PrizePicks-style prop.
    is_dfs: bool = False
    dfs_multiplier: float = 0.0
    dfs_ev_per_dollar: float = 0.0


def analyze_parlay(legs: list[LegAnalysis]) -> ParlayAnalysis:
    if not legs:
        return ParlayAnalysis(legs=[])
    pp = M.parlay_probability([l.corr_leg() for l in legs])
    combined_prob = pp.combined
    combined_dec = 1.0
    lo = hi = 1.0
    for l in legs:
        combined_dec *= l.decimal_price
        lo *= (l.prob_low or l.model_prob)
        hi *= (l.prob_high or l.model_prob)
    # Keep the interval consistent with the correlated estimate.
    ratio = combined_prob / pp.independent if pp.independent > 0 else 1.0
    lo, hi = min(lo * ratio, combined_prob), max(hi * ratio, combined_prob)

    implied_from_price = 1.0 / combined_dec if combined_dec > 0 else 0.0
    american = decimal_to_american(combined_dec)
    ev = M.expected_value(combined_prob, combined_dec)
    b = combined_dec - 1.0
    kelly = M.kelly_fraction(combined_prob, combined_dec)
    kelly_cons = M.kelly_fraction(lo, combined_dec)

    is_dfs = all(
        (l.market.startswith("prop_") or l.market.startswith("team_"))
        and l.bookmaker.lower() in DFS_BOOKS
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
        independent_prob=pp.independent,
        combined_decimal=combined_dec,
        combined_american=american,
        implied_prob=implied_from_price,
        edge=combined_prob - implied_from_price,
        ev_per_dollar=ev,
        risk_reward=b,
        kelly_fraction=kelly,
        kelly_conservative=kelly_cons,
        confidence=min(l.confidence for l in legs),
        prob_low=lo,
        prob_high=hi,
        correlation_note=pp.note,
        max_abs_corr=pp.max_abs_corr,
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
        scale=0.50,
        confidence=0.70,
        category="injuries",
    )


def form_factor(record_info: dict[str, Any], team_label: str, favors_outcome: bool) -> AdjustmentFactor | None:
    """Recent form *relative to the team's own baseline* becomes a mild factor.

    A 4-1 run from a .750 team is not news; a 4-1 run from a .400 team is.
    Confidence grows with the number of recent games observed.
    """
    last5 = record_info.get("last5") or []
    if not last5:
        return None
    n = len(last5)
    wins = sum(1 for g in last5 if g.get("result") == "W")
    baseline = record_info.get("win_pct")
    try:
        baseline = float(baseline) if baseline is not None else 0.5
        if baseline > 1.0:      # ESPN sometimes reports 0-100
            baseline /= 100.0
    except (TypeError, ValueError):
        baseline = 0.5
    residual = (wins / n) - baseline          # -1..+1
    sign = 1.0 if favors_outcome else -1.0
    return AdjustmentFactor(
        name=f"Recent form ({team_label})",
        weight=sign * max(-1.0, min(1.0, residual * 2.0)),
        description=f"{team_label} last-{n}: {wins}-{n - wins} vs season {baseline*100:.0f}%",
        scale=0.25,
        confidence=0.6 * min(1.0, n / 5.0),
        category="form",
    )


def home_field_factor(is_home: bool, sport_key: str) -> AdjustmentFactor:
    """Home advantage is already embedded in every sportsbook line.

    Kept as an informational factor (zero scale) so the UI can show the user
    that the model saw it and deliberately did not double count it.
    """
    return AdjustmentFactor(
        name="Home field",
        weight=0.5 if is_home else -0.5,
        description=("Home advantage — already priced into the consensus line"
                     if is_home else "Road disadvantage — already priced into the consensus line"),
        scale=0.0,
        confidence=1.0,
        category="context",
    )


def rest_factor(days_rest: float | None, team_label: str, favors_outcome: bool, sport_key: str) -> AdjustmentFactor | None:
    """Back-to-back / short-rest penalty for sports where fatigue is measurable."""
    sp = M.sport_params(sport_key)
    if days_rest is None or not sp.rest_matters:
        return None
    if days_rest <= 1.0:
        signal, label = -1.0, "back-to-back"
    elif days_rest <= 2.0:
        signal, label = -0.35, "short rest"
    else:
        return None
    sign = 1.0 if favors_outcome else -1.0
    return AdjustmentFactor(
        name=f"Rest ({team_label})",
        weight=sign * signal,
        description=f"{team_label} on {label} ({days_rest:.0f} day{'s' if days_rest != 1 else ''} rest)",
        scale=0.12,
        confidence=0.8,
        category="schedule",
    )
