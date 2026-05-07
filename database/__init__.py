"""Database helper functions for trading strategies."""
from .wheel_db import (
    load_wheel_state,
    save_wheel_state,
    create_single_trade,
    close_single_trade,
    get_wheel_stats,
)
from .strangle_db import (
    load_strangle_state,
    save_strangle_state,
    create_strangle_trade,
    close_strangle_trade,
    get_strangle_stats,
)

__all__ = [
    "load_wheel_state",
    "save_wheel_state",
    "create_single_trade",
    "close_single_trade",
    "get_wheel_stats",
    "load_strangle_state",
    "save_strangle_state",
    "create_strangle_trade",
    "close_strangle_trade",
    "get_strangle_stats",
]
