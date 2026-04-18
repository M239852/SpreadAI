"""Offline demo slate for player props. Used when PrizePicks fetch fails or
when the user is offline."""
from __future__ import annotations
from datetime import datetime, timezone

from .prizepicks_api import PlayerProp


def _mk(sport_key: str, league: str, player: str, team: str, pos: str, stat: str, line: float, opp: str, desc: str = "") -> PlayerProp:
    return PlayerProp(
        id=f"{sport_key}-{player}-{stat}".replace(" ", "-").lower(),
        player_id=f"{sport_key}-{player}".replace(" ", "-").lower(),
        player_name=player,
        team=team,
        team_name=team,
        position=pos,
        league=league,
        sport_key=sport_key,
        stat_type=stat,
        line=line,
        opponent=opp,
        description=desc or f"vs {opp}",
        start_time=datetime.now(timezone.utc).isoformat(),
        source="Demo",
    )


def demo_props(sport_key: str) -> list[PlayerProp]:
    if sport_key == "basketball_nba":
        return [
            _mk(sport_key, "NBA", "Luka Doncic",       "DAL", "G", "Points",            32.5, "HOU"),
            _mk(sport_key, "NBA", "Luka Doncic",       "DAL", "G", "Assists",            8.5, "HOU"),
            _mk(sport_key, "NBA", "Luka Doncic",       "DAL", "G", "Rebounds",           8.5, "HOU"),
            _mk(sport_key, "NBA", "Nikola Jokic",      "DEN", "C", "Points",            26.5, "PHX"),
            _mk(sport_key, "NBA", "Nikola Jokic",      "DEN", "C", "Rebounds",          12.5, "PHX"),
            _mk(sport_key, "NBA", "Nikola Jokic",      "DEN", "C", "Assists",            9.5, "PHX"),
            _mk(sport_key, "NBA", "Jayson Tatum",      "BOS", "F", "Points",            27.5, "MIL"),
            _mk(sport_key, "NBA", "Jayson Tatum",      "BOS", "F", "3-Pt Made",          3.5, "MIL"),
            _mk(sport_key, "NBA", "Giannis Antetokounmpo", "MIL", "F", "Points",        29.5, "BOS"),
            _mk(sport_key, "NBA", "Giannis Antetokounmpo", "MIL", "F", "Rebounds",      11.5, "BOS"),
            _mk(sport_key, "NBA", "Shai Gilgeous-Alexander", "OKC", "G", "Points",      30.5, "MIN"),
            _mk(sport_key, "NBA", "Shai Gilgeous-Alexander", "OKC", "G", "Assists",      6.5, "MIN"),
            _mk(sport_key, "NBA", "LeBron James",      "LAL", "F", "Points",            24.5, "GSW"),
            _mk(sport_key, "NBA", "LeBron James",      "LAL", "F", "Assists",            7.5, "GSW"),
            _mk(sport_key, "NBA", "Stephen Curry",     "GSW", "G", "Points",            27.5, "LAL"),
            _mk(sport_key, "NBA", "Stephen Curry",     "GSW", "G", "3-Pt Made",          4.5, "LAL"),
            _mk(sport_key, "NBA", "Kevin Durant",      "PHX", "F", "Points",            26.5, "DEN"),
            _mk(sport_key, "NBA", "Devin Booker",      "PHX", "G", "Points",            25.5, "DEN"),
            _mk(sport_key, "NBA", "Anthony Edwards",   "MIN", "G", "Points",            25.5, "OKC"),
            _mk(sport_key, "NBA", "Jalen Brunson",     "NYK", "G", "Points",            27.5, "PHI"),
            _mk(sport_key, "NBA", "Tyrese Haliburton", "IND", "G", "Assists",           10.5, "CLE"),
            _mk(sport_key, "NBA", "Donovan Mitchell",  "CLE", "G", "Points",            26.5, "IND"),
            _mk(sport_key, "NBA", "Trae Young",        "ATL", "G", "Points",            25.5, "CHA"),
            _mk(sport_key, "NBA", "Trae Young",        "ATL", "G", "Assists",           11.5, "CHA"),
        ]
    if sport_key == "americanfootball_nfl":
        return [
            _mk(sport_key, "NFL", "Patrick Mahomes",   "KC",  "QB", "Passing Yards",   267.5, "BUF"),
            _mk(sport_key, "NFL", "Patrick Mahomes",   "KC",  "QB", "Passing TDs",       1.5, "BUF"),
            _mk(sport_key, "NFL", "Josh Allen",        "BUF", "QB", "Passing Yards",   248.5, "KC"),
            _mk(sport_key, "NFL", "Josh Allen",        "BUF", "QB", "Rushing Yards",    39.5, "KC"),
            _mk(sport_key, "NFL", "Travis Kelce",      "KC",  "TE", "Receiving Yards",  72.5, "BUF"),
            _mk(sport_key, "NFL", "Travis Kelce",      "KC",  "TE", "Receptions",        6.5, "BUF"),
            _mk(sport_key, "NFL", "Stefon Diggs",      "BUF", "WR", "Receiving Yards",  78.5, "KC"),
            _mk(sport_key, "NFL", "Jalen Hurts",       "PHI", "QB", "Passing Yards",   232.5, "DAL"),
            _mk(sport_key, "NFL", "Jalen Hurts",       "PHI", "QB", "Rushing Yards",    49.5, "DAL"),
            _mk(sport_key, "NFL", "CeeDee Lamb",       "DAL", "WR", "Receiving Yards",  84.5, "PHI"),
            _mk(sport_key, "NFL", "Dak Prescott",      "DAL", "QB", "Passing Yards",   265.5, "PHI"),
            _mk(sport_key, "NFL", "Saquon Barkley",    "PHI", "RB", "Rushing Yards",    88.5, "DAL"),
            _mk(sport_key, "NFL", "Lamar Jackson",     "BAL", "QB", "Rushing Yards",    54.5, "CIN"),
            _mk(sport_key, "NFL", "Joe Burrow",        "CIN", "QB", "Passing Yards",   258.5, "BAL"),
            _mk(sport_key, "NFL", "Ja'Marr Chase",     "CIN", "WR", "Receiving Yards",  82.5, "BAL"),
            _mk(sport_key, "NFL", "Christian McCaffrey","SF",  "RB", "Rushing Yards",    95.5, "LAR"),
            _mk(sport_key, "NFL", "Tyreek Hill",       "MIA", "WR", "Receiving Yards",  88.5, "NYJ"),
        ]
    if sport_key == "baseball_mlb":
        return [
            _mk(sport_key, "MLB", "Shohei Ohtani",     "LAD", "DH", "Total Bases",       1.5, "SD"),
            _mk(sport_key, "MLB", "Shohei Ohtani",     "LAD", "DH", "Hits",              0.5, "SD"),
            _mk(sport_key, "MLB", "Mookie Betts",      "LAD", "OF", "Hits",              0.5, "SD"),
            _mk(sport_key, "MLB", "Aaron Judge",       "NYY", "OF", "Home Runs",         0.5, "BOS"),
            _mk(sport_key, "MLB", "Aaron Judge",       "NYY", "OF", "Total Bases",       1.5, "BOS"),
            _mk(sport_key, "MLB", "Juan Soto",         "NYY", "OF", "Hits",              0.5, "BOS"),
            _mk(sport_key, "MLB", "Ronald Acuna Jr.",  "ATL", "OF", "Hits",              0.5, "PHI"),
            _mk(sport_key, "MLB", "Bryce Harper",      "PHI", "1B", "Total Bases",       1.5, "ATL"),
            _mk(sport_key, "MLB", "Jose Altuve",       "HOU", "2B", "Hits",              0.5, "TEX"),
            _mk(sport_key, "MLB", "Freddie Freeman",   "LAD", "1B", "Hits",              0.5, "SD"),
            _mk(sport_key, "MLB", "Gerrit Cole",       "NYY", "SP", "Strikeouts",        7.5, "BOS"),
            _mk(sport_key, "MLB", "Tarik Skubal",      "DET", "SP", "Strikeouts",        7.5, "CLE"),
            _mk(sport_key, "MLB", "Paul Skenes",       "PIT", "SP", "Strikeouts",        7.5, "MIL"),
        ]
    if sport_key == "icehockey_nhl":
        return [
            _mk(sport_key, "NHL", "Connor McDavid",    "EDM", "C",  "Points",            1.5, "CGY"),
            _mk(sport_key, "NHL", "Connor McDavid",    "EDM", "C",  "Shots",             4.5, "CGY"),
            _mk(sport_key, "NHL", "Leon Draisaitl",    "EDM", "C",  "Points",            1.5, "CGY"),
            _mk(sport_key, "NHL", "Nathan MacKinnon",  "COL", "C",  "Shots",             4.5, "VGK"),
            _mk(sport_key, "NHL", "Auston Matthews",   "TOR", "C",  "Shots",             4.5, "BOS"),
            _mk(sport_key, "NHL", "Auston Matthews",   "TOR", "C",  "Goals",             0.5, "BOS"),
            _mk(sport_key, "NHL", "David Pastrnak",    "BOS", "RW", "Shots",             4.5, "TOR"),
            _mk(sport_key, "NHL", "Sidney Crosby",     "PIT", "C",  "Points",            0.5, "NYR"),
            _mk(sport_key, "NHL", "Igor Shesterkin",   "NYR", "G",  "Saves",            28.5, "PIT"),
            _mk(sport_key, "NHL", "Jake Oettinger",    "DAL", "G",  "Saves",            27.5, "MIN"),
        ]
    if sport_key == "americanfootball_ncaaf":
        return [
            _mk(sport_key, "NCAAF", "Carson Beck",     "GA",  "QB", "Passing Yards",   251.5, "ALA"),
            _mk(sport_key, "NCAAF", "Quinn Ewers",     "TEX", "QB", "Passing Yards",   278.5, "OU"),
            _mk(sport_key, "NCAAF", "Will Howard",     "OSU", "QB", "Passing Yards",   235.5, "MICH"),
            _mk(sport_key, "NCAAF", "Dillon Gabriel",  "ORE", "QB", "Passing Yards",   268.5, "USC"),
        ]
    return []
