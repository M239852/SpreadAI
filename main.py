"""SpreadAI — desktop sports betting edge finder.

Entry point. Launch with `python main.py` after `pip install -r requirements.txt`,
or double-click run.bat on Windows.
"""
from __future__ import annotations
import sys
import traceback


def main() -> int:
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("customtkinter is not installed. Run:\n    pip install -r requirements.txt")
        return 1

    try:
        from src.ui.app import App
    except Exception:
        traceback.print_exc()
        return 1

    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
