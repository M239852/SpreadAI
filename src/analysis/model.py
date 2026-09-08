"""SpreadAI probability engine (model v2).

This module is the single place where probabilities are *estimated*. The rest
of `src.analysis` (probability.py, props.py, research.py, markets.py) builds
user-facing dataclasses on top of it; the UI never calls this module directly.

Pipeline for a team-market leg (moneyline / spread / total)
-----------------------------------------------------------
1. **Per-book de-vig.** Every sportsbook's market is de-vigged on its own
   (power method for moneylines, multiplicative for spreads/totals) so a book
   with a fat hold doesn't drag the consensus. Only books quoting the
   *consensus point* (weighted median line) contribute to the price consensus.
2. **Weighted consensus in logit space.** Books are weighted by a sharpness
   prior (Pinnacle/Circa > DK/FD > offshore recreational). The weighted mean of
   the logits is the market prior; the weighted dispersion is the market's own
   uncertainty.
3. **Cross-market fusion.** Moneyline and spread are two views of the same
   latent margin distribution. We convert one into the other through a normal
   margin model with a sport-specific standard deviation and fuse the two
   estimates by inverse-variance weighting. This is a structural, market-
   internal check that catches stale or mispriced sides.
4. **Point shift.** If the best-priced book posts a different line than the
   consensus line (e.g. -2.5 vs -3.5), the prior is shifted through the same
   margin model instead of pretending the two lines are equivalent.
5. **Evidence fusion.** Research factors (injuries, form, rest, head-to-head)
   are `Evidence` items with a signed signal, a maximum logit scale and a
   confidence. Their contributions are summed in logit space, discounted by
   the sport's *market efficiency* (public information is mostly priced in),
   and soft-capped with tanh so a pile of weak signals can't produce an
   absurd probability.
6. **Uncertainty + calibration.** Market dispersion and evidence uncertainty
   add in quadrature to an 80% interval around the estimate; an optional
   temperature/shift calibration is applied last.

Player props use `estimate_prop`: exponentially-weighted recent samples are
shrunk toward the posted line (the book's projection is a strong prior), the
distribution is chosen by the shape of the stat (normal / Poisson / negative
binomial), and an empirical hit-rate estimate is blended in as a guard against
distribution misspecification.

Parlays use a Gaussian copula so same-game legs are no longer treated as
independent (moneyline + spread on the same side is ~0.85 correlated; a
favorite covering is mildly correlated with the over in scoring sports).

Everything here is pure Python, deterministic (seeded Monte Carlo) and free
of UI or network code so it can be unit tested in isolation.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..api.odds_api import Game, Bookmaker, Outcome
from ..utils.formatters import american_to_implied

MODEL_VERSION = "2.0"


# =============================================================================
# Math utilities
# =============================================================================

def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def logit(p: float) -> float:
    p = clamp(p, 1e-6, 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def expit(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# Acklam's rational approximation of the inverse normal CDF (rel. err < 1.2e-9),
# followed by one Newton step for good measure.
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00)


def norm_ppf(p: float) -> float:
    p = clamp(p, 1e-12, 1.0 - 1e-12)
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
            (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    # Newton refinement
    e = norm_cdf(x) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    return x - u / (1.0 + x * u / 2.0)


def weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2.0:
            return v
    return pairs[-1][0] if pairs else 0.0


# =============================================================================
# Sport parameters
# =============================================================================

@dataclass(frozen=True)
class SportParams:
    key: str
    margin_sd: float          # SD of final margin (home - away)
    total_sd: float           # SD of combined score
    efficiency: float         # Share of public research already priced in (0..1)
    integer_scores: bool      # Whether pushes on whole-number lines are possible
    fav_over_corr: float      # Corr(favorite covers, over) in the same game
    prop_prior_n: float       # Pseudo-count shrinking prop samples toward the line
    prop_cv: float            # Default coefficient of variation for props
    cross_market: bool        # Moneyline <-> spread fusion is valid (2-way only)
    rest_matters: bool        # Back-to-back fatigue is a real signal
    label: str = ""


SPORTS: dict[str, SportParams] = {
    "americanfootball_nfl":   SportParams("americanfootball_nfl",   13.5, 13.6, 0.80, True, 0.10, 5.0, 0.30, True,  False, "NFL"),
    "americanfootball_ncaaf": SportParams("americanfootball_ncaaf", 16.5, 16.0, 0.65, True, 0.12, 5.0, 0.32, True,  False, "NCAAF"),
    "basketball_nba":         SportParams("basketball_nba",         12.0, 18.5, 0.75, True, 0.06, 4.0, 0.22, True,  True,  "NBA"),
    "basketball_ncaab":       SportParams("basketball_ncaab",       11.0, 15.0, 0.55, True, 0.06, 4.0, 0.25, True,  False, "NCAAB"),
    "baseball_mlb":           SportParams("baseball_mlb",            4.1,  4.3, 0.70, True, 0.04, 8.0, 0.55, True,  False, "MLB"),
    "icehockey_nhl":          SportParams("icehockey_nhl",           2.3,  2.4, 0.70, True, 0.05, 8.0, 0.45, True,  True,  "NHL"),
    "soccer_epl":             SportParams("soccer_epl",              1.7,  1.7, 0.75, True, 0.05, 8.0, 0.50, False, False, "EPL"),
    "mma_mixed_martial_arts": SportParams("mma_mixed_martial_arts",  1.0,  1.0, 0.60, False, 0.0, 6.0, 0.40, False, False, "MMA"),
}
_DEFAULT_SPORT = SportParams("default", 10.0, 10.0, 0.70, True, 0.05, 6.0, 0.30, True, False, "")


def sport_params(sport_key: str | None) -> SportParams:
    return SPORTS.get(sport_key or "", _DEFAULT_SPORT)


# Sharpness prior per bookmaker (The Odds API keys). Unknown books get 0.8.
BOOK_WEIGHTS: dict[str, float] = {
    "pinnacle": 3.0, "circasports": 2.5, "betonlineag": 1.5, "bookmaker": 1.5,
    "lowvig": 1.2, "superbook": 1.1, "draftkings": 1.0, "fanduel": 1.0,
    "betmgm": 1.0, "caesars": 1.0, "williamhill_us": 1.0, "espnbet": 0.9,
    "fanatics": 0.9, "pointsbetus": 0.9, "betrivers": 0.9, "hardrockbet": 0.9,
    "unibet_us": 0.8, "bovada": 0.8, "wynnbet": 0.8, "betparx": 0.7,
    "ballybet": 0.7, "mybookieag": 0.6, "betus": 0.6,
}


def book_weight(book: Bookmaker) -> float:
    key = (book.key or "").lower()
    if key in BOOK_WEIGHTS:
        return BOOK_WEIGHTS[key]
    title = (book.title or "").lower().replace(" ", "")
    for k, w in BOOK_WEIGHTS.items():
        if k in title:
            return w
    return 0.8


# Logit-space noise of a single fair consensus price, before dispersion.
MARKET_BASE_SD = 0.10
# Structural error of the cross-market (margin model) estimate.
CROSS_MARKET_SD = 0.25
# Soft cap on the total evidence shift (logit units); 0.6 ≈ ±14% at p=0.5.
EVIDENCE_CAP = 0.60
# Extra logit uncertainty that scales with how far evidence moved the prior.
EVIDENCE_MOVE_UNCERTAINTY = 0.35


# =============================================================================
# Settings (from config.json)
# =============================================================================

@dataclass
class ModelSettings:
    use_cross_market: bool = True
    use_correlation: bool = True
    kelly_conservative: bool = True
    calibration_temperature: float = 1.0   # >1 shrinks toward 50%
    calibration_shift: float = 0.0         # logit offset
    evidence_discount: float | None = None # override (1 - efficiency)


_SETTINGS = ModelSettings()


def configure(settings: ModelSettings) -> None:
    global _SETTINGS
    _SETTINGS = settings


def current_settings() -> ModelSettings:
    return _SETTINGS


def settings_from_config(cfg: dict) -> ModelSettings:
    m = cfg.get("model") or {}
    try:
        temp = float(m.get("calibration_temperature", 1.0))
    except (TypeError, ValueError):
        temp = 1.0
    try:
        shift = float(m.get("calibration_shift", 0.0))
    except (TypeError, ValueError):
        shift = 0.0
    disc = m.get("evidence_discount")
    try:
        disc = None if disc in (None, "") else clamp(float(disc), 0.0, 1.0)
    except (TypeError, ValueError):
        disc = None
    return ModelSettings(
        use_cross_market=bool(m.get("use_cross_market", True)),
        use_correlation=bool(m.get("use_correlation", True)),
        kelly_conservative=bool(m.get("kelly_conservative", True)),
        calibration_temperature=max(0.25, temp),
        calibration_shift=shift,
        evidence_discount=disc,
    )


def calibrate(p: float, settings: ModelSettings | None = None) -> float:
    s = settings or _SETTINGS
    if s.calibration_temperature == 1.0 and s.calibration_shift == 0.0:
        return p
    return expit((logit(p) - s.calibration_shift) / s.calibration_temperature)


# =============================================================================
# De-vig
# =============================================================================

def devig(implieds: Sequence[float], method: str = "power") -> list[float]:
    """Remove the vig from a full market (2-way or n-way) of implied probs.

    multiplicative : p_i / sum(p)                        (classic)
    power          : p_i ** k with k chosen so sum == 1  (corrects favorite-
                     longshot bias; the standard choice for moneylines)
    additive       : p_i - overround / n                 (rarely best; kept
                     for comparison)
    """
    imps = [max(1e-6, float(p)) for p in implieds]
    if not imps:
        return []
    total = sum(imps)
    if len(imps) == 1:
        return [1.0]
    if method == "additive":
        over = (total - 1.0) / len(imps)
        out = [p - over for p in imps]
        if min(out) > 0:
            return out
        method = "multiplicative"
    if method == "power" and total > 1.0:
        lo, hi = 1.0, 8.0
        for _ in range(60):
            k = (lo + hi) / 2.0
            s = sum(p ** k for p in imps)
            if s > 1.0:
                lo = k
            else:
                hi = k
        k = (lo + hi) / 2.0
        out = [p ** k for p in imps]
        s = sum(out)
        return [p / s for p in out]
    return [p / total for p in imps]


# =============================================================================
# Market consensus
# =============================================================================

@dataclass
class MarketConsensus:
    market: str
    selection: str
    point: float | None
    fair_prob: float
    logit_mean: float
    logit_sd: float
    n_books: int
    n_outcomes: int
    method: str
    book_fair: dict[str, float] = field(default_factory=dict)   # title -> fair prob
    weight_total: float = 0.0
    points_seen: dict[float, int] = field(default_factory=dict)

    @property
    def market_var(self) -> float:
        """Logit variance of the consensus.

        Base noise grows when few books quote the market (a lone book is a
        weaker consensus than four books that agree), plus the observed
        dispersion across books divided by the book count.
        """
        n = max(1, self.n_books)
        return (MARKET_BASE_SD ** 2) * (1.0 + 2.0 / n) + (self.logit_sd ** 2) / n


def _norm_name(s: str) -> str:
    return (s or "").strip().lower()


def _is_selection(o: Outcome, selection: str) -> bool:
    return _norm_name(o.name) == _norm_name(selection)


def _market_rows(game: Game, market_key: str, selection: str) -> list[tuple[Bookmaker, Outcome, list[Outcome]]]:
    """For each book: (book, our outcome, all outcomes on the same line)."""
    rows: list[tuple[Bookmaker, Outcome, list[Outcome]]] = []
    for bk in game.bookmakers:
        mk = bk.market(market_key)
        if not mk:
            continue
        mine = next((o for o in mk.outcomes if _is_selection(o, selection)), None)
        if mine is None:
            continue
        if market_key == "spreads":
            same_line = [o for o in mk.outcomes
                         if o is mine or (o.point is not None and mine.point is not None
                                          and abs(o.point + mine.point) < 1e-6
                                          and not _is_selection(o, selection))]
        elif market_key == "totals":
            same_line = [o for o in mk.outcomes
                         if o is mine or (o.point is not None and mine.point is not None
                                          and abs(o.point - mine.point) < 1e-6
                                          and not _is_selection(o, selection))]
        else:
            same_line = list(mk.outcomes)
        # Deduplicate outcomes by name (some feeds repeat alternates).
        seen: set[str] = set()
        uniq: list[Outcome] = []
        for o in same_line:
            n = _norm_name(o.name)
            if n in seen:
                continue
            seen.add(n)
            uniq.append(o)
        rows.append((bk, mine, uniq))
    return rows


def consensus(game: Game, market_key: str, selection: str, method: str | None = None) -> MarketConsensus | None:
    """Weighted, per-book de-vigged consensus for one selection."""
    rows = _market_rows(game, market_key, selection)
    if not rows:
        return None
    if method is None:
        method = "power" if market_key == "h2h" else "multiplicative"

    point: float | None = None
    points_seen: dict[float, int] = {}
    if market_key in ("spreads", "totals"):
        pts = [(o.point, book_weight(bk)) for bk, o, _ in rows if o.point is not None]
        if pts:
            point = weighted_median([p for p, _ in pts], [w for _, w in pts])
            for p, _ in pts:
                points_seen[p] = points_seen.get(p, 0) + 1
            rows = [r for r in rows if r[1].point is not None and abs(r[1].point - point) < 1e-6]

    logits: list[float] = []
    weights: list[float] = []
    book_fair: dict[str, float] = {}
    n_outcomes = 2
    for bk, mine, line in rows:
        if len(line) < 2:
            # One-sided quote: can't de-vig; treat as an already-fair price with low weight.
            fair = american_to_implied(mine.price)
            w = book_weight(bk) * 0.5
        else:
            imps = [american_to_implied(o.price) for o in line]
            fairs = devig(imps, method)
            idx = next(i for i, o in enumerate(line) if o is mine)
            fair = fairs[idx]
            w = book_weight(bk)
            n_outcomes = max(n_outcomes, len(line))
        fair = clamp(fair, 0.005, 0.995)
        logits.append(logit(fair))
        weights.append(w)
        book_fair[bk.title] = fair

    wt = sum(weights)
    mean = sum(l * w for l, w in zip(logits, weights)) / wt
    var = sum(w * (l - mean) ** 2 for l, w in zip(logits, weights)) / wt if len(logits) > 1 else 0.0
    return MarketConsensus(
        market=market_key, selection=selection, point=point,
        fair_prob=expit(mean), logit_mean=mean, logit_sd=math.sqrt(var),
        n_books=len(logits), n_outcomes=n_outcomes, method=method,
        book_fair=book_fair, weight_total=wt, points_seen=points_seen,
    )


# =============================================================================
# Margin model (cross-market + point shift)
# =============================================================================

def cover_probability(mu: float, sd: float, spread: float, integer_scores: bool = True) -> tuple[float, float]:
    """P(win), P(push) for a side with handicap `spread` when its margin ~ N(mu, sd).

    The side covers when margin + spread > 0. For whole-number spreads in
    integer-scoring sports the push mass is the probability the margin lands
    exactly on -spread (continuity-corrected).
    """
    sd = max(sd, 1e-6)
    threshold = -spread
    if integer_scores and abs(threshold - round(threshold)) < 1e-9:
        t = round(threshold)
        p_win = 1.0 - norm_cdf((t + 0.5 - mu) / sd)
        p_push = norm_cdf((t + 0.5 - mu) / sd) - norm_cdf((t - 0.5 - mu) / sd)
        return p_win, max(0.0, p_push)
    if integer_scores:
        # Half-point line: margin > threshold  <=> margin >= ceil(threshold)
        t = math.ceil(threshold) - 0.5
        return 1.0 - norm_cdf((t - mu) / sd), 0.0
    return 1.0 - norm_cdf((threshold - mu) / sd), 0.0


def margin_from_cover(p_cover: float, sd: float, spread: float) -> float:
    """Invert `cover_probability` (ignoring push mass) to recover the mean margin."""
    return -spread + sd * norm_ppf(clamp(p_cover, 0.01, 0.99))


def over_probability(mu: float, sd: float, line: float, integer_scores: bool = True) -> tuple[float, float]:
    """P(over), P(push) for a total ~ N(mu, sd)."""
    sd = max(sd, 1e-6)
    if integer_scores and abs(line - round(line)) < 1e-9:
        t = round(line)
        p_over = 1.0 - norm_cdf((t + 0.5 - mu) / sd)
        p_push = norm_cdf((t + 0.5 - mu) / sd) - norm_cdf((t - 0.5 - mu) / sd)
        return p_over, max(0.0, p_push)
    if integer_scores:
        t = math.ceil(line) - 0.5
        return 1.0 - norm_cdf((t - mu) / sd), 0.0
    return 1.0 - norm_cdf((line - mu) / sd), 0.0


def total_from_over(p_over: float, sd: float, line: float) -> float:
    return line + sd * norm_ppf(clamp(p_over, 0.01, 0.99))


@dataclass
class CrossMarketEstimate:
    prob: float
    source: str          # e.g. "moneyline → spread"
    mu: float            # implied mean margin (or total)
    sd: float
    push_prob: float = 0.0
    note: str = ""

    @property
    def var(self) -> float:
        return CROSS_MARKET_SD ** 2


def _side_info(game: Game, selection: str) -> tuple[bool | None, str | None]:
    """(is_home, opponent) for a team selection; (None, None) for Over/Under."""
    if _norm_name(selection) == _norm_name(game.home_team):
        return True, game.away_team
    if _norm_name(selection) == _norm_name(game.away_team):
        return False, game.home_team
    return None, None


def cross_market_estimate(game: Game, market_key: str, selection: str, point: float | None,
                          sp: SportParams | None = None) -> CrossMarketEstimate | None:
    """Estimate a spread from the moneyline (or vice-versa) via the margin model."""
    sp = sp or sport_params(game.sport_key)
    if not sp.cross_market:
        return None
    is_home, _ = _side_info(game, selection)
    if is_home is None:
        return None   # totals have no cross-market counterpart

    if market_key == "spreads":
        if point is None:
            return None
        ml = consensus(game, "h2h", selection)
        if ml is None or ml.n_outcomes != 2:
            return None
        mu = sp.margin_sd * norm_ppf(ml.fair_prob)
        p_win, p_push = cover_probability(mu, sp.margin_sd, point, sp.integer_scores)
        denom = max(1e-6, 1.0 - p_push)
        return CrossMarketEstimate(
            prob=clamp(p_win / denom, 0.01, 0.99), source="moneyline → spread",
            mu=mu, sd=sp.margin_sd, push_prob=p_push,
            note=f"ML {ml.fair_prob*100:.1f}% implies a {mu:+.1f} margin; covering {point:+g} ≈ {p_win/denom*100:.1f}%",
        )
    if market_key == "h2h":
        sc = consensus(game, "spreads", selection)
        if sc is None or sc.point is None:
            return None
        mu = margin_from_cover(sc.fair_prob, sp.margin_sd, sc.point)
        p_win, p_tie = cover_probability(mu, sp.margin_sd, 0.0, sp.integer_scores)
        denom = max(1e-6, 1.0 - p_tie)
        return CrossMarketEstimate(
            prob=clamp(p_win / denom, 0.01, 0.99), source="spread → moneyline",
            mu=mu, sd=sp.margin_sd, push_prob=p_tie,
            note=f"Spread {sc.point:+g} at {sc.fair_prob*100:.1f}% implies a {mu:+.1f} margin; winning ≈ {p_win/denom*100:.1f}%",
        )
    return None


def point_shift(prob: float, market_key: str, selection: str, from_point: float, to_point: float,
                sp: SportParams) -> tuple[float, float]:
    """Move a probability from one line to another through the margin/total model.

    Returns (shifted_prob, push_prob at the new line).
    """
    if abs(from_point - to_point) < 1e-9:
        return prob, 0.0
    if market_key == "spreads":
        mu = margin_from_cover(prob, sp.margin_sd, from_point)
        p_win, p_push = cover_probability(mu, sp.margin_sd, to_point, sp.integer_scores)
        return clamp(p_win / max(1e-6, 1.0 - p_push), 0.01, 0.99), p_push
    if market_key == "totals":
        is_over = _norm_name(selection).startswith("over")
        p_over = prob if is_over else 1.0 - prob
        mu = total_from_over(p_over, sp.total_sd, from_point)
        p_o, p_push = over_probability(mu, sp.total_sd, to_point, sp.integer_scores)
        p_o = p_o / max(1e-6, 1.0 - p_push)
        return clamp(p_o if is_over else 1.0 - p_o, 0.01, 0.99), p_push
    return prob, 0.0


# =============================================================================
# Evidence fusion
# =============================================================================

@dataclass
class Evidence:
    name: str
    signal: float               # -1..+1, sign favors the selection
    scale: float                # max logit contribution at |signal| = 1, confidence = 1
    confidence: float = 1.0     # 0..1 — how much to trust the signal
    description: str = ""
    category: str = "research"

    @property
    def contribution(self) -> float:
        return clamp(self.signal, -1.0, 1.0) * self.scale * clamp(self.confidence, 0.0, 1.0)


@dataclass
class Fusion:
    prior_logit: float
    prior_sd: float
    raw_shift: float
    discount: float
    applied_shift: float
    logit: float
    sd: float
    evidence: list[Evidence] = field(default_factory=list)


def fuse_evidence(prior_logit: float, prior_sd: float, evidence: Iterable[Evidence], sp: SportParams,
                  settings: ModelSettings | None = None, cap: float = EVIDENCE_CAP) -> Fusion:
    ev = list(evidence)
    s = settings or _SETTINGS
    discount = s.evidence_discount if s.evidence_discount is not None else (1.0 - sp.efficiency)
    raw = sum(e.contribution for e in ev)
    shifted = raw * discount
    applied = cap * math.tanh(shifted / cap) if cap > 0 else 0.0
    # Uncertainty: low-confidence evidence adds variance, and so does moving far.
    ev_var = sum((abs(e.contribution) * (1.0 - clamp(e.confidence, 0.0, 1.0)) * discount) ** 2 for e in ev)
    ev_var += (EVIDENCE_MOVE_UNCERTAINTY * abs(applied)) ** 2
    sd = math.sqrt(prior_sd ** 2 + ev_var)
    return Fusion(prior_logit, prior_sd, raw, discount, applied, prior_logit + applied, sd, ev)


def confidence_from_sd(sd: float) -> float:
    """Map a logit standard deviation onto a 0..1 confidence score."""
    return 1.0 / (1.0 + (sd / 0.25) ** 2)


# =============================================================================
# Leg estimate (team markets)
# =============================================================================

@dataclass
class LegEstimate:
    prob: float
    prob_low: float
    prob_high: float
    confidence: float
    market_prob: float                  # direct consensus at the consensus point
    prior_prob: float                   # after cross-market fusion + point shift
    cross_prob: float | None
    consensus: MarketConsensus
    cross: CrossMarketEstimate | None
    fusion: Fusion
    push_prob: float = 0.0
    point: float | None = None
    notes: list[str] = field(default_factory=list)
    model_version: str = MODEL_VERSION

    @property
    def evidence_shift_pct(self) -> float:
        """Probability moved by evidence (final - prior), in probability units."""
        return self.prob - self.prior_prob


def estimate_leg(game: Game, market_key: str, selection: str, evidence: Iterable[Evidence] = (),
                 *, leg_point: float | None = None, settings: ModelSettings | None = None) -> LegEstimate | None:
    s = settings or _SETTINGS
    sp = sport_params(game.sport_key)
    cons = consensus(game, market_key, selection)
    if cons is None:
        return None
    notes: list[str] = []

    prior_logit = cons.logit_mean
    prior_var = cons.market_var
    notes.append(
        f"Consensus {cons.fair_prob*100:.1f}% from {cons.n_books} book{'s' if cons.n_books != 1 else ''}"
        + (f" at {cons.point:+g}" if cons.point is not None and market_key == "spreads" else "")
        + (f" at {cons.point:g}" if cons.point is not None and market_key == "totals" else "")
        + f" ({cons.method} de-vig, dispersion ±{cons.logit_sd:.2f} logit)"
    )

    cross: CrossMarketEstimate | None = None
    if s.use_cross_market:
        cross = cross_market_estimate(game, market_key, selection, cons.point, sp)
        if cross is not None:
            w_m = 1.0 / prior_var
            w_c = 1.0 / cross.var
            fused = (w_m * prior_logit + w_c * logit(cross.prob)) / (w_m + w_c)
            prior_var = 1.0 / (w_m + w_c)
            gap = cross.prob - cons.fair_prob
            notes.append(f"{cross.source}: {cross.prob*100:.1f}% ({gap*100:+.1f}% vs direct market; weight {w_c/(w_m+w_c)*100:.0f}%)")
            prior_logit = fused

    push_prob = 0.0
    target_point = leg_point if leg_point is not None else cons.point
    if market_key in ("spreads", "totals") and cons.point is not None and target_point is not None \
            and abs(target_point - cons.point) > 1e-9:
        before = expit(prior_logit)
        shifted, push_prob = point_shift(before, market_key, selection, cons.point, target_point, sp)
        prior_logit = logit(shifted)
        # Off-consensus lines are quoted by fewer books: widen the prior a little.
        prior_var += (0.06 * abs(target_point - cons.point) / max(1.0, sp.margin_sd / 3.0)) ** 2
        notes.append(f"Line shift {cons.point:g} → {target_point:g}: {before*100:.1f}% → {shifted*100:.1f}%")
    elif market_key in ("spreads", "totals") and cons.point is not None and sp.integer_scores \
            and abs(cons.point - round(cons.point)) < 1e-9:
        # Whole-number line at consensus: estimate push mass from the model.
        if market_key == "spreads":
            mu = margin_from_cover(expit(prior_logit), sp.margin_sd, cons.point)
            _, push_prob = cover_probability(mu, sp.margin_sd, cons.point, True)
        else:
            is_over = _norm_name(selection).startswith("over")
            p_over = expit(prior_logit) if is_over else 1.0 - expit(prior_logit)
            mu = total_from_over(p_over, sp.total_sd, cons.point)
            _, push_prob = over_probability(mu, sp.total_sd, cons.point, True)
        if push_prob > 0.005:
            notes.append(f"Whole-number line: ~{push_prob*100:.1f}% push probability")

    fusion = fuse_evidence(prior_logit, math.sqrt(prior_var), evidence, sp, s)
    if fusion.evidence:
        active = [e for e in fusion.evidence if abs(e.contribution) > 1e-9]
        if active:
            notes.append(
                f"Evidence: {len(active)} factor{'s' if len(active) != 1 else ''} → {fusion.raw_shift:+.2f} logit raw, "
                f"× {fusion.discount:.2f} efficiency discount → {fusion.applied_shift:+.2f} applied"
            )

    p = calibrate(clamp(expit(fusion.logit), 0.01, 0.99), s)
    lo = clamp(expit(fusion.logit - 1.2816 * fusion.sd), 0.005, 0.995)
    hi = clamp(expit(fusion.logit + 1.2816 * fusion.sd), 0.005, 0.995)
    return LegEstimate(
        prob=p, prob_low=min(lo, p), prob_high=max(hi, p),
        confidence=confidence_from_sd(fusion.sd),
        market_prob=cons.fair_prob,
        prior_prob=expit(prior_logit),
        cross_prob=cross.prob if cross else None,
        consensus=cons, cross=cross, fusion=fusion,
        push_prob=push_prob, point=target_point, notes=notes,
    )


# =============================================================================
# Player props
# =============================================================================

_DISCRETE_HINTS = (
    "home run", "hits", "goal", "3-pt", "3-pointer", "three", "td", "touchdown",
    "reception", "interception", "stolen", "steal", "block", "turnover", "walk",
    "runs", "rbi", "strikeout", "assist", "double", "triple", "sack", "tackle",
    "shot", "save", "point", "rebound", "field goal", "attempt", "completion", "carr",
)
_CONTINUOUS_HINTS = ("yard", "minute", "time", "distance", "fantasy", "rating")


def _looks_discrete(stat_type: str, samples: Sequence[float]) -> bool:
    st = (stat_type or "").lower()
    if any(h in st for h in _CONTINUOUS_HINTS):
        return False
    if samples and all(abs(v - round(v)) < 1e-9 for v in samples):
        return True
    return any(h in st for h in _DISCRETE_HINTS)


def _poisson_sf(k: int, mu: float) -> float:
    """P(X >= k) for Poisson(mu)."""
    if k <= 0:
        return 1.0
    mu = max(mu, 1e-9)
    term = math.exp(-mu)
    cdf = term
    for i in range(1, k):
        term *= mu / i
        cdf += term
    return max(0.0, 1.0 - cdf)


def _negbin_sf(k: int, mu: float, var: float) -> float:
    """P(X >= k) for a negative binomial with mean mu and variance var (> mu)."""
    if k <= 0:
        return 1.0
    mu = max(mu, 1e-9)
    if var <= mu * 1.02:
        return _poisson_sf(k, mu)
    r = mu * mu / (var - mu)
    p = r / (r + mu)          # success prob in the NB(r, p) parameterization
    pmf = p ** r               # P(X = 0)
    cdf = pmf
    for i in range(1, k):
        pmf *= (r + i - 1) / i * (1.0 - p)
        cdf += pmf
    return max(0.0, 1.0 - cdf)


@dataclass
class PropEstimate:
    prob_over: float
    prob_low: float
    prob_high: float
    confidence: float
    projection: float            # shrunk mean used by the model
    sd: float
    sample_mean: float
    sample_sd: float
    n: int
    n_eff: float
    distribution: str            # normal | poisson | negbin
    hit_rate: float | None       # recency-weighted empirical over-rate
    push_prob: float = 0.0
    notes: list[str] = field(default_factory=list)
    model_version: str = MODEL_VERSION

    @property
    def prob_under(self) -> float:
        return 1.0 - self.prob_over


def estimate_prop(sport_key: str, stat_type: str, line: float, samples: Sequence[float],
                  *, half_life: float = 5.0, settings: ModelSettings | None = None) -> PropEstimate:
    """Probability a player's stat lands over `line`, from recent samples (newest first)."""
    s = settings or _SETTINGS
    sp = sport_params(sport_key)
    xs = [float(v) for v in samples]
    n = len(xs)
    notes: list[str] = []

    # Exponentially-weighted recent mean.
    if n:
        w = [0.5 ** (i / half_life) for i in range(n)]
        wsum = sum(w)
        ew_mean = sum(wi * x for wi, x in zip(w, xs)) / wsum
        n_eff = wsum * wsum / sum(wi * wi for wi in w)
        raw_mean = sum(xs) / n
    else:
        w, wsum, ew_mean, n_eff, raw_mean = [], 0.0, line, 0.0, line

    if n >= 3:
        m = raw_mean
        sample_var = sum((x - m) ** 2 for x in xs) / (n - 1)
    else:
        sample_var = 0.0
    sample_sd = math.sqrt(sample_var)

    # Shrink toward the line (the book's projection is a sharp prior).
    k = sp.prop_prior_n
    mu = (n_eff * ew_mean + k * line) / (n_eff + k)
    sigma0 = max(0.75, sp.prop_cv * abs(line))
    dof = max(n - 1, 0)
    var = (dof * sample_var + k * sigma0 ** 2) / (dof + k)
    sd = math.sqrt(max(var, 0.25))
    if n:
        notes.append(f"Recency-weighted avg {ew_mean:.1f} (n_eff {n_eff:.1f}) shrunk toward line {line:g} → projection {mu:.1f}")
    else:
        notes.append(f"No samples — projection anchored on the line ({line:g}) with sport default σ {sd:.1f}")

    discrete = _looks_discrete(stat_type, xs)
    dist = "normal"
    push = 0.0
    if discrete and mu < 8.0:
        k_over = math.floor(line) + 1          # X > line  <=>  X >= floor(line)+1
        if abs(line - round(line)) < 1e-9:
            # Whole-number line: exact hit is a push.
            k_over = int(round(line)) + 1
        overdispersed = n >= 5 and sample_var > 1.3 * max(raw_mean, 1e-6)
        if overdispersed:
            dist = "negbin"
            p_param = _negbin_sf(k_over, mu, max(var, mu * 1.05))
        else:
            dist = "poisson"
            p_param = _poisson_sf(k_over, mu)
        if abs(line - round(line)) < 1e-9:
            k_line = int(round(line))
            if dist == "poisson":
                push = _poisson_sf(k_line, mu) - _poisson_sf(k_line + 1, mu)
            else:
                push = _negbin_sf(k_line, mu, max(var, mu * 1.05)) - _negbin_sf(k_line + 1, mu, max(var, mu * 1.05))
            p_param = p_param / max(1e-6, 1.0 - push)
    else:
        if discrete:
            p_param, push = over_probability(mu, sd, line, integer_scores=True)
            p_param = p_param / max(1e-6, 1.0 - push)
        else:
            p_param = 1.0 - norm_cdf((line - mu) / sd)
    notes.append(f"{dist} distribution, σ {sd:.1f}")

    hit_rate: float | None = None
    prob = p_param
    if n >= 5:
        hits = sum(wi for wi, x in zip(w, xs) if x > line)
        hit_rate = (hits + 1.0) / (wsum + 2.0)   # Laplace-smoothed, recency weighted
        blend = 0.2 * min(1.0, n / 10.0)
        prob = (1.0 - blend) * p_param + blend * hit_rate
        notes.append(f"Empirical over-rate {hit_rate*100:.0f}% blended at {blend*100:.0f}%")

    prob = clamp(prob, 0.03, 0.97)
    prob = calibrate(prob, s)

    # Uncertainty in the projection propagates to the probability.
    se_mu = sd / math.sqrt(n_eff + k)
    lo = clamp(1.0 - norm_cdf((line - (mu - 1.2816 * se_mu)) / sd), 0.02, 0.98)
    hi = clamp(1.0 - norm_cdf((line - (mu + 1.2816 * se_mu)) / sd), 0.02, 0.98)
    lo, hi = min(lo, prob), max(hi, prob)
    data_conf = n_eff / (n_eff + k)
    confidence = clamp(0.15 + 0.75 * data_conf, 0.0, 1.0)

    return PropEstimate(
        prob_over=prob, prob_low=lo, prob_high=hi, confidence=confidence,
        projection=mu, sd=sd, sample_mean=raw_mean if n else 0.0, sample_sd=sample_sd,
        n=n, n_eff=n_eff, distribution=dist, hit_rate=hit_rate, push_prob=push, notes=notes,
    )


# =============================================================================
# Parlay correlation (Gaussian copula)
# =============================================================================

@dataclass
class CorrLeg:
    """Minimal view of a leg for correlation purposes."""
    prob: float
    group: str                      # legs in different groups are independent
    tags: tuple[str, ...] = ()      # "side:home" | "side:away" | "fav" | "dog"
                                    # | "total:over" | "total:under"
                                    # | "prop" | "dir:over" | "dir:under" | "team:XYZ"
    sport_key: str = ""


def leg_correlation(a: CorrLeg, b: CorrLeg) -> float:
    if not a.group or a.group != b.group:
        return 0.0
    ta, tb = set(a.tags), set(b.tags)
    sp = sport_params(a.sport_key or b.sport_key)

    side_a = next((t for t in ta if t.startswith("side:")), None)
    side_b = next((t for t in tb if t.startswith("side:")), None)
    tot_a = next((t for t in ta if t.startswith("total:")), None)
    tot_b = next((t for t in tb if t.startswith("total:")), None)

    if side_a and side_b:
        return 0.85 if side_a == side_b else -0.85
    if tot_a and tot_b:
        return -0.99 if tot_a != tot_b else 0.99
    if (side_a and tot_b) or (side_b and tot_a):
        side_leg, tot = (a, tot_b) if side_a else (b, tot_a)
        is_fav = "fav" in side_leg.tags
        is_over = tot.endswith("over")
        c = sp.fav_over_corr
        return c if is_fav == is_over else -c

    if "prop" in ta and "prop" in tb:
        team_a = next((t for t in ta if t.startswith("team:")), "")
        team_b = next((t for t in tb if t.startswith("team:")), "")
        dir_a = next((t for t in ta if t.startswith("dir:")), "")
        dir_b = next((t for t in tb if t.startswith("dir:")), "")
        if team_a and team_a == team_b:
            return 0.15 if dir_a == dir_b else -0.10
        return 0.0
    return 0.0


def _cholesky(m: list[list[float]]) -> list[list[float]] | None:
    n = len(m)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                d = m[i][i] - s
                if d <= 1e-10:
                    return None
                L[i][j] = math.sqrt(d)
            else:
                L[i][j] = (m[i][j] - s) / L[j][j]
    return L


def joint_probability(probs: Sequence[float], corr: list[list[float]], *, n_samples: int = 6000,
                      seed: int = 7) -> float:
    """P(all legs hit) under a Gaussian copula with correlation matrix `corr`."""
    n = len(probs)
    if n == 0:
        return 0.0
    if all(abs(corr[i][j]) < 1e-9 for i in range(n) for j in range(n) if i != j):
        out = 1.0
        for p in probs:
            out *= p
        return out
    # Shrink toward identity until the matrix is positive definite.
    shrink = 1.0
    L = None
    while shrink > 0.05:
        m = [[(corr[i][j] * shrink if i != j else 1.0) for j in range(n)] for i in range(n)]
        L = _cholesky(m)
        if L is not None:
            break
        shrink *= 0.85
    if L is None:
        out = 1.0
        for p in probs:
            out *= p
        return out
    thresholds = [norm_ppf(p) for p in probs]   # leg hits when z_i < threshold_i
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_samples):
        z = [rng.gauss(0.0, 1.0) for _ in range(n)]
        ok = True
        for i in range(n):
            v = sum(L[i][k] * z[k] for k in range(i + 1))
            if v >= thresholds[i]:
                ok = False
                break
        if ok:
            hits += 1
    return hits / n_samples


@dataclass
class ParlayProbability:
    combined: float
    independent: float
    max_abs_corr: float
    pairs: list[tuple[int, int, float]] = field(default_factory=list)
    note: str = ""


def parlay_probability(legs: Sequence[CorrLeg], settings: ModelSettings | None = None) -> ParlayProbability:
    s = settings or _SETTINGS
    n = len(legs)
    indep = 1.0
    for l in legs:
        indep *= l.prob
    if n < 2 or not s.use_correlation:
        return ParlayProbability(indep, indep, 0.0)
    corr = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    pairs: list[tuple[int, int, float]] = []
    for i in range(n):
        for j in range(i + 1, n):
            c = leg_correlation(legs[i], legs[j])
            if abs(c) > 1e-9:
                corr[i][j] = corr[j][i] = c
                pairs.append((i, j, c))
    if not pairs:
        return ParlayProbability(indep, indep, 0.0)
    combined = joint_probability([l.prob for l in legs], corr)
    max_c = max(abs(c) for _, _, c in pairs)
    direction = "boosts" if combined > indep else "cuts"
    note = (f"{len(pairs)} same-game pair{'s' if len(pairs) != 1 else ''} correlated (max |ρ| {max_c:.2f}); "
            f"correlation {direction} hit probability from {indep*100:.1f}% to {combined*100:.1f}%")
    return ParlayProbability(combined, indep, max_c, pairs, note)


# =============================================================================
# Staking
# =============================================================================

def expected_value(prob: float, decimal_price: float) -> float:
    return prob * (decimal_price - 1.0) - (1.0 - prob)


def kelly_fraction(prob: float, decimal_price: float) -> float:
    b = decimal_price - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (b * prob - (1.0 - prob)) / b)
