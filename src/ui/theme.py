"""Centralized theme tokens used across the UI."""

APP_NAME = "SpreadAI"
APP_TAGLINE = "Sports Betting Edge Finder"

# Color palette (dark theme)
BG = "#0b0f17"
BG_ELEV_1 = "#131a26"
BG_ELEV_2 = "#1a2332"
BG_ELEV_3 = "#222d3f"
BORDER = "#2a3547"

TEXT = "#e6ecf5"
TEXT_MUTED = "#8c99ad"
TEXT_DIM = "#5d6a7e"

ACCENT = "#5aa0ff"           # primary blue
ACCENT_HOVER = "#78b3ff"
ACCENT_PRESS = "#3b84e0"

POSITIVE = "#2fc27a"         # green — +EV / wins
NEGATIVE = "#ef5a6f"         # red — -EV / losses
WARNING = "#f4b860"          # amber — questionable/uncertain
INFO = "#7ca6ff"

# Typography
FONT = ("Segoe UI", 12)
FONT_SMALL = ("Segoe UI", 11)
FONT_TINY = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI Semibold", 12)
FONT_HEAD = ("Segoe UI Semibold", 14)
FONT_TITLE = ("Segoe UI Semibold", 20)
FONT_HUGE = ("Segoe UI Semibold", 28)
FONT_MONO = ("Consolas", 12)


def edge_color(edge: float) -> str:
    if edge > 0.03:
        return POSITIVE
    if edge < -0.03:
        return NEGATIVE
    return TEXT_MUTED


def severity_color(status: str) -> str:
    s = (status or "").lower()
    if s in ("out", "injured reserve", "suspended", "doubtful"):
        return NEGATIVE
    if s in ("questionable", "day-to-day", "game-time decision"):
        return WARNING
    return TEXT_MUTED
