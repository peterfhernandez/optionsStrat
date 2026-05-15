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
from .calendar_db import (
    load_calendar_state,
    save_calendar_state,
    create_calendar_trade,
    close_calendar_trade,
    get_calendar_stats,
)
from .spread_db import (
    load_spread_state,
    save_spread_state,
    create_spread_trade,
    close_spread_trade,
    get_spread_stats,
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
    "load_calendar_state",
    "save_calendar_state",
    "create_calendar_trade",
    "close_calendar_trade",
    "get_calendar_stats",
    "load_spread_state",
    "save_spread_state",
    "create_spread_trade",
    "close_spread_trade",
    "get_spread_stats",
]
