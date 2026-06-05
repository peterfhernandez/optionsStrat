"""
tests/test_calendar_analysis.py
================================
Tests for the calendar_analysis module.
"""

import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from strategies.calendar_analysis import (
    FarLegData,
    FarLegAnalysis,
    _fetch_far_leg_data,
    _interpret_iv_level,
    _days_remaining,
    analyze_calendar_far_leg,
)


class TestDaysRemaining:
    """Test _days_remaining helper."""

    def test_future_date(self):
        """Days remaining should be positive for future dates."""
        future = (date.today() + timedelta(days=7)).strftime("%d-%b-%Y")
        assert _days_remaining(future) == 7

    def test_today(self):
        """Today should return 0."""
        today = date.today().strftime("%d-%b-%Y")
        assert _days_remaining(today) == 0

    def test_past_date(self):
        """Past dates should return 0 (clamped)."""
        past = (date.today() - timedelta(days=7)).strftime("%d-%b-%Y")
        assert _days_remaining(past) == 0

    def test_alternative_format(self):
        """Support YYYY-MM-DD format."""
        future = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
        assert _days_remaining(future) == 5

    def test_invalid_format(self):
        """Invalid format returns 0."""
        assert _days_remaining("invalid-date") == 0


class TestInterpretIVLevel:
    """Test IV level classification."""

    def test_very_low_iv(self):
        assert _interpret_iv_level(0.25) == "very_low"

    def test_low_iv(self):
        assert _interpret_iv_level(0.40) == "low"

    def test_normal_iv(self):
        assert _interpret_iv_level(0.60) == "normal"

    def test_high_iv(self):
        assert _interpret_iv_level(0.95) == "high"

    def test_very_high_iv(self):
        assert _interpret_iv_level(1.50) == "very_high"

    def test_boundary_values(self):
        assert _interpret_iv_level(0.30) == "low"  # At boundary
        assert _interpret_iv_level(0.50) == "normal"  # At boundary
        assert _interpret_iv_level(0.80) == "high"  # At boundary


class TestFetchFarLegData:
    """Test fetching data from Deribit API."""

    @patch("strategies.calendar_analysis.requests.get")
    def test_successful_fetch(self, mock_get):
        """Test successful API response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "instrument_name": "ETH-26JUN26-2100-C",
                "mark_price": 0.032,
                "mark_iv": 44.43,  # API returns as percentage
                "bid_iv": 43.93,   # API returns as percentage
                "ask_iv": 44.88,   # API returns as percentage
                "greeks": {
                    "delta": 0.39741,
                    "gamma": 0.00157,
                    "vega": 2.12852,
                    "theta": -1.73522,
                    "rho": 0.551,
                },
                "bids": [[0.0315, 841.0]],
                "asks": [[0.0325, 63.0]],
                "open_interest": 7496.0,
                "underlying_price": 2019.79,
            },
        }
        mock_get.return_value = mock_response

        data = _fetch_far_leg_data("ETH-26JUN26-2100-C", paper=True)

        assert data is not None
        assert data.instrument_name == "ETH-26JUN26-2100-C"
        assert data.mark_price == 0.032
        assert data.mark_iv == pytest.approx(0.4443, abs=0.001)  # Converted to decimal
        assert data.delta == 0.39741
        assert data.theta == -1.73522
        assert data.bid_price == 0.0315
        assert data.ask_price == 0.0325

    @patch("strategies.calendar_analysis.requests.get")
    def test_api_error(self, mock_get):
        """Test API error response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "error": {"code": -32602, "message": "Invalid params"}
        }
        mock_get.return_value = mock_response

        data = _fetch_far_leg_data("INVALID-INSTRUMENT", paper=True)
        assert data is None

    @patch("strategies.calendar_analysis.requests.get")
    def test_network_error(self, mock_get):
        """Test network error handling."""
        mock_get.side_effect = Exception("Connection timeout")

        data = _fetch_far_leg_data("ETH-26JUN26-2100-C", paper=True)
        assert data is None

    @patch("strategies.calendar_analysis.requests.get")
    def test_missing_greeks(self, mock_get):
        """Test response without greeks (graceful degradation)."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "instrument_name": "ETH-26JUN26-2100-C",
                "mark_price": 0.032,
                "mark_iv": 0.4443,
                # no greeks field
                "bids": [[0.0315, 841.0]],
                "asks": [[0.0325, 63.0]],
            }
        }
        mock_get.return_value = mock_response

        data = _fetch_far_leg_data("ETH-26JUN26-2100-C", paper=True)

        assert data is not None
        assert data.delta == 0.0  # Default


class TestAnalyzeCalendarFarLeg:
    """Test the main analysis function."""

    @patch("strategies.calendar_analysis._fetch_far_leg_data")
    def test_analyze_call_option(self, mock_fetch):
        """Test analysis of a call option far leg."""
        far_leg_data = FarLegData(
            instrument_name="ETH-26JUN26-2100-C",
            mark_price=0.032,
            mark_iv=0.4443,  # Stored as decimal internally
            bid_iv=0.4393,   # Stored as decimal internally
            ask_iv=0.4488,   # Stored as decimal internally
            delta=0.39741,
            gamma=0.00157,
            vega=2.12852,
            theta=-1.73522,
            rho=0.551,
            bid_price=0.0315,
            ask_price=0.0325,
            open_interest=7496.0,
            underlying_price=2019.79,
        )
        mock_fetch.return_value = far_leg_data

        future_date = (date.today() + timedelta(days=27)).strftime("%d-%b-%Y")
        analysis = analyze_calendar_far_leg(
            asset="ETH",
            strike=2100.0,
            option_type="Call",
            expiry_far=future_date,
            qty=0.1,
            net_debit=6.61,
            paper=True,
        )

        assert analysis is not None
        assert analysis.data == far_leg_data
        assert analysis.iv_level == "low"  # 0.4443 is below 0.50 threshold
        assert analysis.current_pnl == pytest.approx(0.032 * 0.1 - 6.61, abs=0.01)
        assert "delta" in analysis.delta_comment.lower()

    @patch("strategies.calendar_analysis._fetch_far_leg_data")
    def test_analyze_put_option(self, mock_fetch):
        """Test analysis of a put option far leg."""
        far_leg_data = FarLegData(
            instrument_name="ETH-26JUN26-2100-P",
            mark_price=0.045,
            mark_iv=0.42,
            bid_iv=0.40,
            ask_iv=0.44,
            delta=-0.35,
            gamma=0.00160,
            vega=2.30,
            theta=-1.80,
            rho=-0.56,
            bid_price=0.043,
            ask_price=0.047,
            open_interest=5000.0,
            underlying_price=2019.79,
        )
        mock_fetch.return_value = far_leg_data

        future_date = (date.today() + timedelta(days=27)).strftime("%d-%b-%Y")
        analysis = analyze_calendar_far_leg(
            asset="ETH",
            strike=2100.0,
            option_type="Put",
            expiry_far=future_date,
            qty=0.1,
            net_debit=8.0,
            paper=True,
        )

        assert analysis is not None
        assert analysis.iv_level == "low"  # 0.42 is below 0.50 threshold
        assert "delta" in analysis.delta_comment.lower()

    @patch("strategies.calendar_analysis._fetch_far_leg_data")
    def test_fetch_failure(self, mock_fetch):
        """Test graceful handling of fetch failure."""
        mock_fetch.return_value = None

        future_date = (date.today() + timedelta(days=27)).strftime("%d-%b-%Y")
        analysis = analyze_calendar_far_leg(
            asset="ETH",
            strike=2100.0,
            option_type="Call",
            expiry_far=future_date,
            qty=0.1,
            net_debit=6.61,
            paper=True,
        )

        assert analysis is None

    @patch("strategies.calendar_analysis._fetch_far_leg_data")
    def test_invalid_expiry_date(self, mock_fetch):
        """Test handling of invalid expiry date format."""
        analysis = analyze_calendar_far_leg(
            asset="ETH",
            strike=2100.0,
            option_type="Call",
            expiry_far="invalid-date",
            qty=0.1,
            net_debit=6.61,
            paper=True,
        )

        assert analysis is None
        mock_fetch.assert_not_called()


class TestRecommendations:
    """Test recommendation generation logic."""

    @patch("strategies.calendar_analysis._fetch_far_leg_data")
    def test_very_low_iv_recommendation(self, mock_fetch):
        """Test recommendation for very low IV."""
        far_leg_data = FarLegData(
            instrument_name="ETH-26JUN26-2100-C",
            mark_price=0.080,
            mark_iv=0.25,
            bid_iv=0.23,
            ask_iv=0.27,
            delta=0.45,
            gamma=0.001,
            vega=1.5,
            theta=-0.5,
            rho=0.3,
            bid_price=0.078,
            ask_price=0.082,
            open_interest=3000.0,
            underlying_price=2020.0,
        )
        mock_fetch.return_value = far_leg_data

        future_date = (date.today() + timedelta(days=27)).strftime("%d-%b-%Y")
        analysis = analyze_calendar_far_leg(
            asset="ETH",
            strike=2100.0,
            option_type="Call",
            expiry_far=future_date,
            qty=0.1,
            net_debit=6.61,
            paper=True,
        )

        assert "close" in analysis.recommendation.lower()
        assert "very low" in analysis.recommendation.lower()

    @patch("strategies.calendar_analysis._fetch_far_leg_data")
    def test_very_high_iv_recommendation(self, mock_fetch):
        """Test recommendation for very high IV."""
        far_leg_data = FarLegData(
            instrument_name="ETH-26JUN26-2100-C",
            mark_price=0.15,
            mark_iv=1.50,
            bid_iv=1.45,
            ask_iv=1.55,
            delta=0.50,
            gamma=0.001,
            vega=3.5,
            theta=-1.5,
            rho=0.5,
            bid_price=0.14,
            ask_price=0.16,
            open_interest=8000.0,
            underlying_price=2020.0,
        )
        mock_fetch.return_value = far_leg_data

        future_date = (date.today() + timedelta(days=27)).strftime("%d-%b-%Y")
        analysis = analyze_calendar_far_leg(
            asset="ETH",
            strike=2100.0,
            option_type="Call",
            expiry_far=future_date,
            qty=0.1,
            net_debit=6.61,
            paper=True,
        )

        assert "hold" in analysis.recommendation.lower() or "roll" in analysis.recommendation.lower()
        assert "very high" in analysis.recommendation.lower() or "elevated" in analysis.recommendation.lower()


class TestSuggestedRolls:
    """Test suggested roll options."""

    @patch("strategies.calendar_analysis._fetch_far_leg_data")
    def test_roll_options_generated(self, mock_fetch):
        """Test that roll options are suggested when appropriate."""
        far_leg_data = FarLegData(
            instrument_name="ETH-26JUN26-2100-C",
            mark_price=0.032,
            mark_iv=0.4443,
            bid_iv=0.4393,
            ask_iv=0.4488,
            delta=0.39741,
            gamma=0.00157,
            vega=2.12852,
            theta=-1.73522,
            rho=0.551,
            bid_price=0.0315,
            ask_price=0.0325,
            open_interest=7496.0,
            underlying_price=2019.79,
        )
        mock_fetch.return_value = far_leg_data

        future_date = (date.today() + timedelta(days=27)).strftime("%d-%b-%Y")
        analysis = analyze_calendar_far_leg(
            asset="ETH",
            strike=2100.0,
            option_type="Call",
            expiry_far=future_date,
            qty=0.1,
            net_debit=6.61,
            paper=True,
        )

        # Should suggest 1d, 3d, and 7d rolls
        assert len(analysis.suggested_rolls) >= 2
        assert any("1d" in desc for _, desc in analysis.suggested_rolls)

    @patch("strategies.calendar_analysis._fetch_far_leg_data")
    def test_no_rolls_when_expiry_too_close(self, mock_fetch):
        """Test no rolls suggested when far leg expires soon."""
        far_leg_data = FarLegData(
            instrument_name="ETH-05JUN26-2100-C",
            mark_price=0.032,
            mark_iv=0.4443,
            bid_iv=0.4393,
            ask_iv=0.4488,
            delta=0.39741,
            gamma=0.00157,
            vega=2.12852,
            theta=-1.73522,
            rho=0.551,
            bid_price=0.0315,
            ask_price=0.0325,
            open_interest=7496.0,
            underlying_price=2019.79,
        )
        mock_fetch.return_value = far_leg_data

        # Far expiry is 6 days away
        future_date = (date.today() + timedelta(days=6)).strftime("%d-%b-%Y")
        analysis = analyze_calendar_far_leg(
            asset="ETH",
            strike=2100.0,
            option_type="Call",
            expiry_far=future_date,
            qty=0.1,
            net_debit=6.61,
            paper=True,
        )

        # With 6 days left, only 1d and 3d rolls are valid (7d roll would exceed far expiry)
        assert len(analysis.suggested_rolls) == 2
