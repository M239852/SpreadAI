"""Auto-generate multi-leg parlay slips balancing hit probability, EV and payout."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

from ..api.odds_api import Game
from .probability import (
    LegAnalysis, ParlayAnalysis, analyze_parlay, build_leg_analysis,
    AdjustmentFactor,
)
from .research import research_game, build_factors_for_selection, GameResearch


MODES: dict[str, dict] = {
    "safest": {
        "label": "Safest",
        "subtitle": "Highest combined hit probability",
        "min_leg_prob": 0.58,
        "max_leg_american": 300,       # ignore extreme dogs
        "score": "prob",
    },
    "balanced": {
        "label": "Balanced",
        "subtitle": "Best edge with meaningful payout",
        "min_leg_prob": 0.48,
        "max_leg_american": 300,
        "score": "edge_prob",
    },
    "longshot": {
        "label": "High Upside",
        "subtitle": "Positive-EV swings with real payout",
        "min_leg_prob": 0.30,
        "max_leg_american": 500,
        "score": "ev",
    },
}


@dataclass
class GeneratedSlip:
    mode: str
    mode_label: str
    mode_subtitle: str
    legs: list[LegAnalysis]
    parlay: ParlayAnalysis
    per_leg_reasoning: list[str] = field(default_factory=list)
    overall_reasoning: str = ""
    research_by_game: dict[str, GameResearch] = field(default_factory=dict)
    # Stable per-leg exclusion keys (game|market|selection). The multi-slip
    # wrapper in `generate_slips` accumulates these so subsequent slips never
    # repeat the same pick when dedup is enabled.
    leg_keys: list[str] = field(default_factory=list)


def _candidate_selections(game: Game) -> list[tuple[str, str]]:
    """All market/selection pairs worth evaluating for a game."""
    return [
        ("h2h", game.home_team),
        ("h2h", game.away_team),
        ("spreads", game.home_team),
        ("spreads", game.away_team),
        ("totals", "Over"),
        ("totals", "Under"),
    ]


def _score_leg(leg: LegAnalysis, mode: str) -> float:
    cfg = MODES[mode]
    if cfg["score"] == "prob":
        # Reward higher model probability. Slight nudge for positive edge.
        return leg.model_prob + max(0.0, leg.edge) * 0.5
    if cfg["score"] == "edge_prob":
        # Prefer positive edge legs with high enough probability.
        if leg.edge <= 0:
            return -1.0
        return leg.edge * 0.7 + leg.model_prob * 0.3
    if cfg["score"] == "ev":
        # Maximize EV but require positive EV.
        if leg.ev_per_dollar <= 0:
            return -1.0
        return leg.ev_per_dollar + leg.model_prob * 0.25
    return leg.model_prob


def _exclusion_key(market_key: str, selection: str, game_id: str) -> str:
    """Stable key for cross-slip dedup: (game, market, selection)."""
    return f"{game_id}|{market_key}|{selection}".lower()


def generate_slip(
    games: list[Game],
    mode: str = "balanced",
    max_legs: int = 4,
    min_legs: int = 2,
    progress_cb: Callable[[int, int, str], None] | None = None,
    bookmaker_filter: str | None = None,
    exclude_keys: set[str] | None = None,
) -> GeneratedSlip | None:
    """Build a GeneratedSlip from the provided games and mode.

    Enforces one leg per game (no correlated parlays in this generator).
    When `bookmaker_filter` is set, every leg's price comes from that book
    only — the result is a single-book slip you could actually place.
    `exclude_keys` is the cross-slip dedup hook used by `generate_slips`:
    each candidate leg is keyed by (game, market, selection) and skipped if
    that key is already in the exclusion set. The min_legs fallback respects
    the same exclusion so we never silently smuggle a duplicated leg in.
    Returns None if no games produce qualifying legs.
    """
    excluded = exclude_keys or set()
    if mode not in MODES:
        mode = "balanced"
    cfg = MODES[mode]

    # 1) Gather research per game.
    research_by_game: dict[str, GameResearch] = {}
    total = len(games)
    for i, g in enumerate(games):
        if progress_cb:
            progress_cb(i, total, f"Researching {g.away_team} @ {g.home_team}")
        try:
            research_by_game[g.id] = research_game(g)
        except Exception:
            # Tolerate research failures; fall back to empty research so we
            # still use the odds math.
            from .research import TeamResearch  # late import for type
            research_by_game[g.id] = GameResearch(
                game_id=g.id, sport_key=g.sport_key,
                matchup=f"{g.away_team} @ {g.home_team}",
                home=TeamResearch(name=g.home_team),
                away=TeamResearch(name=g.away_team),
            )
    if progress_cb:
        progress_cb(total, total, "Scoring candidates")

    # 2) Build every candidate leg with research-adjusted probabilities.
    candidates: list[tuple[LegAnalysis, str]] = []  # (leg, exclusion_key)
    for g in games:
        research = research_by_game.get(g.id)
        for market_key, selection in _candidate_selections(g):
            key = _exclusion_key(market_key, selection, g.id)
            if key in excluded:
                continue
            factors: list[AdjustmentFactor] = []
            if research is not None:
                factors = build_factors_for_selection(g, research, market_key, selection)
            leg = build_leg_analysis(
                g, market_key, selection, factors,
                bookmaker_filter=bookmaker_filter,
            )
            if leg is None:
                continue
            # Mode-specific filters
            if leg.model_prob < cfg["min_leg_prob"]:
                continue
            if abs(leg.price) > 0 and leg.price > cfg["max_leg_american"]:
                continue
            candidates.append((leg, key))

    if not candidates:
        return None

    # 3) Rank legs and greedily pick the best one per game, up to max_legs.
    candidates.sort(key=lambda lk: _score_leg(lk[0], mode), reverse=True)
    used_games: set[str] = set()
    picked: list[LegAnalysis] = []
    picked_keys: list[str] = []
    for leg, key in candidates:
        if leg.game_id in used_games:
            continue
        if _score_leg(leg, mode) < 0:
            continue
        picked.append(leg)
        picked_keys.append(key)
        used_games.add(leg.game_id)
        if len(picked) >= max_legs:
            break

    if len(picked) < min_legs:
        # Fall back: fill with next-best legs (ignoring mode's negative filter).
        # Exclusion already filtered candidates upstream, so the fallback
        # naturally respects it without a second check.
        for leg, key in candidates:
            if leg.game_id in used_games:
                continue
            picked.append(leg)
            picked_keys.append(key)
            used_games.add(leg.game_id)
            if len(picked) >= min_legs:
                break

    if not picked:
        return None

    parlay = analyze_parlay(picked)

    # 4) Build reasoning per leg
    per_leg: list[str] = []
    for leg in picked:
        research = research_by_game.get(leg.game_id)
        per_leg.append(_reason_for_leg(leg, research))

    overall = _overall_reasoning(mode, picked, parlay)

    return GeneratedSlip(
        mode=mode,
        mode_label=cfg["label"],
        mode_subtitle=cfg["subtitle"],
        legs=picked,
        parlay=parlay,
        per_leg_reasoning=per_leg,
        overall_reasoning=overall,
        research_by_game=research_by_game,
        leg_keys=picked_keys,
    )


# --- Multi-slip orchestration ---------------------------------------------

def generate_slips(
    games: list[Game],
    mode: str = "balanced",
    max_legs: int = 4,
    min_legs: int = 2,
    count: int = 1,
    dedupe_legs: bool = True,
    progress_cb: Callable[[int, int, str], None] | None = None,
    bookmaker_filter: str | None = None,
) -> list[GeneratedSlip]:
    """Generate up to `count` slips back-to-back.

    When `dedupe_legs` is True (the default), each slip's exact (game, market,
    selection) picks are excluded from later slips — different markets on the
    same game can still appear in another slip. The pool's quality drops as
    slips accumulate, so callers should stop early if a slip comes back None
    rather than synthesize filler.

    When `dedupe_legs` is False, no exclusion is applied; the generator is
    deterministic so slips will be identical or near-identical. That's the
    explicit semantic of the toggle — if the caller wants variety they should
    leave dedup on.

    Returns the slips actually built (length 0..count). Stops early on the
    first None so the caller doesn't have to handle gaps.
    """
    if count <= 0:
        return []
    excluded: set[str] = set() if dedupe_legs else set()
    out: list[GeneratedSlip] = []
    # Research is the expensive part; do it once and reuse across slips by
    # short-circuiting `generate_slip` with an inner pass that doesn't reload
    # the per-game research. Easiest path that keeps the existing function
    # honest: just call it once per slip — research_by_game lives inside the
    # call but the cost is bounded by `count` (≤5 in practice).
    for i in range(count):
        slip = generate_slip(
            games=games,
            mode=mode,
            max_legs=max_legs,
            min_legs=min_legs,
            progress_cb=progress_cb if i == 0 else None,
            bookmaker_filter=bookmaker_filter,
            exclude_keys=excluded if dedupe_legs else None,
        )
        if slip is None:
            break
        out.append(slip)
        if dedupe_legs:
            excluded.update(slip.leg_keys)
    return out


def _reason_for_leg(leg: LegAnalysis, research: GameResearch | None) -> str:
    parts: list[str] = []

    # Probability / edge narrative
    book_pct = leg.book_implied * 100
    model_pct = leg.model_prob * 100
    edge_pct = leg.edge * 100
    if edge_pct >= 3:
        parts.append(
            f"Model gives this {model_pct:.1f}% — about {edge_pct:+.1f}% over the book's "
            f"implied {book_pct:.1f}% at {leg.bookmaker}."
        )
    elif edge_pct >= 0:
        parts.append(
            f"Model at {model_pct:.1f}% vs book {book_pct:.1f}% — fair price with a slight lean."
        )
    else:
        parts.append(
            f"Priced at {book_pct:.1f}% implied vs model {model_pct:.1f}%; taken because it anchors "
            f"the slip with a high hit rate."
        )

    # Adjustment drivers
    drivers: list[str] = []
    for f in leg.adjustments:
        if abs(f.weight) < 0.04:
            continue
        arrow = "↑" if f.weight > 0 else "↓"
        drivers.append(f"{arrow} {f.name}: {f.description}")
    if drivers:
        parts.append("Research drivers — " + "; ".join(drivers) + ".")

    # Research context
    if research is not None:
        side_home = leg.selection.startswith(research.home.name)
        side_away = leg.selection.startswith(research.away.name)
        target = research.home if side_home else (research.away if side_away else None)
        foe = research.away if side_home else (research.home if side_away else None)
        bits = []
        if target and target.last5_summary:
            bits.append(f"{target.name} last-5: {target.last5_summary}")
        if foe and foe.last5_summary:
            bits.append(f"{foe.name} last-5: {foe.last5_summary}")
        if research.h2h_summary and int(research.h2h_summary.get("played") or 0) >= 2:
            s = research.h2h_summary
            bits.append(f"H2H last {s['played']}: home {s['record']}")
        if bits:
            parts.append("Context — " + " · ".join(bits) + ".")

    return " ".join(parts)


def _overall_reasoning(mode: str, legs: list[LegAnalysis], parlay: ParlayAnalysis) -> str:
    cfg = MODES[mode]
    n = len(legs)
    prob_pct = parlay.combined_prob * 100
    book_imp_pct = parlay.implied_prob * 100
    edge_pct = parlay.edge * 100
    rr = parlay.risk_reward

    stance: str
    if mode == "safest":
        stance = (
            f"Optimized for consistency: {n} legs with each model probability ≥ "
            f"{int(cfg['min_leg_prob'] * 100)}%. Priority was hit rate over payout."
        )
    elif mode == "longshot":
        stance = (
            f"Optimized for expected value: {n} legs chosen where the model's probability "
            f"exceeds the book's price by the widest margin. Variance is higher."
        )
    else:
        stance = (
            f"Optimized for edge with meaningful payout: {n} +edge legs combined into a balanced slip."
        )

    verdict: str
    if edge_pct >= 5:
        verdict = f"Model edge of {edge_pct:+.1f}% — the slip is priced favorably."
    elif edge_pct >= 0:
        verdict = f"Model edge of {edge_pct:+.1f}% — roughly fair, slight lean."
    else:
        verdict = (
            f"Model implies a {edge_pct:+.1f}% gap vs the book's price — entertainment lean, "
            "not a value play."
        )

    return (
        f"{stance}  Combined hit probability {prob_pct:.1f}% "
        f"(book implies {book_imp_pct:.1f}%).  Risk : Reward 1 : {rr:.2f}.  {verdict}"
    )
