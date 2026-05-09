"""
access/base.py
==============
Abstract broker interface that every platform adapter must implement.

All concrete clients (DeribitClient, etc.) inherit from BrokerBase and return
OrderResult objects so callers stay platform-agnostic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OrderResult:
    """Normalised representation of a single order returned by any broker."""
    order_id:      str
    instrument:    str
    direction:     str            # "buy" | "sell"
    amount:        float          # contracts / USD notional depending on broker
    price:         Optional[float]
    state:         str            # "open" | "filled" | "partially_filled" | "cancelled" | "rejected"
    filled_amount: float
    avg_price:     Optional[float]
    label:         Optional[str]
    raw:           dict = field(default_factory=dict, repr=False)


class BrokerBase(ABC):
    """
    Minimal interface every broker adapter must satisfy.

    Authentication is handled internally; callers only need to construct the
    client and call the trading methods.
    """

    @abstractmethod
    def authenticate(self) -> None:
        """Obtain (or refresh) an access token. Called automatically as needed."""

    @abstractmethod
    def place_order(
        self,
        instrument: str,
        direction: str,
        amount: float,
        order_type: str = "limit",
        price: Optional[float] = None,
        label: Optional[str] = None,
        time_in_force: str = "good_til_cancelled",
    ) -> OrderResult:
        """
        Place a buy or sell order.

        Parameters
        ----------
        instrument:    Broker-native instrument name (e.g. "ETH-30MAY25-2000-P")
        direction:     "buy" or "sell"
        amount:        Contract amount (meaning is broker-specific)
        order_type:    "limit" | "market"
        price:         Limit price (required for limit orders)
        label:         Optional user-supplied tag stored on the order
        time_in_force: "good_til_cancelled" | "fill_or_kill" | "immediate_or_cancel"
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order by ID. Returns raw broker response."""

    @abstractmethod
    def get_order_state(self, order_id: str) -> OrderResult:
        """Fetch current state of a single order."""

    @abstractmethod
    def get_open_orders(self, instrument: Optional[str] = None) -> list[OrderResult]:
        """
        Return all open orders, optionally filtered to a specific instrument.
        """

    @abstractmethod
    def get_position(self, instrument: str) -> dict:
        """
        Return current position for the given instrument.
        Returns an empty dict if no position exists.
        """
