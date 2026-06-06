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
    RollOptionDetail,
    _fetch_far_leg_data,
    _interpret_iv_level,
    _days_remaining,
    _next_friday,
    calculate_roll_options,
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


class TestCalculateRollOptions:
    """Test calculate_roll_options for roll details with PoP and expected profit."""

    def test_call_roll_options(self):
        """Test roll option calculation for call options."""
        roll_opts = calculate_roll_options(
            strike=2000.0,
            option_type="Call",
            spot=2000.0,
            iv=0.80,
            qty=0.125,
            current_far_pnl=10.0,
            expiry_far_days=30,
        )

        assert len(roll_opts) == 3  # 1d, 3d, 7d
        assert all(isinstance(opt, RollOptionDetail) for opt in roll_opts)

        # Check 1d option
        assert roll_opts[0].days == 1
        assert roll_opts[0].strike == 2000.0
        assert roll_opts[0].option_type == "Call"
        assert roll_opts[0].estimated_premium > 0.0
        assert 0.0 <= roll_opts[0].probability_profit <= 1.0
        assert roll_opts[0].expected_pnl == 10.0 + roll_opts[0].estimated_premium

    def test_put_roll_options(self):
        """Test roll option calculation for put options."""
        roll_opts = calculate_roll_options(
            strike=2000.0,
            option_type="Put",
            spot=2000.0,
            iv=0.75,
            qty=0.125,
            current_far_pnl=5.0,
            expiry_far_days=30,
        )

        assert len(roll_opts) == 3  # 1d, 3d, 7d
        assert all(opt.option_type == "Put" for opt in roll_opts)

    def test_roll_options_ordered_by_days(self):
        """Test roll options are ordered by days (1, 3, 7)."""
        roll_opts = calculate_roll_options(
            strike=2000.0,
            option_type="Call",
            spot=2000.0,
            iv=0.80,
            qty=0.125,
            current_far_pnl=10.0,
            expiry_far_days=30,
        )

        days_list = [opt.days for opt in roll_opts]
        assert days_list == [1, 3, 7]

    def test_roll_options_not_past_far_expiry(self):
        """Test roll options are not suggested if they exceed far leg expiry."""
        # Only 2 days left on far leg
        roll_opts = calculate_roll_options(
            strike=2000.0,
            option_type="Call",
            spot=2000.0,
            iv=0.80,
            qty=0.125,
            current_far_pnl=10.0,
            expiry_far_days=2,  # Only 2 days left
        )

        # Should only suggest 1d (not 3d or 7d)
        assert len(roll_opts) <= 1
        assert all(opt.days < 2 for opt in roll_opts)

    def test_premium_increases_with_days(self):
        """Test that premium generally increases with more days to expiry."""
        roll_opts = calculate_roll_options(
            strike=2000.0,
            option_type="Call",
            spot=2000.0,
            iv=0.80,
            qty=0.125,
            current_far_pnl=0.0,
            expiry_far_days=30,
        )

        # Premium should generally increase with more time
        premiums = [opt.estimated_premium for opt in roll_opts]
        assert premiums[0] <= premiums[1] <= premiums[2]

    def test_probability_higher_atm(self):
        """Test PoP is higher when spot is at strike (ATM)."""
        # ATM: spot == strike
        roll_opts_atm = calculate_roll_options(
            strike=2000.0,
            option_type="Call",
            spot=2000.0,
            iv=0.80,
            qty=0.125,
            current_far_pnl=0.0,
            expiry_far_days=30,
        )

        # OTM: spot much higher than strike
        roll_opts_otm = calculate_roll_options(
            strike=2000.0,
            option_type="Call",
            spot=2500.0,
            iv=0.80,
            qty=0.125,
            current_far_pnl=0.0,
            expiry_far_days=30,
        )

        # For calls, ATM should have higher PoP than OTM (spot high)
        # (Lower delta/probability of expiring worthless when spot is high)
        assert roll_opts_atm[0].probability_profit > roll_opts_otm[0].probability_profit

    def test_justification_based_on_pop(self):
        """Test justification text reflects PoP levels."""
        # High IV scenario - high PoP
        roll_opts = calculate_roll_options(
            strike=2000.0,
            option_type="Call",
            spot=1500.0,  # Well below strike, high PoP for expiring worthless
            iv=0.80,
            qty=0.125,
            current_far_pnl=0.0,
            expiry_far_days=30,
        )

        # With spot well below strike, PoP should be high
        for opt in roll_opts:
            if opt.probability_profit > 0.80:
                assert "High PoP" in opt.justification
            elif opt.probability_profit > 0.65:
                assert "Good PoP" in opt.justification
            else:
                assert "Lower PoP" in opt.justification

    def test_expected_pnl_calculation(self):
        """Test expected P&L is current + premium."""
        roll_opts = calculate_roll_options(
            strike=2000.0,
            option_type="Call",
            spot=2000.0,
            iv=0.80,
            qty=0.125,
            current_far_pnl=10.0,
            expiry_far_days=30,
        )

        # Expected P&L should be current + premium
        for opt in roll_opts:
            expected = 10.0 + opt.estimated_premium
            assert abs(opt.expected_pnl - expected) < 0.01  # Allow for rounding

    def test_roll_option_detail_data(self):
        """Test RollOptionDetail contains all required fields."""
        roll_opts = calculate_roll_options(
            strike=2000.0,
            option_type="Put",
            spot=2000.0,
            iv=0.75,
            qty=0.125,
            current_far_pnl=5.0,
            expiry_far_days=30,
        )

        opt = roll_opts[0]
        # All fields should be present and valid
        assert hasattr(opt, "days") and opt.days == 1
        assert hasattr(opt, "expiry_date") and isinstance(opt.expiry_date, date)
        assert hasattr(opt, "strike") and opt.strike == 2000.0
        assert hasattr(opt, "option_type") and opt.option_type == "Put"
        assert hasattr(opt, "estimated_premium") and isinstance(opt.estimated_premium, float)
        assert hasattr(opt, "probability_profit") and 0.0 <= opt.probability_profit <= 1.0
        assert hasattr(opt, "expected_pnl") and isinstance(opt.expected_pnl, float)
        assert hasattr(opt, "justification") and isinstance(opt.justification, str)
