"""
access
======
Broker access layer for the options strategy tool.

Provides a platform-agnostic BrokerBase interface and concrete adapters
for each supported exchange.

Supported adapters
------------------
DeribitClient   — Deribit REST API (paper via test.deribit.com, live via www.deribit.com)

Usage
-----
    from access import DeribitClient, make_instrument
    from datetime import date

    client = DeribitClient(paper=True)   # credentials from env vars
    instrument = make_instrument("ETH", date(2025, 5, 30), 2000, "put")
    result = client.place_order(instrument, "sell", 1, "limit", 0.05)
"""

from .base import BrokerBase, OrderResult
from .deribit import DeribitClient, DeribitError, make_instrument

__all__ = [
    "BrokerBase",
    "OrderResult",
    "DeribitClient",
    "DeribitError",
    "make_instrument",
]
