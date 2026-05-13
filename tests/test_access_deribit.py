"""
tests/test_access_deribit.py
============================
Unit tests for the access.deribit module.

All HTTP calls are mocked — no live network required.
"""

import time
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from access import DeribitClient, DeribitError, OrderResult, make_instrument
from access.deribit import _PAPER_URL, _LIVE_URL


# ── make_instrument ───────────────────────────────────────────────────────────

class TestMakeInstrument:
    def test_eth_put(self):
        result = make_instrument("ETH", date(2025, 5, 30), 2000, "put")
        assert result == "ETH-30MAY25-2000-P"

    def test_btc_call(self):
        result = make_instrument("BTC", date(2025, 6, 27), 90000, "call")
        assert result == "BTC-27JUN25-90000-C"

    def test_sol_linear(self):
        result = make_instrument("SOL", date(2025, 5, 30), 150, "put")
        assert result == "SOL_USDC-30MAY25-150-P"

    def test_xrp_linear(self):
        result = make_instrument("XRP", date(2025, 5, 30), 2, "call")
        assert result == "XRP_USDC-30MAY25-2-C"

    def test_case_insensitive_type(self):
        assert make_instrument("ETH", date(2025, 5, 30), 2000, "PUT") == "ETH-30MAY25-2000-P"
        assert make_instrument("ETH", date(2025, 5, 30), 2000, "Call") == "ETH-30MAY25-2000-C"

    def test_unsupported_asset_raises(self):
        with pytest.raises(ValueError, match="Unsupported asset"):
            make_instrument("DOGE", date(2025, 5, 30), 1, "put")

    def test_decimal_strike(self):
        result = make_instrument("ETH", date(2025, 5, 30), 2000.5, "put")
        assert "2000.5" in result


# ── DeribitClient construction ────────────────────────────────────────────────

class TestDeribitClientInit:
    def test_paper_url(self):
        c = DeribitClient("id", "secret", paper=True)
        assert c._base == _PAPER_URL
        assert c.paper is True

    def test_live_url(self):
        c = DeribitClient("id", "secret", paper=False)
        assert c._base == _LIVE_URL
        assert c.paper is False

    def test_missing_paper_credentials_raises(self, monkeypatch):
        monkeypatch.delenv("DERIBIT_PAPER_CLIENT_ID",     raising=False)
        monkeypatch.delenv("DERIBIT_PAPER_CLIENT_SECRET", raising=False)
        with pytest.raises(ValueError, match="paper credentials missing"):
            DeribitClient(paper=True)

    def test_missing_live_credentials_raises(self, monkeypatch):
        monkeypatch.delenv("DERIBIT_LIVE_CLIENT_ID",     raising=False)
        monkeypatch.delenv("DERIBIT_LIVE_CLIENT_SECRET", raising=False)
        with pytest.raises(ValueError, match="live credentials missing"):
            DeribitClient(paper=False)

    def test_paper_credentials_from_env(self, monkeypatch):
        monkeypatch.setenv("DERIBIT_PAPER_CLIENT_ID",     "paper_id")
        monkeypatch.setenv("DERIBIT_PAPER_CLIENT_SECRET", "paper_secret")
        c = DeribitClient(paper=True)
        assert c._id     == "paper_id"
        assert c._secret == "paper_secret"

    def test_live_credentials_from_env(self, monkeypatch):
        monkeypatch.setenv("DERIBIT_LIVE_CLIENT_ID",     "live_id")
        monkeypatch.setenv("DERIBIT_LIVE_CLIENT_SECRET", "live_secret")
        c = DeribitClient(paper=False)
        assert c._id     == "live_id"
        assert c._secret == "live_secret"

    def test_paper_and_live_env_vars_are_isolated(self, monkeypatch):
        monkeypatch.setenv("DERIBIT_PAPER_CLIENT_ID",     "paper_id")
        monkeypatch.setenv("DERIBIT_PAPER_CLIENT_SECRET", "paper_secret")
        monkeypatch.setenv("DERIBIT_LIVE_CLIENT_ID",      "live_id")
        monkeypatch.setenv("DERIBIT_LIVE_CLIENT_SECRET",  "live_secret")
        paper = DeribitClient(paper=True)
        live  = DeribitClient(paper=False)
        assert paper._id != live._id
        assert paper._id == "paper_id"
        assert live._id  == "live_id"

    def test_explicit_creds_take_priority(self, monkeypatch):
        monkeypatch.setenv("DERIBIT_PAPER_CLIENT_ID",     "env_id")
        monkeypatch.setenv("DERIBIT_PAPER_CLIENT_SECRET", "env_secret")
        c = DeribitClient("explicit_id", "explicit_secret", paper=True)
        assert c._id == "explicit_id"


# ── Authentication ────────────────────────────────────────────────────────────

def _mock_auth_response():
    return {"access_token": "tok123", "expires_in": 900}


def _make_client():
    return DeribitClient("test_id", "test_secret", paper=True)


class TestAuthentication:
    def test_authenticate_sets_token(self):
        client = _make_client()
        with patch.object(client, "_request", return_value=_mock_auth_response()) as mock_req:
            client.authenticate()
            assert client._token == "tok123"
            assert client._token_expiry > time.time()
            mock_req.assert_called_once_with(
                "public/auth",
                {
                    "grant_type":    "client_credentials",
                    "client_id":     "test_id",
                    "client_secret": "test_secret",
                },
            )

    def test_ensure_auth_skips_when_valid(self):
        client = _make_client()
        client._token        = "existing_tok"
        client._token_expiry = time.time() + 500
        with patch.object(client, "authenticate") as mock_auth:
            client._ensure_auth()
            mock_auth.assert_not_called()

    def test_ensure_auth_refreshes_when_expired(self):
        client = _make_client()
        client._token        = "old_tok"
        client._token_expiry = time.time() - 1   # already expired
        with patch.object(client, "authenticate") as mock_auth:
            client._ensure_auth()
            mock_auth.assert_called_once()


# ── _request (public endpoints) ───────────────────────────────────────────────

class TestRequest:
    def test_success_returns_result(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": {"key": "value"}}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = client._request("public/get_index", {"currency": "ETH"})

        assert result == {"key": "value"}

    def test_api_error_raises_deribit_error(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": 13009, "message": "Wrong API key"}}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            with pytest.raises(DeribitError, match="13009"):
                client._request("public/auth", {})

    def test_http_error_propagates(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("no json")
        mock_resp.raise_for_status.side_effect = requests.HTTPError("503 Server Error")

        with patch("requests.get", return_value=mock_resp):
            with pytest.raises(requests.HTTPError):
                client._request("public/auth", {})


# ── place_order ───────────────────────────────────────────────────────────────

def _raw_order(state="open"):
    return {
        "order": {
            "order_id":       "ETH-123456",
            "instrument_name": "ETH-30MAY25-2000-P",
            "direction":      "sell",
            "amount":         1.0,
            "price":          0.05,
            "order_state":    state,
            "filled_amount":  0.0,
            "average_price":  None,
            "label":          "test",
        }
    }


class TestPlaceOrder:
    def test_sell_limit_success(self):
        client = _make_client()
        with patch.object(client, "_ensure_auth"), \
             patch.object(client, "_private_request", return_value=_raw_order()):
            result = client.place_order("ETH-30MAY25-2000-P", "sell", 1, "limit", 0.05)

        assert isinstance(result, OrderResult)
        assert result.order_id   == "ETH-123456"
        assert result.state      == "open"
        assert result.direction  == "sell"
        assert result.amount     == 1.0

    def test_limit_without_price_raises(self):
        client = _make_client()
        with pytest.raises(ValueError, match="price is required"):
            client.place_order("ETH-30MAY25-2000-P", "sell", 1, "limit")

    def test_invalid_direction_raises(self):
        client = _make_client()
        with pytest.raises(ValueError, match="direction must be"):
            client.place_order("ETH-30MAY25-2000-P", "short", 1, "market")

    def test_market_order_no_price_needed(self):
        client = _make_client()
        raw = _raw_order()
        raw["order"]["order_state"] = "filled"
        with patch.object(client, "_ensure_auth"), \
             patch.object(client, "_private_request", return_value=raw):
            result = client.place_order("ETH-30MAY25-2000-P", "sell", 1, "market")
        assert result.state == "filled"

    def test_label_passed_through(self):
        client = _make_client()
        with patch.object(client, "_ensure_auth"), \
             patch.object(client, "_private_request", return_value=_raw_order()) as mock_post:
            client.place_order("ETH-30MAY25-2000-P", "sell", 1, "limit", 0.05, label="wheel_csp")

        call_params = mock_post.call_args[0][1]
        assert call_params["label"] == "wheel_csp"


# ── cancel_order ──────────────────────────────────────────────────────────────

class TestCancelOrder:
    def test_cancel_returns_raw(self):
        client = _make_client()
        with patch.object(client, "_ensure_auth"), \
             patch.object(client, "_private_request", return_value={"order_state": "cancelled"}):
            result = client.cancel_order("ETH-123456")
        assert result["order_state"] == "cancelled"


# ── get_order_state ───────────────────────────────────────────────────────────

class TestGetOrderState:
    def test_returns_order_result(self):
        client = _make_client()
        raw = _raw_order("filled")["order"]
        with patch.object(client, "_private_get", return_value=raw):
            result = client.get_order_state("ETH-123456")
        assert result.state == "filled"


# ── get_open_orders ───────────────────────────────────────────────────────────

class TestGetOpenOrders:
    def test_no_orders_across_all_currencies(self):
        client = _make_client()
        with patch.object(client, "_private_get", return_value=[]):
            results = client.get_open_orders()
        assert results == []

    def test_merges_orders_across_currencies(self):
        client = _make_client()
        one_order = [_raw_order()["order"]]
        # Return one order for BTC, none for the rest
        def side_effect(method, params):
            return one_order if params.get("currency") == "BTC" else []
        with patch.object(client, "_private_get", side_effect=side_effect):
            results = client.get_open_orders()
        assert len(results) == 1
        assert isinstance(results[0], OrderResult)

    def test_dict_response_with_orders_key(self):
        # Deribit sometimes wraps the list under "orders" — each currency call unwraps it.
        client = _make_client()
        def side_effect(method, params):
            return {"orders": [_raw_order()["order"]]} if params.get("currency") == "BTC" else []
        with patch.object(client, "_private_get", side_effect=side_effect):
            results = client.get_open_orders()
        assert len(results) == 1

    def test_filters_by_instrument(self):
        client = _make_client()
        with patch.object(client, "_private_get", return_value=[]) as mock_get:
            client.get_open_orders("ETH-30MAY25-2000-P")
        call_args = mock_get.call_args
        assert "ETH-30MAY25-2000-P" in str(call_args)


# ── get_position ──────────────────────────────────────────────────────────────

class TestGetPosition:
    def test_returns_position(self):
        client = _make_client()
        pos = {"instrument_name": "ETH-30MAY25-2000-P", "size": -1.0}
        with patch.object(client, "_private_get", return_value=pos):
            result = client.get_position("ETH-30MAY25-2000-P")
        assert result["size"] == -1.0

    def test_not_found_returns_empty_dict(self):
        client = _make_client()
        with patch.object(client, "_private_get", side_effect=DeribitError("Position not found")):
            result = client.get_position("ETH-30MAY25-2000-P")
        assert result == {}

    def test_other_deribit_error_propagates(self):
        client = _make_client()
        with patch.object(client, "_private_get", side_effect=DeribitError("Something else")):
            with pytest.raises(DeribitError):
                client.get_position("ETH-30MAY25-2000-P")


# ── tick helpers ─────────────────────────────────────────────────────────────

class TestTickHelpers:
    def test_effective_tick_base(self):
        # Price below the step threshold → base tick
        info = {"tick_size": 0.0001, "tick_size_steps": [{"above_price": 0.005, "tick_size": 0.0005}]}
        assert DeribitClient._effective_tick(info, 0.003) == 0.0001

    def test_effective_tick_stepped(self):
        # Price above the step threshold → larger tick
        info = {"tick_size": 0.0001, "tick_size_steps": [{"above_price": 0.005, "tick_size": 0.0005}]}
        assert DeribitClient._effective_tick(info, 0.0226) == 0.0005

    def test_effective_tick_no_steps(self):
        info = {"tick_size": 0.0001}
        assert DeribitClient._effective_tick(info, 0.05) == 0.0001

    def test_snap_to_tick_rounds_down(self):
        # 0.0226 → nearest 0.0005 multiple = 0.0225
        result = DeribitClient._snap_to_tick(0.0226, 0.0005)
        assert abs(result - 0.0225) < 1e-9

    def test_snap_to_tick_rounds_up(self):
        # 0.0228 → nearest 0.0005 multiple = 0.0230
        result = DeribitClient._snap_to_tick(0.0228, 0.0005)
        assert abs(result - 0.0230) < 1e-9

    def test_snap_to_tick_already_valid(self):
        result = DeribitClient._snap_to_tick(0.0225, 0.0005)
        assert abs(result - 0.0225) < 1e-9

    def test_place_order_snaps_price(self):
        # Full integration: place_order fetches instrument info and snaps price
        client = _make_client()
        inst_info = {
            "tick_size": 0.0001,
            "tick_size_steps": [{"above_price": 0.005, "tick_size": 0.0005}],
        }
        with patch.object(client, "_ensure_auth"), \
             patch.object(client, "_request", return_value=inst_info), \
             patch.object(client, "_private_request", return_value=_raw_order()) as mock_priv:
            client.place_order("ETH-22MAY26-2300-C", "sell", 250, "limit", 0.0226)
        sent_price = mock_priv.call_args[0][1]["price"]
        assert abs(sent_price - 0.0225) < 1e-9, f"expected 0.0225, got {sent_price}"

    def test_place_order_raises_price_to_min_price(self):
        # If BS price is below min_price, order price should be raised to min_price.
        client = _make_client()
        inst_info = {"tick_size": 0.01, "min_price": 5.0, "min_trade_amount": 1}
        with patch.object(client, "_ensure_auth"), \
             patch.object(client, "_request", return_value=inst_info), \
             patch.object(client, "_private_request", return_value=_raw_order()) as mock_priv:
            client.place_order("SOL_USDC-22MAY26-96-C", "sell", 2, "limit", 2.9)
        sent_price = mock_priv.call_args[0][1]["price"]
        assert abs(sent_price - 5.0) < 1e-9, f"expected min_price 5.0, got {sent_price}"

    def test_place_order_raises_amount_to_min_trade_amount(self):
        # If amount is below min_trade_amount, it should be raised to the minimum.
        client = _make_client()
        inst_info = {"tick_size": 0.01, "min_price": 0.0, "min_trade_amount": 10}
        with patch.object(client, "_ensure_auth"), \
             patch.object(client, "_request", return_value=inst_info), \
             patch.object(client, "_private_request", return_value=_raw_order()) as mock_priv:
            client.place_order("SOL_USDC-22MAY26-96-C", "sell", 2, "limit", 3.0)
        sent_amount = mock_priv.call_args[0][1]["amount"]
        assert sent_amount == 10, f"expected min_trade_amount 10, got {sent_amount}"

    def test_place_order_get_instrument_failure_uses_fallback(self):
        # If get_instrument fails, falls back to tick=0.0001 and no min constraints.
        client = _make_client()
        with patch.object(client, "_ensure_auth"), \
             patch.object(client, "_request", side_effect=Exception("network error")), \
             patch.object(client, "_private_request", return_value=_raw_order()) as mock_priv:
            client.place_order("SOL_USDC-22MAY26-96-C", "sell", 2, "limit", 2.9)
        sent_price = mock_priv.call_args[0][1]["price"]
        assert abs(sent_price - 2.9) < 1e-9

    def test_deribit_error_includes_data_field(self):
        # DeribitError message should include error.data when present.
        client = _make_client()
        error_body = {
            "error": {"code": -32602, "message": "Invalid params", "data": {"reason": "price_out_of_range"}}
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = error_body
        mock_resp.ok = True
        with patch.object(client, "_ensure_auth"), \
             patch("requests.get", return_value=mock_resp):
            with pytest.raises(DeribitError, match="price_out_of_range"):
                client.place_order("SOL_USDC-22MAY26-96-C", "sell", 2, "limit", 2.9)


# ── find_instrument ───────────────────────────────────────────────────────────

def _inst(name: str) -> dict:
    return {"instrument_name": name}


class TestFindInstrument:
    def test_snaps_to_nearest_strike_same_expiry(self):
        client = _make_client()
        instruments = [_inst(n) for n in [
            "ETH-22MAY26-2200-C", "ETH-22MAY26-2300-C", "ETH-22MAY26-2400-C",
        ]]
        with patch.object(client, "_request", return_value=instruments):
            result = client.find_instrument("ETH", date(2026, 5, 22), 2280.0, "call")
        assert result == "ETH-22MAY26-2300-C"

    def test_exact_match_unchanged(self):
        client = _make_client()
        instruments = [_inst("ETH-22MAY26-2300-C")]
        with patch.object(client, "_request", return_value=instruments):
            result = client.find_instrument("ETH", date(2026, 5, 22), 2300.0, "call")
        assert result == "ETH-22MAY26-2300-C"

    def test_snaps_to_nearest_expiry_when_target_missing(self):
        # Target expiry 22MAY26 not listed; 08MAY26 is 14 days away, 29MAY26 is 7 days away.
        client = _make_client()
        instruments = [_inst(n) for n in [
            "ETH-08MAY26-2300-C", "ETH-29MAY26-2300-C",
        ]]
        with patch.object(client, "_request", return_value=instruments):
            result = client.find_instrument("ETH", date(2026, 5, 22), 2300.0, "call")
        assert result == "ETH-29MAY26-2300-C"

    def test_filters_correct_option_type(self):
        client = _make_client()
        # Only puts listed — requesting a call should fall back to computed name.
        instruments = [_inst("ETH-22MAY26-2300-P")]
        with patch.object(client, "_request", return_value=instruments):
            result = client.find_instrument("ETH", date(2026, 5, 22), 2300.0, "call")
        assert result == "ETH-22MAY26-2300-C"   # fallback

    def test_network_failure_returns_computed(self):
        client = _make_client()
        with patch.object(client, "_request", side_effect=Exception("timeout")):
            result = client.find_instrument("ETH", date(2026, 5, 22), 2300.0, "call")
        assert result == "ETH-22MAY26-2300-C"

    def test_linear_asset_uses_usdc_currency(self):
        client = _make_client()
        instruments = [_inst("SOL_USDC-22MAY26-150-P")]
        with patch.object(client, "_request", return_value=instruments) as mock_req:
            client.find_instrument("SOL", date(2026, 5, 22), 150.0, "put")
        assert mock_req.call_args[0][1]["currency"] == "USDC"

    def test_expired_param_is_lowercase_string(self):
        # requests serialises Python False as "False"; Deribit requires "false".
        client = _make_client()
        with patch.object(client, "_request", return_value=[]) as mock_req:
            client.find_instrument("ETH", date(2026, 5, 22), 2300.0, "call")
        expired_param = mock_req.call_args[0][1]["expired"]
        assert expired_param == "false", f"expected 'false', got {expired_param!r}"
