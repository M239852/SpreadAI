from __future__ import annotations
from datetime import datetime, timezone


def american_to_decimal(american: int | float) -> float:
    """Convert American odds to decimal odds."""
    a = float(american)
    if a >= 0:
        return 1.0 + a / 100.0
    return 1.0 + 100.0 / abs(a)


def decimal_to_american(decimal: float) -> int:
    """Convert decimal odds to American odds."""
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100.0))
    return int(round(-100.0 / (decimal - 1.0)))


def american_to_implied(american: int | float) -> float:
    """Implied probability from American odds (with the vig)."""
    a = float(american)
    if a >= 0:
        return 100.0 / (a + 100.0)
    return abs(a) / (abs(a) + 100.0)


def devig_two_way(imp_a: float, imp_b: float) -> tuple[float, float]:
    """Remove vig from a two-way market proportionally."""
    total = imp_a + imp_b
    if total <= 0:
        return imp_a, imp_b
    return imp_a / total, imp_b / total


def format_american(american: int | float) -> str:
    a = int(round(float(american)))
    return f"+{a}" if a > 0 else str(a)


def format_pct(p: float, digits: int = 1) -> str:
    return f"{p * 100:.{digits}f}%"


def format_money(amount: float, digits: int = 2) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.{digits}f}"


def format_game_time(iso_str: str) -> str:
    """Format an ISO timestamp into a short human-readable local time."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local = dt.astimezone()
        return local.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
    except Exception:
        return iso_str


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
