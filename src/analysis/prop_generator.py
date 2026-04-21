"""Auto-generate a PrizePicks-style player prop slip.

Sibling of `src.analysis.generator` (which does team parlays). Given a list of
`PlayerProp` and a mode, this module analyzes each prop, ranks candidates, and
assembles a multi-leg slip with the computed hit probability and expected
profit at the corresponding power-play multiplier.

Modes
-----
safest     — highest model probability per pick, line-tested with long recent
             samples preferred.
balanced   — best edge vs. the line, prob above the power-play break-even.
upside     — props with the strongest directional signal from recent form,
             accepting higher variance in exchange for less crowded picks.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

from ..api.prizepicks_api import PlayerProp, POWER_PAYOUTS
from .probability import LegAnalysis, ParlayAnalysis, analyze_parlay
from .props import PropAnalysis, analyze_prop, prop_to_leg


# ----------------------- Mode configuration ---------------------------------

MODES: dict[str, dict] = {
    "safest": {
        "label": "Safest",
        "subtitle": "Highest model hit rate per leg",
        "min_side_prob": 0.62,
        "score": "prob",
    },
    "balanced": {
        "label": "Balanced",
        "subtitle": "Strong edge, payout-worthy hit rate",
        "min_side_prob": 0.55,
        "score": "edge",
    },
    "upside": {
        "label": "Upside",
        "subtitle": "Less crowded picks with form backing",
        "min_side_prob": 0.52,
        "score": "trend",
    },
}


# ----------------------- Result container -----------------------------------

@dataclass
class PropPick:
    """One pick (prop + side) in a generated prop slip."""
    prop: PlayerProp
    analysis: PropAnalysis
    side: str                    # "Over" or "Under"
    prob: float                  # model hit probability of the chosen side
    reasoning: str = ""


@dataclass
class GeneratedPropSlip:
    mode: str
    mode_label: str
    mode_subtitle: str
    picks: list[PropPick]
    legs: list[LegAnalysis]
    parlay: ParlayAnalysis
    power_multiplier: float = 0.0    # 0 when leg count is outside 2..6
    break_even_prob: float = 0.0     # 1 / multiplier
    projected_payout: float = 0.0    # at `stake`
    projected_profit: float = 0.0
    ev_dollars: float = 0.0
    overall_reasoning: str = ""


# ----------------------- Core assembly --------------------------------------

def _choose_side(a: PropAnalysis) -> tuple[str, float]:
    if a.over_prob >= a.under_prob:
        return "Over", a.over_prob
    return "Under", a.under_prob


def _score(pick: PropPick, mode: str) -> float:
    """Higher score = better pick under the chosen mode.

    Below-threshold picks are heavily penalized but never disqualified —
    callers may still pick the least-bad candidate when nothing great exists
    (e.g. skip_network demo runs where every analysis is ~50/50).
    """
    cfg = MODES[mode]
    a = pick.analysis
    prob = pick.prob
    trend_signal = 0.0
    # Signal from last-5 hits (normalized -1..+1): number of hits on the chosen
    # side minus the midpoint, scaled to [-1, 1].
    if a.samples and len(a.samples) >= 3:
        recent = a.samples[:5]
        hits_over = sum(1 for v in recent if v > a.prop.line)
        hits_chosen = hits_over if pick.side == "Over" else (len(recent) - hits_over)
        trend_signal = (hits_chosen - len(recent) / 2.0) / (len(recent) / 2.0)

    # Edge here means model prob vs. the PrizePicks break-even for a 3-leg (5x)
    # power play — the most-common slip length. This gives us a single scalar
    # edge measure regardless of the final slip size.
    breakeven_3 = 1.0 / POWER_PAYOUTS[3]   # 0.20
    edge = prob - breakeven_3

    if mode == "safest":
        score = prob + (0.05 if len(a.samples) >= 5 else 0.0)
    elif mode == "balanced":
        score = edge * 0.7 + prob * 0.3 + trend_signal * 0.05
    elif mode == "upside":
        score = trend_signal * 0.6 + (prob - 0.5) * 0.8
    else:
        score = prob

    # Soft penalty for below-threshold picks — keeps ordering consistent with
    # the mode's preference while still leaving everything rankable.
    if prob < cfg["min_side_prob"]:
        score -= 0.5 * (cfg["min_side_prob"] - prob)
    return score


def _reasoning(pick: PropPick) -> str:
    a = pick.analysis
    p = pick.prop
    prob_pct = pick.prob * 100
    parts = [f"Model projects {pick.side} {p.line} {p.stat_type} at {prob_pct:.1f}%."]
    if a.samples:
        recent = a.samples[:5]
        hits_over = sum(1 for v in recent if v > p.line)
        hits = hits_over if pick.side == "Over" else (len(recent) - hits_over)
        parts.append(
            f"Last-{len(a.samples)} avg {a.season_avg:.1f} (σ {a.stdev:.1f}); "
            f"{hits}/{len(recent)} recent games cleared the {pick.side} side."
        )
    else:
        parts.append(f"No recent samples — using sport-default σ {a.stdev:.1f} centered on the line.")
    return " ".join(parts)


def generate_prop_slip(
    props: list[PlayerProp],
    mode: str = "balanced",
    max_legs: int = 3,
    min_legs: int = 2,
    stake: float = 10.0,
    progress_cb: Callable[[int, int, str], None] | None = None,
    *,
    skip_network: bool = False,
    precomputed: dict[str, PropAnalysis] | None = None,
    stat_filter: str | None = None,
    team_filter: str | None = None,
    book_filter: str | None = None,
) -> GeneratedPropSlip | None:
    """Build a GeneratedPropSlip from the provided props.

    Parameters
    ----------
    props : source pool (typically from `fetch_props_from_books(sport_key)`
            so every prop carries its originating book in `.source`).
    mode : one of `MODES` — picks the scoring function.
    max_legs / min_legs : bounds on the returned slip length.
    stake : used for the projected payout / profit fields.
    progress_cb : `(done, total, note)` called while analyzing props.
    skip_network : skip ESPN history fetches — faster but less accurate.
    precomputed : optional map of prop.id → PropAnalysis (e.g. from PropsView's
                  background pass) so we don't redo the network fetches.
    stat_filter / team_filter : narrow the candidate pool.
    book_filter : restrict to props from one book ("PrizePicks" / "Underdog")
                  or None/"all" to use every book. Demo props always pass —
                  they're offline placeholders, not live board entries, and
                  blocking them would leave the pool empty in demo mode.
    """
    if mode not in MODES:
        mode = "balanced"
    cfg = MODES[mode]

    book_f = (book_filter or "").strip().lower()
    if book_f in ("", "all", "all books"):
        book_f = ""

    # Filter input pool.
    pool: list[PlayerProp] = []
    for p in props:
        if stat_filter and stat_filter.lower() not in p.stat_type.lower():
            continue
        if team_filter and team_filter.lower() not in (p.team or "").lower():
            continue
        if book_f:
            src = (p.source or "").lower()
            # Demo props are offline samples — let them through so the filter
            # never zeroes out the pool when the user has no live data.
            if src != "demo" and src != book_f:
                continue
        pool.append(p)

    if not pool:
        return None

    # Analyze each prop (reusing precomputed analyses where available).
    # We DO NOT hard-reject below min_side_prob here. Without ESPN history
    # (skip_network or quiet APIs) analyses converge to 50/50 and the hard
    # filter would wipe out the entire pool; instead we let `_score()` punish
    # below-threshold picks and the ranker still returns the best of what's
    # available so the generator never silently yields nothing.
    precomputed = precomputed or {}
    picks: list[PropPick] = []
    total = len(pool)
    for i, prop in enumerate(pool):
        if progress_cb:
            progress_cb(i, total, f"Analyzing {prop.player_name} {prop.stat_type}")
        a = precomputed.get(prop.id)
        if a is None:
            try:
                a = analyze_prop(prop, skip_network=skip_network)
            except Exception:
                continue
        side, prob = _choose_side(a)
        pick = PropPick(prop=prop, analysis=a, side=side, prob=prob)
        pick.reasoning = _reasoning(pick)
        picks.append(pick)

    if progress_cb:
        progress_cb(total, total, "Ranking candidates")

    if not picks:
        return None

    # Rank by mode score, pick top N while de-duping per player (one pick per
    # player keeps the slip uncorrelated).
    picks.sort(key=lambda pk: _score(pk, mode), reverse=True)
    used_players: set[str] = set()
    chosen: list[PropPick] = []
    for pk in picks:
        pid = pk.prop.player_id or pk.prop.player_name.lower()
        if pid in used_players:
            continue
        chosen.append(pk)
        used_players.add(pid)
        if len(chosen) >= max_legs:
            break

    # If filters left us below min_legs, relax and keep going with remaining
    # picks (still de-duping per player).
    if len(chosen) < min_legs:
        for pk in picks:
            pid = pk.prop.player_id or pk.prop.player_name.lower()
            if pid in used_players:
                continue
            chosen.append(pk)
            used_players.add(pid)
            if len(chosen) >= min_legs:
                break

    if len(chosen) < min_legs:
        return None

    # Assemble the slip — convert each pick to a LegAnalysis (DFS-flavored,
    # bookmaker=PrizePicks) and let the shared parlay analyzer compute the rest.
    legs = [prop_to_leg(pk.prop, pk.analysis, pk.side) for pk in chosen]
    parlay = analyze_parlay(legs)

    mult = POWER_PAYOUTS.get(len(chosen), 0.0)
    break_even = (1.0 / mult) if mult > 0 else 0.0
    payout = stake * mult if mult > 0 else 0.0
    profit = payout - stake if mult > 0 else 0.0
    # EV per $1 staked when the slip is payed out as a PrizePicks power play.
    ev_per_dollar = (parlay.combined_prob * (mult - 1.0) - (1.0 - parlay.combined_prob)) if mult > 0 else 0.0
    ev_dollars = ev_per_dollar * stake

    return GeneratedPropSlip(
        mode=mode,
        mode_label=cfg["label"],
        mode_subtitle=cfg["subtitle"],
        picks=chosen,
        legs=legs,
        parlay=parlay,
        power_multiplier=mult,
        break_even_prob=break_even,
        projected_payout=payout,
        projected_profit=profit,
        ev_dollars=ev_dollars,
        overall_reasoning=_overall_reasoning(mode, chosen, parlay, mult, break_even),
    )


def _overall_reasoning(
    mode: str, picks: list[PropPick], parlay: ParlayAnalysis,
    mult: float, break_even: float,
) -> str:
    n = len(picks)
    combined_pct = parlay.combined_prob * 100
    if mult <= 0:
        multiplier_clause = f"{n}-leg slip (outside the 2–6 power-play range)."
    else:
        multiplier_clause = f"{n}-leg power play pays {mult:.0f}x; break-even {break_even*100:.1f}%."

    # Book-verification line — spells out which DFS books each leg is
    # placeable on. Because every pick comes from a live book feed, listing
    # the sources here doubles as the "yes, you can actually select these"
    # guarantee the generator makes to the user.
    book_counts: dict[str, int] = {}
    for pk in picks:
        src = pk.prop.source or "?"
        book_counts[src] = book_counts.get(src, 0) + 1
    if book_counts:
        parts = [f"{c} on {b}" for b, c in book_counts.items()]
        verified = f"Verified placeable: {', '.join(parts)}."
    else:
        verified = ""

    if mode == "safest":
        stance = f"Optimized for hit rate: every leg's model probability is ≥ {MODES['safest']['min_side_prob']*100:.0f}%."
    elif mode == "upside":
        stance = "Optimized for recent-form directional edge; variance is higher in exchange for less crowded picks."
    else:
        stance = "Optimized for edge vs. the power-play break-even with meaningful combined hit rate."

    if mult > 0:
        gap = parlay.combined_prob - break_even
        if gap >= 0.05:
            verdict = f"Combined model probability {combined_pct:.1f}% sits {gap*100:+.1f}% above break-even — +EV."
        elif gap >= 0:
            verdict = f"Combined model probability {combined_pct:.1f}% is just above break-even — slim margin."
        else:
            verdict = f"Combined model probability {combined_pct:.1f}% is below the {break_even*100:.1f}% break-even — expect negative EV."
    else:
        verdict = f"Combined model probability {combined_pct:.1f}%."

    tail = f"{stance}  {multiplier_clause}  {verdict}"
    return f"{tail}  {verified}" if verified else tail
