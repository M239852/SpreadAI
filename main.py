"""SpreadAI — desktop sports betting edge finder.

Entry point. Launch with `python main.py` after `pip install -r requirements.txt`,
or double-click run.bat on Windows. Logs go to the console and .cache/spreadai.log.
"""
from __future__ import annotations
import logging
import sys
import traceback


def main() -> int:
    from src.utils.logs import setup_logging, LOG_PATH
    setup_logging()
    log = logging.getLogger("spreadai")

    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("customtkinter is not installed. Run:\n    pip install -r requirements.txt")
        return 1

    try:
        from src.ui.app import App
    except Exception:
        traceback.print_exc()
        log.exception("failed to import the UI")
        return 1

    log.info("SpreadAI starting (python %s) — log file: %s", sys.version.split()[0], LOG_PATH)
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
