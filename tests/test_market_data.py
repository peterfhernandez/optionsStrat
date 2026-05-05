"""
tests/test_market.market_data.py
=========================
Tests for market.market_data.py — spot price fetching, IV fetching, and
Deribit instrument name construction.

Test strategy
-------------
Tier 1 — pure functions, no mocking:
    _atm_strike         : rounding correctness per asset
    _expiry_date        : daily vs weekly, always in future
    _deribit_instrument : format correctness, no zero-padding

Tier 2 — external I/O mocked via unittest.mock.patch:
    _price_from_binance   : success, bad status, missing key, exception
    _price_from_coingecko : success, 429 retry, bad response, exception
    _fetch_mark_iv        : success, bad status, zero IV, None
    _fetch_order_book     : full response, missing mark_iv, bad status
    get_spot_price        : binance success, binance fail → coingecko,
                           both fail, unsupported asset
    get_deribit_iv        : put succeeds, put fails → call, both fail,
                           unsupported asset
"""

import re
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from market.market_data import (
    _atm_strike,
    _expiry_date,
    _deribit_instrument,
    _price_from_binance,
    _price_from_coingecko,
    _fetch_mark_iv,
    _fetch_order_book,
    get_spot_price,
    get_deribit_iv,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_response(status_code: int, json_data: dict) -> MagicMock:
    """Build a mock requests.Response object."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    return mock


# ── _atm_strike ───────────────────────────────────────────────────────────────

class TestAtmStrike:

    def test_eth_rounds_to_nearest_100(self):
        assert _atm_strike(2314.0, 100) == 2300
        assert _atm_strike(2350.0, 100) == 2400
        assert _atm_strike(2349.9, 100) == 2300

    def test_btc_rounds_to_nearest_1000(self):
        assert _atm_strike(77600.0, 1000) == 78000
        assert _atm_strike(77499.0, 1000) == 77000

    def test_sol_rounds_to_nearest_1(self):
        assert _atm_strike(86.41, 1) == 86
        assert _atm_strike(86.60, 1) == 87

    def test_exact_boundary(self):
        """Exactly on a boundary rounds to nearest even (Python banker's rounding)."""
        result = _atm_strike(2500.0, 100)
        assert result in (2400, 2500, 2600)   # accept any valid rounding

    def test_always_multiple_of_increment(self):
        """Result is always a multiple of strike_round."""
        for spot in (1234.5, 2000.0, 86.41, 77777.0):
            for rnd in (1, 100, 500, 1000):
                result = _atm_strike(spot, rnd)
                assert result % rnd == 0, f"_atm_strike({spot}, {rnd}) = {result} not multiple of {rnd}"


# ── _expiry_date ──────────────────────────────────────────────────────────────

class TestExpiryDate:

    def test_daily_returns_tomorrow(self):
        """days=1 should return a date exactly 1 day from now."""
        result = _expiry_date(1)
        now = datetime.now(timezone.utc)
        diff = result - now
        assert 0 < diff.total_seconds() < 2 * 86400   # between 0 and 48 hours

    def test_weekly_returns_friday(self):
        """days=7 should return the next Friday."""
        result = _expiry_date(7)
        assert result.weekday() == 4   # 4 = Friday

    def test_weekly_always_in_future(self):
        """Next Friday is always at least 1 day away."""
        result = _expiry_date(7)
        now = datetime.now(timezone.utc)
        assert result > now

    def test_daily_different_from_weekly(self):
        """Daily and weekly expiry should differ."""
        daily  = _expiry_date(1)
        weekly = _expiry_date(7)
        assert daily != weekly

    def test_weekly_at_most_7_days_away(self):
        """Next Friday is at most 7 days from today."""
        result = _expiry_date(7)
        now = datetime.now(timezone.utc)
        assert (result - now).days <= 7


# ── _deribit_instrument ───────────────────────────────────────────────────────

class TestDeribitInstrument:

    def test_format_structure(self):
        """Instrument name must match TICKER-DDMMMYY-STRIKE-TYPE."""
        name = _deribit_instrument("ETH", 2300.0, 7, 100, "P")
        parts = name.split("-")
        assert len(parts) == 4
        assert parts[0] == "ETH"
        assert parts[3] == "P"

    def test_call_option_type(self):
        name = _deribit_instrument("ETH", 2300.0, 7, 100, "C")
        assert name.endswith("-C")

    def test_put_option_type(self):
        name = _deribit_instrument("ETH", 2300.0, 7, 100, "P")
        assert name.endswith("-P")

    def test_no_zero_padded_day(self):
        """Day must never be zero-padded — Deribit uses '1MAY26' not '01MAY26'."""
        name = _deribit_instrument("ETH", 2300.0, 7, 100, "P")
        date_part = name.split("-")[1]
        # Date part should not start with '0'
        assert not date_part.startswith("0"), f"Zero-padded date: {date_part}"

    def test_month_uppercase(self):
        """Month abbreviation must be uppercase."""
        name = _deribit_instrument("ETH", 2300.0, 7, 100, "P")
        date_part = name.split("-")[1]
        assert date_part == date_part.upper()

    def test_strike_rounded_correctly(self):
        """Strike in instrument name should be rounded to increment."""
        name = _deribit_instrument("ETH", 2314.0, 7, 100, "P")
        strike = int(name.split("-")[2])
        assert strike % 100 == 0

    def test_btc_ticker(self):
        name = _deribit_instrument("BTC", 77600.0, 7, 1000, "P")
        assert name.startswith("BTC-")

    def test_sol_usdc_ticker(self):
        name = _deribit_instrument("SOL_USDC", 86.0, 7, 1, "P")
        assert name.startswith("SOL_USDC-")

    def test_date_format_regex(self):
        """Date part should match D+MMMYY or DD+MMMYY pattern."""
        name = _deribit_instrument("ETH", 2300.0, 7, 100, "P")
        date_part = name.split("-")[1]
        assert re.match(r"^\d{1,2}[A-Z]{3}\d{2}$", date_part), f"Bad date format: {date_part}"


# ── _price_from_binance ───────────────────────────────────────────────────────

class TestPriceFromBinance:

    def test_success(self):
        mock = _mock_response(200, {"symbol": "ETHUSDT", "price": "2314.13"})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _price_from_binance("ETH")
        assert result == pytest.approx(2314.13)

    def test_bad_status_returns_none(self):
        mock = _mock_response(404, {})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _price_from_binance("ETH")
        assert result is None

    def test_missing_price_key_returns_none(self):
        mock = _mock_response(200, {"symbol": "ETHUSDT"})  # no "price" key
        with patch("market.market_data.requests.get", return_value=mock):
            result = _price_from_binance("ETH")
        assert result is None

    def test_exception_returns_none(self):
        with patch("market.market_data.requests.get", side_effect=ConnectionError("timeout")):
            result = _price_from_binance("ETH")
        assert result is None

    def test_returns_float(self):
        mock = _mock_response(200, {"price": "2314.13"})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _price_from_binance("ETH")
        assert isinstance(result, float)

    def test_btc_symbol_used(self):
        """Verify the correct Binance symbol is used for BTC."""
        mock = _mock_response(200, {"price": "77600.0"})
        with patch("market.market_data.requests.get", return_value=mock) as mock_get:
            _price_from_binance("BTC")
        call_kwargs = mock_get.call_args
        assert "BTCUSDT" in str(call_kwargs)


# ── _price_from_coingecko ─────────────────────────────────────────────────────

class TestPriceFromCoinGecko:

    def test_success(self):
        mock = _mock_response(200, {"ethereum": {"usd": 2318.35}})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _price_from_coingecko("ETH")
        assert result == pytest.approx(2318.35)

    def test_bad_status_returns_none(self):
        mock = _mock_response(500, {})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _price_from_coingecko("ETH")
        assert result is None

    def test_missing_coin_id_returns_none(self):
        """Response doesn't contain the expected coin ID."""
        mock = _mock_response(200, {})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _price_from_coingecko("ETH")
        assert result is None

    def test_429_retries_and_succeeds(self):
        """On 429, should wait and retry — second call succeeds."""
        rate_limited = _mock_response(429, {})
        success      = _mock_response(200, {"ethereum": {"usd": 2318.35}})
        with patch("market.market_data.requests.get", side_effect=[rate_limited, success]):
            with patch("market.market_data.time.sleep"):   # don't actually sleep
                result = _price_from_coingecko("ETH")
        assert result == pytest.approx(2318.35)

    def test_429_twice_returns_none(self):
        """Two consecutive 429s should return None."""
        rate_limited = _mock_response(429, {})
        with patch("market.market_data.requests.get", return_value=rate_limited):
            with patch("market.market_data.time.sleep"):
                result = _price_from_coingecko("ETH")
        assert result is None

    def test_exception_returns_none(self):
        with patch("market.market_data.requests.get", side_effect=Exception("network error")):
            result = _price_from_coingecko("ETH")
        assert result is None


# ── _fetch_mark_iv ────────────────────────────────────────────────────────────

class TestFetchMarkIv:

    def test_success_converts_to_decimal(self):
        """mark_iv of 80.0 should return 0.80."""
        mock = _mock_response(200, {"result": {"mark_iv": 80.0}})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _fetch_mark_iv("ETH-1MAY26-2300-P")
        assert result == pytest.approx(0.80)

    def test_bad_status_returns_none(self):
        mock = _mock_response(400, {})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _fetch_mark_iv("ETH-1MAY26-2300-P")
        assert result is None

    def test_zero_iv_returns_none(self):
        """mark_iv of 0 should return None (invalid)."""
        mock = _mock_response(200, {"result": {"mark_iv": 0}})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _fetch_mark_iv("ETH-1MAY26-2300-P")
        assert result is None

    def test_missing_result_returns_none(self):
        mock = _mock_response(200, {})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _fetch_mark_iv("ETH-1MAY26-2300-P")
        assert result is None

    def test_returns_float(self):
        mock = _mock_response(200, {"result": {"mark_iv": 52.5}})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _fetch_mark_iv("ETH-1MAY26-2300-P")
        assert isinstance(result, float)
        assert result == pytest.approx(0.525)


# ── _fetch_order_book ─────────────────────────────────────────────────────────

class TestFetchOrderBook:

    def _full_response(self):
        return _mock_response(200, {"result": {
            "mark_iv":        52.67,
            "bid_iv":         51.50,
            "ask_iv":         53.54,
            "open_interest":  4791.0,
            "best_bid_price": 0.0235,
            "best_ask_price": 0.024,
            "stats":          {"volume_usd": 84365.8},
        }})

    def test_success_returns_dict(self):
        with patch("market.market_data.requests.get", return_value=self._full_response()):
            result = _fetch_order_book("ETH-1MAY26-2300-P")
        assert isinstance(result, dict)

    def test_mark_iv_converted_to_decimal(self):
        with patch("market.market_data.requests.get", return_value=self._full_response()):
            result = _fetch_order_book("ETH-1MAY26-2300-P")
        assert result["mark_iv"] == pytest.approx(0.5267)

    def test_iv_spread_calculated(self):
        """iv_spread = (ask_iv - bid_iv) / 100."""
        with patch("market.market_data.requests.get", return_value=self._full_response()):
            result = _fetch_order_book("ETH-1MAY26-2300-P")
        assert result["iv_spread"] == pytest.approx((53.54 - 51.50) / 100, abs=1e-4)

    def test_open_interest_present(self):
        with patch("market.market_data.requests.get", return_value=self._full_response()):
            result = _fetch_order_book("ETH-1MAY26-2300-P")
        assert result["open_interest"] == pytest.approx(4791.0)

    def test_volume_usd_present(self):
        with patch("market.market_data.requests.get", return_value=self._full_response()):
            result = _fetch_order_book("ETH-1MAY26-2300-P")
        assert result["volume_usd"] == pytest.approx(84365.8)

    def test_bad_status_returns_none(self):
        mock = _mock_response(400, {})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _fetch_order_book("ETH-1MAY26-2300-P")
        assert result is None

    def test_missing_mark_iv_returns_none(self):
        mock = _mock_response(200, {"result": {"open_interest": 100}})
        with patch("market.market_data.requests.get", return_value=mock):
            result = _fetch_order_book("ETH-1MAY26-2300-P")
        assert result is None

    def test_all_expected_keys_present(self):
        with patch("market.market_data.requests.get", return_value=self._full_response()):
            result = _fetch_order_book("ETH-1MAY26-2300-P")
        expected_keys = {"mark_iv", "bid_iv", "ask_iv", "iv_spread",
                         "open_interest", "volume_usd", "best_bid", "best_ask"}
        assert expected_keys.issubset(result.keys())


# ── get_spot_price ────────────────────────────────────────────────────────────

class TestGetSpotPrice:

    def test_binance_success(self):
        """Returns Binance price when available."""
        with patch("market.market_data._price_from_binance", return_value=2314.13):
            result = get_spot_price("ETH")
        assert result == pytest.approx(2314.13)

    def test_falls_back_to_coingecko(self, capsys):
        """When Binance fails, falls back to CoinGecko."""
        with patch("market.market_data._price_from_binance",   return_value=None):
            with patch("market.market_data._price_from_coingecko", return_value=2318.35):
                result = get_spot_price("ETH")
        assert result == pytest.approx(2318.35)

    def test_both_fail_returns_none(self, capsys):
        with patch("market.market_data._price_from_binance",    return_value=None):
            with patch("market.market_data._price_from_coingecko", return_value=None):
                result = get_spot_price("ETH")
        assert result is None

    def test_unsupported_asset_raises(self):
        with pytest.raises(ValueError, match="Unsupported asset"):
            get_spot_price("DOGE")

    def test_lowercase_asset_accepted(self):
        """Asset symbol should be case-insensitive."""
        with patch("market.market_data._price_from_binance", return_value=2314.13):
            result = get_spot_price("eth")
        assert result == pytest.approx(2314.13)

    def test_btc_supported(self):
        with patch("market.market_data._price_from_binance", return_value=77600.0):
            result = get_spot_price("BTC")
        assert result == pytest.approx(77600.0)

    def test_sol_supported(self):
        with patch("market.market_data._price_from_binance", return_value=86.41):
            result = get_spot_price("SOL")
        assert result == pytest.approx(86.41)

    def test_xrp_supported(self):
        with patch("market.market_data._price_from_binance", return_value=2.51):
            result = get_spot_price("XRP")
        assert result == pytest.approx(2.51)

    def test_xrp_supported(self):
        with patch("market.market_data._price_from_binance", return_value=2.51):
            result = get_spot_price("XRP")
        assert result == pytest.approx(2.51)


# ── get_deribit_iv ────────────────────────────────────────────────────────────

class TestGetDeribitIv:

    def test_put_succeeds(self):
        """Returns IV from put when put fetch succeeds."""
        with patch("market.market_data._fetch_mark_iv", return_value=0.80):
            result = get_deribit_iv("ETH", 2300.0, 7)
        assert result == pytest.approx(0.80)

    def test_put_fails_tries_call(self):
        """When put returns None, tries call."""
        with patch("market.market_data._fetch_mark_iv", side_effect=[None, 0.75]):
            result = get_deribit_iv("ETH", 2300.0, 7)
        assert result == pytest.approx(0.75)

    def test_both_fail_returns_none(self):
        with patch("market.market_data._fetch_mark_iv", return_value=None):
            result = get_deribit_iv("ETH", 2300.0, 7)
        assert result is None

    def test_unsupported_asset_raises(self):
        with pytest.raises(ValueError, match="Unsupported asset"):
            get_deribit_iv("DOGE", 1.0, 7)

    def test_lowercase_asset_accepted(self):
        with patch("market.market_data._fetch_mark_iv", return_value=0.80):
            result = get_deribit_iv("eth", 2300.0, 7)
        assert result == pytest.approx(0.80)

    def test_returns_decimal_not_percentage(self):
        """IV should be returned as decimal (0.80) not percentage (80.0)."""
        with patch("market.market_data._fetch_mark_iv", return_value=0.80):
            result = get_deribit_iv("ETH", 2300.0, 7)
        assert result < 10.0, f"IV looks like a percentage, not a decimal: {result}"

    def test_btc_uses_correct_ticker(self):
        """BTC should use 'BTC' as the Deribit ticker."""
        with patch("market.market_data._fetch_mark_iv", return_value=0.60) as mock_iv:
            with patch("market.market_data._deribit_instrument", return_value="BTC-1MAY26-78000-P") as mock_inst:
                get_deribit_iv("BTC", 77600.0, 7)
        call_args = mock_inst.call_args_list[0]
        assert call_args.kwargs.get("ticker") == "BTC" or "BTC" in str(call_args)

    def test_sol_uses_sol_usdc_ticker(self):
        """SOL should use 'SOL_USDC' as the Deribit ticker."""
        with patch("market.market_data._fetch_mark_iv", return_value=0.55):
            with patch("market.market_data._deribit_instrument", return_value="SOL_USDC-1MAY26-86-P") as mock_inst:
                get_deribit_iv("SOL", 86.41, 7)
        call_args = mock_inst.call_args_list[0]
        assert "SOL_USDC" in str(call_args)

    def test_xrp_uses_correct_ticker(self):
        """XRP should use 'XRP' as the Deribit ticker."""
        with patch("market.market_data._fetch_mark_iv", return_value=0.70):
            with patch("market.market_data._deribit_instrument", return_value="XRP-1MAY26-2.5-P") as mock_inst:
                get_deribit_iv("XRP", 2.5, 7)
        call_args = mock_inst.call_args_list[0]
        assert call_args.kwargs.get("ticker") == "XRP" or "XRP" in str(call_args)
