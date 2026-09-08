"""Tests for the LegAnalysis / ParlayAnalysis layer and the generators."""
import pytest

from src.analysis import probability as P
from src.analysis import props as PR
from src.analysis import research as R
from src.analysis import generator as G
from src.analysis import prop_generator as PG
from src.analysis import markets as MK
from src.api.demo_data import demo_games
from src.api.demo_props import demo_props


def test_build_leg_analysis_fields():
    g = demo_games("americanfootball_nfl")[0]
    leg = P.build_leg_analysis(g, "spreads", g.home_team, [])
    assert leg is not None
    assert leg.selection.startswith(g.home_team)
    assert leg.prob_low <= leg.model_prob <= leg.prob_high
    assert 0 < leg.confidence <= 1
    assert leg.corr_group == g.id and "side:home" in leg.corr_tags
    assert leg.kelly_conservative <= leg.kelly_fraction
    assert leg.notes and "Consensus" in leg.notes[0]


def test_bookmaker_filter_restricts_price():
    g = demo_games("basketball_nba")[0]
    leg = P.build_leg_analysis(g, "h2h", g.away_team, [], bookmaker_filter="FanDuel")
    assert leg.bookmaker == "FanDuel"


def test_factors_move_probability():
    g = demo_games("basketball_ncaab")[0]
    base = P.build_leg_analysis(g, "h2h", g.home_team, [])
    inj = [P.injury_factor(0.8, g.away_team, favors_outcome=True)]
    boosted = P.build_leg_analysis(g, "h2h", g.home_team, inj)
    assert boosted.model_prob > base.model_prob
    assert boosted.adjustments[0].contribution > 0


def test_home_field_is_informational():
    f = P.home_field_factor(True, "basketball_nba")
    assert f.contribution == 0.0


def test_form_factor_uses_baseline():
    hot = P.form_factor({"last5": [{"result": "W"}] * 4 + [{"result": "L"}], "win_pct": 0.4}, "X", True)
    expected = P.form_factor({"last5": [{"result": "W"}] * 4 + [{"result": "L"}], "win_pct": 0.8}, "X", True)
    assert hot.weight > 0 > expected.weight or hot.weight > expected.weight


def test_rest_factor_only_for_fatigue_sports():
    assert P.rest_factor(1, "X", True, "basketball_nba") is not None
    assert P.rest_factor(1, "X", True, "americanfootball_nfl") is None
    assert P.rest_factor(4, "X", True, "basketball_nba") is None


def test_team_pick_to_leg_platforms():
    g = demo_games("americanfootball_nfl")[0]
    pp = P.team_pick_to_leg(g, "spreads", g.home_team, "prizepicks")
    ks = P.team_pick_to_leg(g, "spreads", g.home_team, "kalshi")
    assert pp.bookmaker == "PrizePicks" and pp.price == -110
    assert ks.bookmaker == "Kalshi" and abs(1 / ks.decimal_price - ks.model_prob) < 1e-6


def test_analyze_parlay_correlated_same_game():
    g = demo_games("americanfootball_nfl")[0]
    ml = P.build_leg_analysis(g, "h2h", g.home_team, [])
    sp = P.build_leg_analysis(g, "spreads", g.home_team, [])
    par = P.analyze_parlay([ml, sp])
    assert par.combined_prob > par.independent_prob
    assert par.correlation_note
    assert par.prob_low <= par.combined_prob <= par.prob_high
    g2 = demo_games("americanfootball_nfl")[1]
    other = P.build_leg_analysis(g2, "h2h", g2.home_team, [])
    par2 = P.analyze_parlay([ml, other])
    assert par2.combined_prob == pytest.approx(par2.independent_prob)
    assert par2.correlation_note == ""


def test_analyze_parlay_dfs_mode():
    g = demo_games("basketball_nba")[0]
    legs = [P.team_pick_to_leg(g, "h2h", g.home_team), P.team_pick_to_leg(demo_games("basketball_nba")[1], "totals", "Over")]
    par = P.analyze_parlay(legs)
    assert par.is_dfs and par.dfs_multiplier == 3.0


def test_apply_adjustments_legacy_helper():
    f = [P.AdjustmentFactor("x", 1.0, scale=0.5, confidence=1.0)]
    assert P.apply_adjustments(0.5, f, sport_key="basketball_ncaab") > 0.5
    assert P.apply_adjustments(0.5, []) == 0.5


def test_prop_analysis_offline():
    p = demo_props("basketball_nba")[0]
    a = PR.analyze_prop(p, skip_network=True)
    assert abs(a.over_prob + a.under_prob - 1.0) < 1e-9
    leg = PR.prop_to_leg(p, a, "Under")
    assert leg.market == "prop_under" and "dir:under" in leg.corr_tags
    assert leg.prob_low <= leg.model_prob <= leg.prob_high


def test_generate_slip_offline(monkeypatch):
    monkeypatch.setattr(G, "research_game", lambda g: R.empty_research(g))
    games = demo_games("americanfootball_nfl")
    slips = G.generate_slips(games, mode="balanced", max_legs=3, count=2)
    assert 1 <= len(slips) <= 2
    for s in slips:
        assert 2 <= len(s.legs) <= 3
        assert len({l.game_id for l in s.legs}) == len(s.legs)
        assert s.overall_reasoning
    if len(slips) == 2:
        assert not set(slips[0].leg_keys) & set(slips[1].leg_keys)


def test_generate_prop_slip_offline():
    props = demo_props("basketball_nba")
    slips = PG.generate_prop_slips(props, mode="safest", max_legs=3, min_legs=3, count=2, skip_network=True)
    assert slips and len(slips[0].legs) == 3
    assert slips[0].power_multiplier == 5.0


def test_markets_scanners_run_on_demo():
    games = demo_games("basketball_nba")
    shops = MK.all_line_shops(games)
    assert shops and all(0 < s.fair_prob < 1 for s in shops)
    MK.positive_ev_picks(games)
    MK.arb_opportunities(games)
    assert MK.biggest_line_spreads(games, limit=5)
