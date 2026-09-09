"""Logging setup: console + a rotating file in .cache/spreadai.log.

Tk callbacks that raise, worker threads that fail and view renders that error
all end up here, so a stuck screen always leaves a trace the user can send.
"""
from __future__ import annotations
import logging
import logging.handlers
import sys

from .storage import CACHE_DIR

LOG_PATH = CACHE_DIR / "spreadai.log"
_configured = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _configured
    root = logging.getLogger()
    if _configured:
        return root
    _configured = True
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        root.warning("could not open log file at %s", LOG_PATH)
    return root
