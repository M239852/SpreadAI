"""Design tokens shared by every view.

Everything visual — colors, type scale, spacing, radii, layout metrics — lives
here so the views stay declarative. Semantic colors come in a strong/soft pair
(`POSITIVE` for text and marks, `POSITIVE_SOFT` for tinted backgrounds).
"""
from __future__ import annotations

APP_NAME = "SpreadAI"
APP_TAGLINE = "Edge finder"
APP_VERSION = "2.0"

# ---------------------------------------------------------------- palette
BG = "#0a0d13"
BG_ELEV_1 = "#10151f"       # sidebar, cards
BG_ELEV_2 = "#161d2a"       # nested panels
BG_ELEV_3 = "#1e2736"       # chips, inputs
BG_ELEV_4 = "#273245"       # hover
BORDER = "#222c3c"
BORDER_STRONG = "#344259"

TEXT = "#e8edf5"
TEXT_MUTED = "#8b97ab"
TEXT_DIM = "#5b6779"

ACCENT = "#5b9dff"           # primary blue
ACCENT_HOVER = "#7ab1ff"
ACCENT_PRESS = "#3b84e0"
ACCENT_SOFT = "#172740"
ACCENT_TEXT = "#0a0d13"      # text on accent fills

POSITIVE = "#31c583"         # +EV / wins
POSITIVE_SOFT = "#123122"
NEGATIVE = "#f05a6e"         # -EV / losses
NEGATIVE_SOFT = "#3b1b22"
WARNING = "#f2b75c"          # uncertain / marginal
WARNING_SOFT = "#3a2d14"
INFO = "#7ca6ff"
MODEL = "#b48cff"            # model outputs (probability, intervals)
MODEL_SOFT = "#2a1f47"

# ---------------------------------------------------------------- type scale
FONT_FAMILY = "Segoe UI"
FONT_FAMILY_SEMI = "Segoe UI Semibold"
FONT_FAMILY_MONO = "Consolas"

FONT_TINY = (FONT_FAMILY, 10)
FONT_LABEL = (FONT_FAMILY_SEMI, 10)      # uppercase section labels
FONT_SMALL = (FONT_FAMILY, 11)
FONT = (FONT_FAMILY, 12)
FONT_BOLD = (FONT_FAMILY_SEMI, 12)
FONT_SUB = (FONT_FAMILY_SEMI, 13)
FONT_HEAD = (FONT_FAMILY_SEMI, 14)
FONT_TITLE = (FONT_FAMILY_SEMI, 20)
FONT_HUGE = (FONT_FAMILY_SEMI, 28)
FONT_DISPLAY = (FONT_FAMILY_SEMI, 34)
FONT_MONO = (FONT_FAMILY_MONO, 12)
FONT_MONO_SMALL = (FONT_FAMILY_MONO, 11)

# ---------------------------------------------------------------- spacing / radii
SP_1, SP_2, SP_3, SP_4, SP_5, SP_6 = 4, 8, 12, 16, 24, 32
R_SM, R_MD, R_LG, R_PILL = 6, 10, 14, 999

# ---------------------------------------------------------------- layout
SIDEBAR_W = 232
SIDEBAR_W_COLLAPSED = 64
SLIP_W = 384
TOPBAR_H = 58
PAGE_PAD_X = 24


# ---------------------------------------------------------------- helpers

def mix(hex_a: str, hex_b: str, t: float) -> str:
    """Linear blend between two hex colors (t=0 → a, t=1 → b)."""
    t = max(0.0, min(1.0, t))
    a = tuple(int(hex_a[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(hex_b[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def edge_color(edge: float) -> str:
    if edge > 0.03:
        return POSITIVE
    if edge < -0.03:
        return NEGATIVE
    return TEXT_MUTED


def edge_soft(edge: float) -> str:
    if edge > 0.03:
        return POSITIVE_SOFT
    if edge < -0.03:
        return NEGATIVE_SOFT
    return BG_ELEV_3


def confidence_color(conf: float) -> str:
    if conf >= 0.7:
        return POSITIVE
    if conf >= 0.4:
        return WARNING
    return NEGATIVE


def prob_color(p: float) -> str:
    """Color for a hit probability shown on its own (props, pick-em tiles)."""
    if p >= 0.58:
        return POSITIVE
    if p >= 0.48:
        return WARNING
    return NEGATIVE


def severity_color(status: str) -> str:
    s = (status or "").lower()
    if s in ("out", "injured reserve", "suspended", "doubtful"):
        return NEGATIVE
    if s in ("questionable", "day-to-day", "game-time decision"):
        return WARNING
    return TEXT_MUTED


def variant_colors(variant: str) -> tuple[str, str]:
    """(background, foreground) for a semantic pill/badge variant."""
    return {
        "neutral":  (BG_ELEV_3, TEXT_MUTED),
        "plain":    (BG_ELEV_3, TEXT),
        "accent":   (ACCENT_SOFT, ACCENT),
        "positive": (POSITIVE_SOFT, POSITIVE),
        "negative": (NEGATIVE_SOFT, NEGATIVE),
        "warning":  (WARNING_SOFT, WARNING),
        "model":    (MODEL_SOFT, MODEL),
    }.get(variant, (BG_ELEV_3, TEXT_MUTED))


def edge_variant(edge: float) -> str:
    if edge > 0.03:
        return "positive"
    if edge < -0.03:
        return "negative"
    return "neutral"
