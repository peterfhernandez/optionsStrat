"""
access/deribit.py
=================
Deribit broker adapter.

Paper trading  →  test.deribit.com  (Deribit Testnet)
Live trading   →  www.deribit.com

Authentication uses the Deribit OAuth2 client-credentials flow
(POST /public/auth).  Tokens are cached and refreshed automatically.

Credentials
-----------
Store credentials in .env (see .env.example).  Paper and live keys are kept
separate so there is no risk of accidentally using live credentials while
testing:

    DERIBIT_PAPER_CLIENT_ID=...       # testnet key
    DERIBIT_PAPER_CLIENT_SECRET=...
    DERIBIT_LIVE_CLIENT_ID=...        # mainnet key
    DERIBIT_LIVE_CLIENT_SECRET=...

DeribitClient(paper=True)  reads the PAPER vars.
DeribitClient(paper=False) reads the LIVE  vars.

Explicit client_id / client_secret arguments always override env vars.

Instrument names
----------------
Deribit options follow the pattern:  {TICKER}-{DDMMMYY}-{STRIKE}-{C|P}
Examples:
    ETH-30MAY25-2000-P
    BTC-27JUN25-90000-C
    SOL_USDC-30MAY25-150-P   (linear USDC-settled)

Use deribit.make_instrument() to build these from trade parameters.
"""

import os
import time
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

from .base import BrokerBase, OrderResult

# Load .env from the project root (two levels up from this file).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

_PAPER_URL = "https://test.deribit.com/api/v2"
_LIVE_URL  = "https://www.deribit.com/api/v2"

# How many seconds before expiry to pre-emptively refresh the token.
_TOKEN_REFRESH_BUFFER = 60

# Deribit asset ticker for each supported asset symbol.
_ASSET_TICKER = {
    "ETH": "ETH",
    "BTC": "BTC",
    "SOL": "SOL_USDC",
    "XRP": "XRP_USDC",
}


def make_instrument(asset: str, expiry: date, strike: float, option_type: str) -> str:
    """
    Build a Deribit instrument name from trade parameters.

    Parameters
    ----------
    asset:       "ETH" | "BTC" | "SOL" | "XRP"
    expiry:      option expiry date
    strike:      strike price (rounded to exchange increment)
    option_type: "put" | "call"  (case-insensitive)

    Returns
    -------
    e.g. "ETH-30MAY25-2000-P"
    """
    ticker = _ASSET_TICKER.get(asset.upper())
    if ticker is None:
        raise ValueError(f"Unsupported asset '{asset}'. Supported: {list(_ASSET_TICKER)}")

    exp_str = expiry.strftime("%d%b%y").upper()   # "30MAY25"
    opt     = "P" if option_type.lower().startswith("p") else "C"

    # Deribit expects integer strikes for most assets.
    strike_str = str(int(strike)) if strike == int(strike) else str(strike)

    return f"{ticker}-{exp_str}-{strike_str}-{opt}"


class DeribitError(Exception):
    """Raised when the Deribit API returns an error response."""


class DeribitClient(BrokerBase):
    """
    Deribit REST API client.

    Usage
    -----
    client = DeribitClient(paper=True)          # reads env vars for credentials
    client = DeribitClient("id", "secret")      # explicit credentials
    result = client.place_order("ETH-30MAY25-2000-P", "sell", 1, "limit", 0.05)
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        paper: bool = True,
    ) -> None:
        env_prefix   = "DERIBIT_PAPER" if paper else "DERIBIT_LIVE"
        self._id     = client_id     or os.environ.get(f"{env_prefix}_CLIENT_ID",     "")
        self._secret = client_secret or os.environ.get(f"{env_prefix}_CLIENT_SECRET", "")
        self._base   = _PAPER_URL if paper else _LIVE_URL
        self.paper   = paper

        self._token:        Optional[str] = None
        self._token_expiry: float         = 0.0

        if not self._id or not self._secret:
            mode = "paper" if paper else "live"
            raise ValueError(
                f"Deribit {mode} credentials missing. "
                f"Set {env_prefix}_CLIENT_ID and {env_prefix}_CLIENT_SECRET "
                f"in .env (see .env.example), or pass them to DeribitClient()."
            )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _ensure_auth(self) -> None:
        """Authenticate if we have no token or it is about to expire."""
        if self._token and time.time() < self._token_expiry - _TOKEN_REFRESH_BUFFER:
            return
        self.authenticate()

    def _request(self, method: str, params: Optional[dict] = None) -> dict:
        """
        Execute a single JSON-RPC style REST call and return the 'result' field.

        Raises DeribitError on API-level errors.
        Raises requests.HTTPError on HTTP-level errors.
        """
        url  = f"{self._base}/{method}"
        resp = requests.get(url, params=params or {}, timeout=10)
        resp.raise_for_status()
        body = resp.json()

        if "error" in body:
            err = body["error"]
            raise DeribitError(f"Deribit API error {err.get('code')}: {err.get('message')}")

        return body.get("result", body)

    def _private_get(self, method: str, params: Optional[dict] = None) -> dict:
        """GET from an authenticated (private) endpoint."""
        self._ensure_auth()
        url     = f"{self._base}/{method}"
        headers = {"Authorization": f"Bearer {self._token}"}
        resp    = requests.get(url, params=params or {}, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json()

        if "error" in body:
            err = body["error"]
            raise DeribitError(f"Deribit API error {err.get('code')}: {err.get('message')}")

        return body.get("result", body)

    def _private_post(self, method: str, params: dict) -> dict:
        """POST to an authenticated (private) endpoint."""
        self._ensure_auth()
        url     = f"{self._base}/{method}"
        headers = {"Authorization": f"Bearer {self._token}"}
        resp    = requests.post(url, json=params, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json()

        if "error" in body:
            err = body["error"]
            raise DeribitError(f"Deribit API error {err.get('code')}: {err.get('message')}")

        return body.get("result", body)

    @staticmethod
    def _parse_order(raw: dict) -> OrderResult:
        """Normalise a Deribit order object into an OrderResult."""
        order = raw.get("order", raw)   # /private/buy wraps it under "order"
        return OrderResult(
            order_id      = order.get("order_id",       ""),
            instrument    = order.get("instrument_name", ""),
            direction     = order.get("direction",       ""),
            amount        = float(order.get("amount",         0)),
            price         = order.get("price"),
            state         = order.get("order_state",    "unknown"),
            filled_amount = float(order.get("filled_amount", 0)),
            avg_price     = order.get("average_price"),
            label         = order.get("label"),
            raw           = raw,
        )

    # ── BrokerBase implementation ─────────────────────────────────────────────

    def authenticate(self) -> None:
        """
        Obtain an access token via the client-credentials grant.
        Token lifetime is returned by the API and cached for subsequent calls.
        """
        result = self._request(
            "public/auth",
            {
                "grant_type":    "client_credentials",
                "client_id":     self._id,
                "client_secret": self._secret,
            },
        )
        self._token        = result["access_token"]
        self._token_expiry = time.time() + result.get("expires_in", 900)
        logger.debug(
            "Deribit authenticated (%s). Token expires in %ds.",
            "paper" if self.paper else "live",
            result.get("expires_in", 900),
        )

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
        Place an options order on Deribit.

        For limit orders, price is required.
        For market orders, price is ignored.

        Amount semantics (Deribit):
          • BTC/ETH inverse contracts: USD notional (minimum 10 USD)
          • SOL_USDC / XRP_USDC linear: number of contracts (minimum 1)
        """
        if direction not in ("buy", "sell"):
            raise ValueError(f"direction must be 'buy' or 'sell', got '{direction}'")
        if order_type == "limit" and price is None:
            raise ValueError("price is required for limit orders")

        endpoint = f"private/{direction}"
        params: dict = {
            "instrument_name": instrument,
            "amount":          amount,
            "type":            order_type,
            "time_in_force":   time_in_force,
        }
        if order_type == "limit":
            params["price"] = price
        if label:
            params["label"] = label

        raw = self._private_post(endpoint, params)
        result = self._parse_order(raw)
        logger.info(
            "Order placed: %s %s %s %s @ %s → %s (id=%s)",
            direction, amount, instrument, order_type, price,
            result.state, result.order_id,
        )
        return result

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order. Returns the raw Deribit response."""
        result = self._private_post("private/cancel", {"order_id": order_id})
        logger.info("Order cancelled: %s", order_id)
        return result

    def get_order_state(self, order_id: str) -> OrderResult:
        """Fetch current state of a single order by ID."""
        raw = self._private_get("private/get_order_state", {"order_id": order_id})
        return self._parse_order(raw)

    def get_open_orders(self, instrument: Optional[str] = None) -> list[OrderResult]:
        """
        Return all open orders for the account, optionally filtered by instrument.
        When no instrument is given, queries each supported currency in turn.
        """
        if instrument:
            raw_list = self._private_get(
                "private/get_open_orders_by_instrument",
                {"instrument_name": instrument},
            )
            if isinstance(raw_list, dict):
                raw_list = raw_list.get("orders", [])
            return [self._parse_order(o) for o in raw_list]

        # Deribit requires a specific currency — fetch for each and merge.
        orders: list[OrderResult] = []
        for currency in ("BTC", "ETH", "USDC"):
            raw_list = self._private_get(
                "private/get_open_orders_by_currency",
                {"currency": currency},
            )
            if isinstance(raw_list, dict):
                raw_list = raw_list.get("orders", [])
            orders.extend(self._parse_order(o) for o in raw_list)
        return orders

    def get_position(self, instrument: str) -> dict:
        """
        Return current position for the given instrument.
        Returns an empty dict if no position is held.
        """
        try:
            return self._private_get(
                "private/get_position",
                {"instrument_name": instrument},
            )
        except DeribitError as exc:
            if "Position not found" in str(exc):
                return {}
            raise
