"""Offline demo slate used when no Odds API key is configured.

Each game is generated with 4–5 sportsbooks carrying slightly varied prices
so the cross-book comparison + best-price picker exercises itself meaningfully.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from .odds_api import Game, Bookmaker, Market, Outcome


_BOOKS: list[tuple[str, str]] = [
    ("draftkings", "DraftKings"),
    ("fanduel", "FanDuel"),
    ("betmgm", "BetMGM"),
    ("caesars", "Caesars"),
    ("pointsbetus", "PointsBet"),
]

# Signed offsets applied to the base prices at each book so the cross-book
# comparison finds different "best" prices per line.
_BOOK_VARIATIONS: list[tuple[int, int, int, int]] = [
    (0, 0, 0, 0),      # DK  — base
    (-5, +3, +2, -2),  # FD
    (+4, -4, -3, +3),  # MGM
    (-3, +2, +3, -3),  # Caesars
    (+5, -5, -2, +2),  # PointsBet
]


def _make_game(
    gid: str,
    sport_key: str,
    sport_title: str,
    home: str,
    away: str,
    hours_ahead: int,
    *,
    home_ml: int,
    away_ml: int,
    spread: float,           # home spread (negative if home is favored)
    total: float,
    over_price: int = -110,
    under_price: int = -110,
    num_books: int = 4,
) -> Game:
    ct = (datetime.now(timezone.utc) + timedelta(hours=hours_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ")
    bookmakers: list[Bookmaker] = []
    for (k, title), v in list(zip(_BOOKS, _BOOK_VARIATIONS))[:num_books]:
        h_ml, a_ml, sp_off, tot_off = v
        markets = [
            Market("h2h", [
                Outcome(home, home_ml + h_ml),
                Outcome(away, away_ml + a_ml),
            ]),
            Market("spreads", [
                Outcome(home, -110 + sp_off, spread),
                Outcome(away, -110 - sp_off, -spread),
            ]),
            Market("totals", [
                Outcome("Over", over_price + tot_off, total),
                Outcome("Under", under_price - tot_off, total),
            ]),
        ]
        bookmakers.append(Bookmaker(k, title, ct, markets))
    return Game(gid, sport_key, sport_title, ct, home, away, bookmakers)


def _nfl() -> list[Game]:
    sk, st = "americanfootball_nfl", "NFL"
    return [
        _make_game("demo-nfl-1",  sk, st, "Kansas City Chiefs",     "Buffalo Bills",         28,  home_ml=-145, away_ml=+125, spread=-2.5, total=48.5),
        _make_game("demo-nfl-2",  sk, st, "Philadelphia Eagles",    "Dallas Cowboys",        52,  home_ml=-175, away_ml=+150, spread=-3.5, total=45.5),
        _make_game("demo-nfl-3",  sk, st, "San Francisco 49ers",    "Los Angeles Rams",      5,   home_ml=-220, away_ml=+185, spread=-5.0, total=47.0),
        _make_game("demo-nfl-4",  sk, st, "Baltimore Ravens",       "Cincinnati Bengals",    30,  home_ml=-160, away_ml=+135, spread=-3.0, total=46.5),
        _make_game("demo-nfl-5",  sk, st, "Detroit Lions",          "Green Bay Packers",     54,  home_ml=-135, away_ml=+115, spread=-2.0, total=49.5),
        _make_game("demo-nfl-6",  sk, st, "Miami Dolphins",         "New York Jets",         76,  home_ml=-190, away_ml=+160, spread=-4.0, total=43.5),
        _make_game("demo-nfl-7",  sk, st, "Jacksonville Jaguars",   "Houston Texans",        100, home_ml=-118, away_ml=-102, spread=-1.0, total=44.5),
        _make_game("demo-nfl-8",  sk, st, "Minnesota Vikings",      "Chicago Bears",         31,  home_ml=-155, away_ml=+132, spread=-2.5, total=42.5),
        _make_game("demo-nfl-9",  sk, st, "Seattle Seahawks",       "Arizona Cardinals",     79,  home_ml=-210, away_ml=+175, spread=-4.5, total=44.0),
        _make_game("demo-nfl-10", sk, st, "Tampa Bay Buccaneers",   "New Orleans Saints",    103, home_ml=-128, away_ml=+108, spread=-1.5, total=41.5),
    ]


def _ncaaf() -> list[Game]:
    sk, st = "americanfootball_ncaaf", "NCAAF"
    return [
        _make_game("demo-ncaaf-1", sk, st, "Georgia Bulldogs",        "Alabama Crimson Tide",  26, home_ml=-165, away_ml=+140, spread=-3.5, total=52.5),
        _make_game("demo-ncaaf-2", sk, st, "Ohio State Buckeyes",     "Michigan Wolverines",   50, home_ml=-135, away_ml=+115, spread=-2.5, total=48.5),
        _make_game("demo-ncaaf-3", sk, st, "Texas Longhorns",         "Oklahoma Sooners",      72, home_ml=-210, away_ml=+175, spread=-5.5, total=58.0),
        _make_game("demo-ncaaf-4", sk, st, "Penn State Nittany Lions","Wisconsin Badgers",     28, home_ml=-280, away_ml=+220, spread=-7.0, total=46.5),
        _make_game("demo-ncaaf-5", sk, st, "Oregon Ducks",            "USC Trojans",           46, home_ml=-150, away_ml=+128, spread=-3.0, total=64.5),
        _make_game("demo-ncaaf-6", sk, st, "LSU Tigers",              "Ole Miss Rebels",       70, home_ml=-185, away_ml=+155, spread=-4.5, total=60.5),
        _make_game("demo-ncaaf-7", sk, st, "Notre Dame Fighting Irish","Clemson Tigers",       96, home_ml=+110, away_ml=-130, spread=+2.0, total=47.5),
        _make_game("demo-ncaaf-8", sk, st, "Florida State Seminoles", "Miami Hurricanes",      120,home_ml=-175, away_ml=+148, spread=-4.0, total=51.5),
    ]


def _nba() -> list[Game]:
    sk, st = "basketball_nba", "NBA"
    return [
        _make_game("demo-nba-1",  sk, st, "Boston Celtics",        "Milwaukee Bucks",       6,  home_ml=-210, away_ml=+175, spread=-5.5, total=224.5),
        _make_game("demo-nba-2",  sk, st, "Denver Nuggets",        "Phoenix Suns",          30, home_ml=-135, away_ml=+115, spread=-2.0, total=228.5),
        _make_game("demo-nba-3",  sk, st, "Los Angeles Lakers",    "Golden State Warriors", 4,  home_ml=-140, away_ml=+120, spread=-2.5, total=232.5),
        _make_game("demo-nba-4",  sk, st, "Oklahoma City Thunder", "Minnesota Timberwolves",28, home_ml=-165, away_ml=+140, spread=-3.5, total=223.5),
        _make_game("demo-nba-5",  sk, st, "New York Knicks",       "Philadelphia 76ers",    5,  home_ml=-150, away_ml=+128, spread=-3.0, total=215.5),
        _make_game("demo-nba-6",  sk, st, "Cleveland Cavaliers",   "Indiana Pacers",        26, home_ml=-180, away_ml=+152, spread=-4.0, total=230.5),
        _make_game("demo-nba-7",  sk, st, "Miami Heat",            "Orlando Magic",         2,  home_ml=-125, away_ml=+105, spread=-1.5, total=216.5),
        _make_game("demo-nba-8",  sk, st, "Dallas Mavericks",      "Houston Rockets",       29, home_ml=-260, away_ml=+210, spread=-6.5, total=226.5),
        _make_game("demo-nba-9",  sk, st, "Sacramento Kings",      "Memphis Grizzlies",     52, home_ml=-145, away_ml=+125, spread=-2.5, total=233.5),
        _make_game("demo-nba-10", sk, st, "Atlanta Hawks",         "Charlotte Hornets",     3,  home_ml=-200, away_ml=+168, spread=-5.0, total=234.5),
    ]


def _ncaab() -> list[Game]:
    sk, st = "basketball_ncaab", "NCAAB"
    return [
        _make_game("demo-ncaab-1", sk, st, "Duke Blue Devils",       "North Carolina Tar Heels", 4,  home_ml=-155, away_ml=+130, spread=-3.5, total=152.5),
        _make_game("demo-ncaab-2", sk, st, "Kansas Jayhawks",        "Kentucky Wildcats",        28, home_ml=-135, away_ml=+115, spread=-2.5, total=148.5),
        _make_game("demo-ncaab-3", sk, st, "UCLA Bruins",            "Arizona Wildcats",         30, home_ml=+105, away_ml=-125, spread=+1.5, total=144.5),
        _make_game("demo-ncaab-4", sk, st, "Gonzaga Bulldogs",       "Saint Mary's Gaels",       5,  home_ml=-260, away_ml=+210, spread=-7.0, total=148.0),
        _make_game("demo-ncaab-5", sk, st, "Baylor Bears",           "Texas Tech Red Raiders",   54, home_ml=-170, away_ml=+145, spread=-4.0, total=139.5),
        _make_game("demo-ncaab-6", sk, st, "Tennessee Volunteers",   "Florida Gators",           29, home_ml=-200, away_ml=+168, spread=-5.5, total=137.5),
    ]


def _mlb() -> list[Game]:
    sk, st = "baseball_mlb", "MLB"
    return [
        _make_game("demo-mlb-1",  sk, st, "Los Angeles Dodgers",   "San Diego Padres",        8,  home_ml=-160, away_ml=+140, spread=-1.5, total=8.5),
        _make_game("demo-mlb-2",  sk, st, "New York Yankees",      "Boston Red Sox",          4,  home_ml=-135, away_ml=+115, spread=-1.5, total=9.0),
        _make_game("demo-mlb-3",  sk, st, "Houston Astros",        "Texas Rangers",           26, home_ml=-145, away_ml=+125, spread=-1.5, total=8.5),
        _make_game("demo-mlb-4",  sk, st, "Atlanta Braves",        "Philadelphia Phillies",   7,  home_ml=-130, away_ml=+110, spread=-1.5, total=8.5),
        _make_game("demo-mlb-5",  sk, st, "Chicago Cubs",          "St. Louis Cardinals",     28, home_ml=-115, away_ml=-105, spread=-1.5, total=8.0),
        _make_game("demo-mlb-6",  sk, st, "San Francisco Giants",  "Arizona Diamondbacks",    30, home_ml=+105, away_ml=-125, spread=+1.5, total=8.5),
        _make_game("demo-mlb-7",  sk, st, "Toronto Blue Jays",     "Baltimore Orioles",       5,  home_ml=-125, away_ml=+105, spread=-1.5, total=9.5),
        _make_game("demo-mlb-8",  sk, st, "Seattle Mariners",      "Minnesota Twins",         52, home_ml=-140, away_ml=+120, spread=-1.5, total=7.5),
    ]


def _nhl() -> list[Game]:
    sk, st = "icehockey_nhl", "NHL"
    return [
        _make_game("demo-nhl-1", sk, st, "Colorado Avalanche",      "Vegas Golden Knights",  4,  home_ml=-125, away_ml=+105, spread=-1.5, total=6.5),
        _make_game("demo-nhl-2", sk, st, "Boston Bruins",           "Toronto Maple Leafs",   6,  home_ml=-135, away_ml=+115, spread=-1.5, total=6.0),
        _make_game("demo-nhl-3", sk, st, "Edmonton Oilers",         "Calgary Flames",        28, home_ml=-155, away_ml=+132, spread=-1.5, total=6.5),
        _make_game("demo-nhl-4", sk, st, "New York Rangers",        "Pittsburgh Penguins",   5,  home_ml=-145, away_ml=+125, spread=-1.5, total=5.5),
        _make_game("demo-nhl-5", sk, st, "Florida Panthers",        "Tampa Bay Lightning",   30, home_ml=+100, away_ml=-120, spread=+1.5, total=6.5),
        _make_game("demo-nhl-6", sk, st, "Dallas Stars",            "Minnesota Wild",        54, home_ml=-140, away_ml=+120, spread=-1.5, total=5.5),
    ]


def _epl() -> list[Game]:
    sk, st = "soccer_epl", "EPL"
    # Soccer demo uses the three-way market we don't fully model; keep to simple
    # spreads (goal lines) and totals so the existing UI works.
    return [
        _make_game("demo-epl-1", sk, st, "Manchester City",  "Liverpool",        30, home_ml=-115, away_ml=+250, spread=-0.5, total=2.5),
        _make_game("demo-epl-2", sk, st, "Arsenal",          "Tottenham Hotspur",52, home_ml=-135, away_ml=+320, spread=-0.5, total=2.5),
        _make_game("demo-epl-3", sk, st, "Manchester United","Chelsea",          76, home_ml=+110, away_ml=+240, spread=+0.0, total=2.5),
        _make_game("demo-epl-4", sk, st, "Newcastle United", "Brighton",         78, home_ml=-120, away_ml=+300, spread=-0.5, total=2.5),
    ]


def demo_games(sport_key: str) -> list[Game]:
    if sport_key.startswith("americanfootball_nfl"):
        return _nfl()
    if sport_key.startswith("americanfootball_ncaaf"):
        return _ncaaf()
    if sport_key.startswith("basketball_nba"):
        return _nba()
    if sport_key.startswith("basketball_ncaab"):
        return _ncaab()
    if sport_key.startswith("baseball_mlb"):
        return _mlb()
    if sport_key.startswith("icehockey_nhl"):
        return _nhl()
    if sport_key.startswith("soccer_epl"):
        return _epl()
    return []
