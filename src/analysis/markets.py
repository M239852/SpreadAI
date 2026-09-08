"""Cross-book market analytics.

This is the math behind the Markets view (per-game line shop) and the Odds
Analyzer view (+EV / arbitrage / best-line scanner across the whole slate).

Everything here is pure over the `Game` dataclass — no UI, no threading —
so the views can call it from a worker thread and marshal the results onto
the main loop.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable

from ..api.odds_api import Game, Bookmaker, Outcome
from ..utils.formatters import american_to_implied, american_to_decimal


# --- Line shop (all books side-by-side for one selection) ---------------

@dataclass
class BookQuote:
    bookmaker_key: str
    bookmaker_title: str
    price: int                   # American
    point: float | None = None
    implied: float = 0.0          # With vig
    decimal: float = 0.0

    @property
    def american_str(self) -> str:
        return f"+{self.price}" if self.price > 0 else str(self.price)


@dataclass
class LineShop:
    game_id: str
    matchup: str
    market: str                  # h2h | spreads | totals
    selection: str               # team name, "Over", "Under"
    quotes: list[BookQuote] = field(default_factory=list)
    fair_prob: float = 0.0       # De-vigged consensus fair probability
    best_price: int = 0
    best_book: str = ""
    best_edge: float = 0.0       # fair_prob - best_book_implied (positive = +EV)

    @property
    def price_spread(self) -> int:
        """Difference between best and worst American price in the market."""
        if len(self.quotes) < 2:
            return 0
        prices = [q.price for q in self.quotes]
        return max(prices) - min(prices)


def line_shop_for(game: Game, market_key: str, selection: str) -> LineShop:
    """Collect every book's quote for (market_key, selection) on this game."""
    quotes: list[BookQuote] = []
    for bk in game.bookmakers:
        mk = bk.market(market_key)
        if not mk:
            continue
        for o in mk.outcomes:
            if o.name.lower() != selection.lower():
                continue
            quotes.append(BookQuote(
                bookmaker_key=bk.key,
                bookmaker_title=bk.title,
                price=o.price,
                point=o.point,
                implied=american_to_implied(o.price),
                decimal=american_to_decimal(o.price),
            ))
    quotes.sort(key=lambda q: q.price, reverse=True)   # best price first

    fair = _fair_prob(game, market_key, selection)
    best_price = quotes[0].price if quotes else 0
    best_book = quotes[0].bookmaker_title if quotes else ""
    best_imp = quotes[0].implied if quotes else 0.0
    edge = fair - best_imp if quotes else 0.0

    return LineShop(
        game_id=game.id,
        matchup=f"{game.away_team} @ {game.home_team}",
        market=market_key,
        selection=selection,
        quotes=quotes,
        fair_prob=fair,
        best_price=best_price,
        best_book=best_book,
        best_edge=edge,
    )


def _fair_prob(game: Game, market_key: str, selection: str) -> float:
    """Sharpness-weighted, per-book de-vigged consensus (see `model.consensus`)."""
    from . import model as M
    cons = M.consensus(game, market_key, selection)
    return cons.fair_prob if cons else 0.0


def _opposite(game: Game, market_key: str, selection: str) -> str | None:
    if market_key == "h2h" or market_key == "spreads":
        if selection == game.home_team:
            return game.away_team
        if selection == game.away_team:
            return game.home_team
        return None
    if market_key == "totals":
        return "Under" if selection.lower().startswith("over") else "Over"
    return None


# --- Standard (Moneyline / Spread / Total) selections per game ----------

def selections_for_game(game: Game) -> list[tuple[str, str]]:
    """The selections we care about for each game's three big markets."""
    return [
        ("h2h",     game.home_team),
        ("h2h",     game.away_team),
        ("spreads", game.home_team),
        ("spreads", game.away_team),
        ("totals",  "Over"),
        ("totals",  "Under"),
    ]


def all_line_shops(games: Iterable[Game]) -> list[LineShop]:
    out: list[LineShop] = []
    for g in games:
        for mk, sel in selections_for_game(g):
            ls = line_shop_for(g, mk, sel)
            if ls.quotes:
                out.append(ls)
    return out


# --- Positive-EV scanner ------------------------------------------------

@dataclass
class EdgePick:
    shop: LineShop
    edge: float           # fair - best_implied
    ev_per_dollar: float  # Kelly-ish EV at the best price

    @property
    def summary(self) -> str:
        return (f"{self.shop.matchup} · {_market_label(self.shop.market)} · "
                f"{_format_selection(self.shop.market, self.shop.selection, _best_point(self.shop))} "
                f"@ {self.shop.best_book} {_fmt_am(self.shop.best_price)}")


def _best_point(shop: LineShop) -> float | None:
    return shop.quotes[0].point if shop.quotes else None


def positive_ev_picks(
    games: Iterable[Game],
    min_edge: float = 0.02,
) -> list[EdgePick]:
    """Every selection whose best book price beats the consensus fair price."""
    picks: list[EdgePick] = []
    for shop in all_line_shops(games):
        if shop.best_edge < min_edge or shop.best_price == 0:
            continue
        dec = shop.quotes[0].decimal
        ev = shop.fair_prob * (dec - 1.0) - (1.0 - shop.fair_prob)
        picks.append(EdgePick(shop=shop, edge=shop.best_edge, ev_per_dollar=ev))
    picks.sort(key=lambda p: p.edge, reverse=True)
    return picks


# --- Arbitrage scanner --------------------------------------------------

@dataclass
class ArbOpportunity:
    game_id: str
    matchup: str
    market: str
    selection_a: str
    selection_b: str
    book_a_title: str
    book_b_title: str
    price_a: int
    price_b: int
    point_a: float | None
    point_b: float | None
    implied_sum: float          # 1/dec_a + 1/dec_b; <1 means arb
    profit_pct: float           # Guaranteed return on total stake
    stake_a_pct: float          # Fraction of bankroll on side A
    stake_b_pct: float          # Fraction of bankroll on side B

    @property
    def summary(self) -> str:
        return (f"{self.matchup} · {_market_label(self.market)} · "
                f"{_format_selection(self.market, self.selection_a, self.point_a)} "
                f"@ {self.book_a_title} {_fmt_am(self.price_a)} / "
                f"{_format_selection(self.market, self.selection_b, self.point_b)} "
                f"@ {self.book_b_title} {_fmt_am(self.price_b)} · "
                f"+{self.profit_pct*100:.2f}%")


def _two_way_pairs(game: Game) -> list[tuple[str, str, str]]:
    """Return (market, sel_a, sel_b) pairs to check for arbitrage."""
    return [
        ("h2h",     game.home_team, game.away_team),
        ("spreads", game.home_team, game.away_team),
        ("totals",  "Over",         "Under"),
    ]


def arb_opportunities(
    games: Iterable[Game],
    min_profit: float = 0.001,   # 0.1% — anything above the exchange fee
) -> list[ArbOpportunity]:
    """Find two-way markets where the best price on each side sums to <100%
    implied probability — lock in a risk-free profit across two books.

    For spreads/totals we require the POINT on each side to match so the
    legs are actually opposite sides of the same number. This excludes
    middling, which lives in a different scanner.
    """
    out: list[ArbOpportunity] = []
    for g in games:
        for mk, sa, sb in _two_way_pairs(g):
            shop_a = line_shop_for(g, mk, sa)
            shop_b = line_shop_for(g, mk, sb)
            if not shop_a.quotes or not shop_b.quotes:
                continue

            # For spreads/totals, iterate quote-pairs to find matching points.
            if mk in ("spreads", "totals"):
                for qa in shop_a.quotes:
                    for qb in shop_b.quotes:
                        if _points_pair(mk, qa.point, qb.point):
                            opp = _check_pair(
                                g, mk, sa, sb, qa, qb, min_profit,
                            )
                            if opp:
                                out.append(opp)
                                break   # one arb per sa/qa is enough
            else:
                qa = shop_a.quotes[0]
                qb = shop_b.quotes[0]
                opp = _check_pair(g, mk, sa, sb, qa, qb, min_profit)
                if opp:
                    out.append(opp)

    out.sort(key=lambda o: o.profit_pct, reverse=True)
    return out


def _points_pair(market: str, pa: float | None, pb: float | None) -> bool:
    if market == "spreads":
        # Opposite-sign points on the same line, e.g. -3.5 and +3.5
        if pa is None or pb is None:
            return False
        return abs(pa + pb) < 0.01
    if market == "totals":
        # Same total value
        if pa is None or pb is None:
            return False
        return abs(pa - pb) < 0.01
    return True


def _check_pair(
    game: Game, market: str, sa: str, sb: str,
    qa: BookQuote, qb: BookQuote, min_profit: float,
) -> ArbOpportunity | None:
    if qa.bookmaker_title == qb.bookmaker_title:
        return None   # arb requires two different books
    inv_a = 1.0 / qa.decimal
    inv_b = 1.0 / qb.decimal
    implied_sum = inv_a + inv_b
    if implied_sum >= 1.0:
        return None
    profit = (1.0 - implied_sum)
    if profit < min_profit:
        return None
    # Stake allocation for equalized profit across both outcomes.
    stake_a = inv_a / implied_sum
    stake_b = inv_b / implied_sum
    return ArbOpportunity(
        game_id=game.id,
        matchup=f"{game.away_team} @ {game.home_team}",
        market=market,
        selection_a=sa, selection_b=sb,
        book_a_title=qa.bookmaker_title, book_b_title=qb.bookmaker_title,
        price_a=qa.price, price_b=qb.price,
        point_a=qa.point, point_b=qb.point,
        implied_sum=implied_sum,
        profit_pct=profit,
        stake_a_pct=stake_a, stake_b_pct=stake_b,
    )


# --- Best-line shopping (biggest spread between books) ------------------

@dataclass
class BestLineRow:
    shop: LineShop
    price_spread: int   # diff between highest and lowest book price
    worst_book: str
    worst_price: int


def biggest_line_spreads(games: Iterable[Game], limit: int = 30) -> list[BestLineRow]:
    """Selections where the price differs most across books — line-shopping wins."""
    rows: list[BestLineRow] = []
    for shop in all_line_shops(games):
        if len(shop.quotes) < 2:
            continue
        worst = shop.quotes[-1]
        rows.append(BestLineRow(
            shop=shop,
            price_spread=shop.price_spread,
            worst_book=worst.bookmaker_title,
            worst_price=worst.price,
        ))
    rows.sort(key=lambda r: r.price_spread, reverse=True)
    return rows[:limit]


# --- formatting helpers used by summaries ------------------------------

def _market_label(market: str) -> str:
    return {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}.get(market, market)


def _format_selection(market: str, selection: str, point: float | None) -> str:
    if market == "spreads" and point is not None:
        return f"{selection} {point:+g}"
    if market == "totals" and point is not None:
        return f"{selection} {point}"
    return selection


def _fmt_am(price: int) -> str:
    return f"+{price}" if price > 0 else str(price)
