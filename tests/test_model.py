"""Unit tests for the probability engine (src/analysis/model.py)."""
import math

import pytest

from src.analysis import model as M
from src.api.odds_api import Game, Bookmaker, Market, Outcome
from src.api.demo_data import demo_games


# ---------------------------------------------------------------- helpers

def _game(home_ml=-150, away_ml=+130, spread=-3.0, total=45.5, books=3, sport="americanfootball_nfl",
          spread_prices=(-110, -110), points=None):
    bks = []
    for i in range(books):
        pt = spread if points is None else points[i]
        bks.append(Bookmaker(f"book{i}", f"Book {i}", "", [
            Market("h2h", [Outcome("Home", home_ml + 3 * i), Outcome("Away", away_ml - 3 * i)]),
            Market("spreads", [Outcome("Home", spread_prices[0], pt), Outcome("Away", spread_prices[1], -pt)]),
            Market("totals", [Outcome("Over", -110, total), Outcome("Under", -110, total)]),
        ]))
    return Game("g1", sport, "NFL", "2030-01-01T00:00:00Z", "Home", "Away", bks)


# ---------------------------------------------------------------- math

def test_norm_ppf_inverts_cdf():
    for p in (0.001, 0.05, 0.3, 0.5, 0.77, 0.95, 0.999):
        assert abs(M.norm_cdf(M.norm_ppf(p)) - p) < 1e-9


def test_logit_expit_roundtrip():
    for p in (0.01, 0.25, 0.5, 0.9):
        assert abs(M.expit(M.logit(p)) - p) < 1e-12


# ---------------------------------------------------------------- devig

def test_devig_multiplicative_sums_to_one():
    out = M.devig([0.55, 0.50], "multiplicative")
    assert abs(sum(out) - 1.0) < 1e-12
    assert out[0] > out[1]


def test_devig_power_reduces_longshot_bias():
    # -400 / +300 market: implied 0.80 / 0.25 (5% hold). Power de-vig should
    # give the favorite MORE than the multiplicative method does.
    imps = [0.80, 0.25]
    mult = M.devig(imps, "multiplicative")
    powr = M.devig(imps, "power")
    assert abs(sum(powr) - 1.0) < 1e-9
    assert powr[0] > mult[0]


def test_devig_three_way():
    out = M.devig([0.45, 0.30, 0.30], "power")
    assert abs(sum(out) - 1.0) < 1e-9
    assert len(out) == 3


# ---------------------------------------------------------------- consensus

def test_consensus_uses_all_books_and_devigs():
    g = _game(books=4)
    c = M.consensus(g, "h2h", "Home")
    assert c is not None
    assert c.n_books == 4
    assert 0.55 < c.fair_prob < 0.62          # -150 ≈ 60% implied, ~57% fair
    assert c.logit_sd > 0                     # books disagree slightly
    assert abs(sum(v for v in c.book_fair.values()) / 4 - c.fair_prob) < 0.02


def test_consensus_point_is_weighted_median_and_filters_books():
    g = _game(books=3, points=[-3.0, -3.0, -2.5])
    c = M.consensus(g, "spreads", "Home")
    assert c.point == -3.0
    assert c.n_books == 2                     # the -2.5 book doesn't vote on the -3 price
    assert c.points_seen == {-3.0: 2, -2.5: 1}


def test_consensus_missing_market_returns_none():
    g = _game()
    assert M.consensus(g, "h2h", "Nobody") is None


# ---------------------------------------------------------------- margin model

def test_cover_probability_push_mass_on_whole_numbers():
    p_win, p_push = M.cover_probability(mu=3.0, sd=13.5, spread=-3.0, integer_scores=True)
    assert 0.02 < p_push < 0.05
    assert abs(p_win - (1 - p_push) / 2) < 0.01     # symmetric around the mean
    p_win2, p_push2 = M.cover_probability(mu=3.0, sd=13.5, spread=-2.5, integer_scores=True)
    assert p_push2 == 0.0
    assert p_win2 > p_win


def test_margin_roundtrip():
    mu = 4.2
    p, _ = M.cover_probability(mu, 12.0, -6.5, integer_scores=False)
    assert abs(M.margin_from_cover(p, 12.0, -6.5) - mu) < 1e-6


def test_point_shift_monotone():
    sp = M.sport_params("americanfootball_nfl")
    p0 = 0.50
    easier, _ = M.point_shift(p0, "spreads", "Home", -3.0, -1.5, sp)
    harder, _ = M.point_shift(p0, "spreads", "Home", -3.0, -6.5, sp)
    assert easier > p0 > harder
    over_up, _ = M.point_shift(0.5, "totals", "Over", 45.5, 42.5, sp)
    under_up, _ = M.point_shift(0.5, "totals", "Under", 45.5, 48.5, sp)
    assert over_up > 0.5 and under_up > 0.5


def test_cross_market_agrees_when_lines_are_consistent():
    # -3 spread with NFL sd 13.5 implies ML ≈ Φ(3/13.5) ≈ 58.8%; use -142 / +122.
    g = _game(home_ml=-142, away_ml=+122, spread=-3.0, books=1)
    est = M.cross_market_estimate(g, "spreads", "Home", -3.0)
    direct = M.consensus(g, "spreads", "Home").fair_prob
    assert est is not None and est.source == "moneyline → spread"
    assert abs(est.prob - direct) < 0.04
    ml_from_spread = M.cross_market_estimate(g, "h2h", "Home", None)
    assert abs(ml_from_spread.prob - M.consensus(g, "h2h", "Home").fair_prob) < 0.05


def test_cross_market_detects_stale_spread():
    # Moneyline says a big favorite (-400) but the spread is only -3: the
    # spread side should be pulled UP by the cross-market estimate.
    g = _game(home_ml=-400, away_ml=+320, spread=-3.0, books=1)
    est = M.estimate_leg(g, "spreads", "Home")
    assert est.cross_prob is not None
    assert est.cross_prob > est.market_prob
    assert est.market_prob < est.prior_prob < est.cross_prob


def test_cross_market_disabled_by_settings():
    g = _game(books=1)
    est = M.estimate_leg(g, "spreads", "Home", settings=M.ModelSettings(use_cross_market=False))
    assert est.cross_prob is None
    assert abs(est.prior_prob - est.market_prob) < 1e-9


# ---------------------------------------------------------------- evidence

def test_evidence_shift_is_discounted_and_capped():
    sp = M.sport_params("americanfootball_nfl")      # efficiency 0.80 → discount 0.2
    ev = [M.Evidence("x", 1.0, 0.5, 1.0)]
    f = M.fuse_evidence(0.0, 0.1, ev, sp, M.ModelSettings())
    assert abs(f.raw_shift - 0.5) < 1e-9
    assert abs(f.applied_shift - M.EVIDENCE_CAP * math.tanh(0.1 / M.EVIDENCE_CAP)) < 1e-9
    huge = [M.Evidence("h", 1.0, 5.0, 1.0)] * 4
    f2 = M.fuse_evidence(0.0, 0.1, huge, sp, M.ModelSettings(evidence_discount=1.0))
    assert f2.applied_shift <= M.EVIDENCE_CAP
    assert f2.sd > f.sd                              # bigger move → more uncertainty


def test_low_confidence_evidence_adds_uncertainty():
    sp = M.sport_params("basketball_ncaab")
    sure = M.fuse_evidence(0.0, 0.1, [M.Evidence("a", 1.0, 0.4, 1.0)], sp)
    shaky = M.fuse_evidence(0.0, 0.1, [M.Evidence("a", 1.0, 0.4, 0.3)], sp)
    assert abs(shaky.applied_shift) < abs(sure.applied_shift)


def test_estimate_leg_interval_and_confidence():
    g = _game(books=4)
    est = M.estimate_leg(g, "h2h", "Home")
    assert est.prob_low < est.prob < est.prob_high
    assert 0.0 < est.confidence <= 1.0
    one = M.estimate_leg(_game(books=1), "h2h", "Home")
    assert one.confidence <= est.confidence + 1e-9   # fewer books → not more confident


def test_estimate_leg_applies_evidence_direction():
    g = _game(books=2)
    base = M.estimate_leg(g, "h2h", "Home").prob
    up = M.estimate_leg(g, "h2h", "Home", [M.Evidence("inj", 1.0, 0.5, 0.8)]).prob
    down = M.estimate_leg(g, "h2h", "Home", [M.Evidence("inj", -1.0, 0.5, 0.8)]).prob
    assert down < base < up


def test_calibration_temperature_shrinks_toward_half():
    s = M.ModelSettings(calibration_temperature=2.0)
    assert 0.5 < M.calibrate(0.8, s) < 0.8
    assert 0.2 < M.calibrate(0.2, s) < 0.5
    assert M.calibrate(0.8, M.ModelSettings()) == 0.8


def test_settings_from_config_defaults_and_parsing():
    s = M.settings_from_config({})
    assert s.use_cross_market and s.use_correlation and s.kelly_conservative
    s2 = M.settings_from_config({"model": {"use_correlation": False, "calibration_temperature": "bad",
                                           "evidence_discount": 0.5}})
    assert not s2.use_correlation and s2.calibration_temperature == 1.0 and s2.evidence_discount == 0.5


# ---------------------------------------------------------------- props

def test_prop_no_samples_is_near_coinflip():
    est = M.estimate_prop("basketball_nba", "Points", 25.5, [])
    assert abs(est.prob_over - 0.5) < 0.03
    assert est.distribution == "normal"
    assert est.confidence < 0.3


def test_prop_shrinks_toward_line():
    samples = [35, 33, 34, 36, 32, 35, 34, 33]      # avg ≈ 34 vs line 25.5
    est = M.estimate_prop("basketball_nba", "Points", 25.5, samples)
    assert 25.5 < est.projection < 34.0
    assert est.prob_over > 0.75
    assert est.prob_low < est.prob_over < est.prob_high
    assert est.confidence > 0.5


def test_prop_recency_weighting_prefers_recent_games():
    hot_recent = [30, 30, 30, 20, 20, 20]
    cold_recent = [20, 20, 20, 30, 30, 30]
    a = M.estimate_prop("basketball_nba", "Points", 24.5, hot_recent)
    b = M.estimate_prop("basketball_nba", "Points", 24.5, cold_recent)
    assert a.projection > b.projection
    assert a.prob_over > b.prob_over


def test_prop_low_count_uses_poisson():
    est = M.estimate_prop("baseball_mlb", "Home Runs", 0.5, [0, 1, 0, 0, 1, 0, 0, 0])
    assert est.distribution in ("poisson", "negbin")
    assert 0.1 < est.prob_over < 0.5
    # A batter who homers every game must have high over probability.
    hot = M.estimate_prop("baseball_mlb", "Home Runs", 0.5, [1, 2, 1, 1, 1, 1, 2, 1, 1, 1])
    assert hot.prob_over > est.prob_over


def test_prop_whole_number_line_has_push_mass():
    est = M.estimate_prop("icehockey_nhl", "Shots", 3.0, [3, 4, 2, 3, 5, 3])
    assert est.push_prob > 0.05


def test_poisson_sf_matches_closed_form():
    mu = 1.3
    assert abs(M._poisson_sf(1, mu) - (1 - math.exp(-mu))) < 1e-12
    assert M._negbin_sf(1, mu, mu * 0.9) == M._poisson_sf(1, mu)   # underdispersed → Poisson


# ---------------------------------------------------------------- parlay correlation

def test_same_side_correlation_boosts_joint_probability():
    ml = M.CorrLeg(0.60, "g1", ("side:home", "fav"), "americanfootball_nfl")
    sp = M.CorrLeg(0.50, "g1", ("side:home", "fav"), "americanfootball_nfl")
    res = M.parlay_probability([ml, sp])
    assert res.independent == pytest.approx(0.30)
    assert res.combined > 0.36
    assert res.max_abs_corr == 0.85
    assert "correlated" in res.note


def test_opposite_sides_cut_joint_probability():
    a = M.CorrLeg(0.60, "g1", ("side:home", "fav"))
    b = M.CorrLeg(0.50, "g1", ("side:away", "dog"))
    res = M.parlay_probability([a, b])
    assert res.combined < res.independent


def test_over_and_under_same_game_nearly_impossible():
    a = M.CorrLeg(0.5, "g1", ("total:over",))
    b = M.CorrLeg(0.5, "g1", ("total:under",))
    assert M.parlay_probability([a, b]).combined < 0.05


def test_independent_when_groups_differ_or_disabled():
    a = M.CorrLeg(0.6, "g1", ("side:home", "fav"))
    b = M.CorrLeg(0.6, "g2", ("side:home", "fav"))
    res = M.parlay_probability([a, b])
    assert res.combined == pytest.approx(0.36)
    res2 = M.parlay_probability([a, M.CorrLeg(0.6, "g1", ("side:home", "fav"))],
                                M.ModelSettings(use_correlation=False))
    assert res2.combined == pytest.approx(0.36)


def test_joint_probability_is_deterministic():
    corr = [[1, 0.5], [0.5, 1]]
    assert M.joint_probability([0.5, 0.5], corr) == M.joint_probability([0.5, 0.5], corr)


def test_cholesky_shrinks_non_psd_matrix():
    # Inconsistent triangle: A~B strongly +, B~C strongly +, A~C strongly -
    corr = [[1, 0.9, -0.9], [0.9, 1, 0.9], [-0.9, 0.9, 1]]
    p = M.joint_probability([0.5, 0.5, 0.5], corr)
    assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------- demo slate end-to-end

@pytest.mark.parametrize("sport", ["americanfootball_nfl", "basketball_nba", "baseball_mlb", "icehockey_nhl", "soccer_epl"])
def test_demo_slate_every_market_estimates(sport):
    for g in demo_games(sport):
        for mk, sel in (("h2h", g.home_team), ("h2h", g.away_team), ("spreads", g.home_team),
                        ("spreads", g.away_team), ("totals", "Over"), ("totals", "Under")):
            est = M.estimate_leg(g, mk, sel)
            assert est is not None, (g.id, mk, sel)
            assert 0.01 <= est.prob <= 0.99
            assert est.prob_low <= est.prob <= est.prob_high
        # Two sides of a two-way market should be complementary at the consensus point.
        h = M.consensus(g, "h2h", g.home_team).fair_prob
        a = M.consensus(g, "h2h", g.away_team).fair_prob
        assert abs(h + a - 1.0) < 0.02
