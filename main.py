"""
main.py
=======
Entry point for the Crypto Options Strategy Tool.

Delegates to ui.menus for the menu loop. This is the main entry point only.
All menu and strategy logic is in ui.menus or strategy modules.
"""

# ── Stdlib ────────────────────────────────────────────────────────────────────
import sys

# ── Third-party dependency check ──────────────────────────────────────────────
try:
    import requests  # noqa: F401
except ImportError:
    print("Please run: pip install requests openpyxl colorama")
    sys.exit(1)

try:
    import openpyxl  # noqa: F401
except ImportError:
    print("Please run: pip install requests openpyxl")
    sys.exit(1)

try:
    import colorama  # noqa: F401
except ImportError:
    print("Please run: pip install requests openpyxl colorama")
    sys.exit(1)

# ── Database ──────────────────────────────────────────────────────────────────
from models import init_db
init_db()

# ── Menu system ───────────────────────────────────────────────────────────────
from ui.menus import run_app


if __name__ == "__main__":
    run_app()