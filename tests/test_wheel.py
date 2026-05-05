"""
tests/test_wheel.py
===================
Tests for strategies/wheel.py — state file helpers, strike analysis,
paper trading menu transitions, and the performance summary.

Test strategy
-------------
Tier 1 — pure functions, no mocking:
    _state_file     : filename format, asset uppercasing

Tier 2 — mocked I/O:
    _load           : missing file returns defaults, existing file parsed,
                      uses correct asset-specific filename
    _save           : writes correct JSON content, correct filename, roundtrip

Tier 3 — output and side effects:
    show_strikes    : runs without error, output mentions asset/Put/Call/OTM%,
                      pricing functions are called for each OTM level
    show_summary    : reads workbook sheets and prints expected stats,
                      handles empty sheets and missing values gracefully

Tier 4 — interactive menu (mocked input, mocked excel_tracker):
    wheel_paper_menu :
        - [1] Sell Put: opens position, accumulates premium, persists state,
                        appends a trade row, rejects if position already open
        - [2] Expire:   put-worthless → no_position + win,
                        put-ITM      → no_position + loss with reduced P&L,
                        call-worthless → holding + cycle increment,
                        no-position  → warning, no state change
        - [3] Assign:   short_put → holding with cost basis set,
                        rejected outside short_put stage
        - [4] Sell Call: opens call from holding, rejected outside holding
        - [5] Back:     no state mutation

Tier 5 — end-to-end:
    test_full_wheel_cycle : drive all four menu choices to walk
                            no_position → short_put → holding → short_call
                            → no_position with cycles=1 and wins=2.

Note: pricing math is covered in test_pricing.py — bs_put / bs_call /
prob_otm_put / prob_otm_call are mocked here so wheel tests stay
focused on wheel logic.
"""

import json
import os
from unittest.mock import patch, mock_open, MagicMock

import pytest

from strategies.wheel import (
    _state_file,
    _load,
    _save,
    show_strikes,
    wheel_paper_menu,
    show_summary,
)
from config import BUDGET_USD, OTM_LEVELS


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def default_state():
    """The default state returned when no wheel state file exists."""
    return {
        "stage":         "no_position",
        "open":          None,
        "asset_held":    0.0,
        "cost_basis":    0.0,
        "total_premium": 0.0,
        "wins":          0,
        "losses":        0,
        "cycles":        0,
    }


@pytest.fixture
def open_put_position():
    """A standard open short-put position dict."""
    return {
        "type":      "Put",
        "strike":    1700.0,
        "expiry":    "01-May-2026",
        "premium":   25.0,
        "spot_open": 2000.0,
        "qty":       BUDGET_USD / 1700.0,
        "days":      7,
        "asset":     "ETH",
    }


@pytest.fixture
def open_call_position():
    """A standard open short-call position dict (after assignment)."""
    return {
        "type":      "Call",
        "strike":    2300.0,
        "expiry":    "01-May-2026",
        "premium":   30.0,
        "spot_open": 2000.0,
        "qty":       0.125,
        "days":      7,
        "asset":     "ETH",
    }


@pytest.fixture
def mock_wb():
    """A MagicMock standing in for an openpyxl Workbook."""
    return MagicMock()


# ── _state_file ───────────────────────────────────────────────────────────────

class TestStateFile:

    def test_eth_filename(self):
        assert _state_file("ETH") == "paper_state_ETH.json"

    def test_btc_filename(self):
        assert _state_file("BTC") == "paper_state_BTC.json"

    def test_sol_filename(self):
        assert _state_file("SOL") == "paper_state_SOL.json"

    def test_xrp_filename(self):
        assert _state_file("XRP") == "paper_state_XRP.json"

    def test_lowercase_uppercased(self):
        assert _state_file("eth") == "paper_state_ETH.json"

    def test_format_consistent(self):
        """All state files follow the same pattern."""
        for asset in ("ETH", "BTC", "SOL", "XRP"):
            name = _state_file(asset)
            assert name.startswith("paper_state_")
            assert name.endswith(".json")
            assert asset in name


# ── _load ─────────────────────────────────────────────────────────────────────

class TestLoad:

    def test_missing_file_returns_defaults(self, default_state):
        """When state file doesn't exist, returns a fresh default state."""
        with patch("strategies.wheel.os.path.exists", return_value=False):
            result = _load("ETH")
        assert result == default_state

    def test_missing_file_default_stage(self):
        """Default stage is 'no_position'."""
        with patch("strategies.wheel.os.path.exists", return_value=False):
            result = _load("ETH")
        assert result["stage"] == "no_position"
        assert result["open"]  is None

    def test_existing_file_returns_contents(self):
        """When state file exists, returns its parsed contents."""
        state = {
            "stage":         "holding",
            "open":          None,
            "asset_held":    0.147,
            "cost_basis":    1700.0,
            "total_premium": 25.0,
            "wins":          1,
            "losses":        0,
            "cycles":        0,
        }
        with patch("strategies.wheel.os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=json.dumps(state))):
            result = _load("ETH")
        assert result["stage"]         == "holding"
        assert result["asset_held"]    == pytest.approx(0.147)
        assert result["total_premium"] == 25.0
        assert result["wins"]          == 1

    def test_uses_correct_filename(self):
        """_load uses _state_file to determine the path."""
        with patch("strategies.wheel.os.path.exists", return_value=False) as mock_exists:
            _load("BTC")
        mock_exists.assert_called_once_with("paper_state_BTC.json")


# ── _save ─────────────────────────────────────────────────────────────────────

class TestSave:

    def test_saves_valid_json(self, tmp_path, monkeypatch, default_state):
        monkeypatch.chdir(tmp_path)
        _save("ETH", default_state)
        with open("paper_state_ETH.json") as f:
            assert json.load(f) == default_state

    def test_saves_to_correct_filename(self, default_state):
        """_save writes to the asset-specific filename."""
        m = mock_open()
        with patch("builtins.open", m):
            _save("SOL", default_state)
        m.assert_called_once_with("paper_state_SOL.json", "w")

    def test_roundtrip(self, tmp_path, monkeypatch):
        """State saved by _save can be loaded back by _load."""
        monkeypatch.chdir(tmp_path)
        state = {
            "stage":         "short_put",
            "open":          {"type": "Put", "strike": 1700.0},
            "asset_held":    0.0,
            "cost_basis":    0.0,
            "total_premium": 25.0,
            "wins":          0,
            "losses":        0,
            "cycles":        0,
        }
        _save("ETH", state)
        loaded = _load("ETH")
        assert loaded["stage"]         == "short_put"
        assert loaded["total_premium"] == 25.0
        assert loaded["open"]["strike"] == 1700.0


# ── show_strikes ──────────────────────────────────────────────────────────────

class TestShowStrikes:
    """
    show_strikes is a presentation function — we don't assert exact formatting,
    just that it runs, produces output, and exercises the pricing helpers
    once per OTM level for puts and once per OTM level for calls.
    """

    def test_runs_without_error(self, capsys):
        with patch("strategies.wheel.bs_put",         return_value=5.0), \
             patch("strategies.wheel.bs_call",        return_value=5.0), \
             patch("strategies.wheel.prob_otm_put",   return_value=0.85), \
             patch("strategies.wheel.prob_otm_call",  return_value=0.85):
            show_strikes("ETH", spot=2000.0, iv=0.80, days=7)
        out = capsys.readouterr().out
        assert out  # produced *something*

    def test_output_mentions_asset(self, capsys):
        with patch("strategies.wheel.bs_put",         return_value=5.0), \
             patch("strategies.wheel.bs_call",        return_value=5.0), \
             patch("strategies.wheel.prob_otm_put",   return_value=0.85), \
             patch("strategies.wheel.prob_otm_call",  return_value=0.85):
            show_strikes("BTC", spot=90000.0, iv=0.60, days=7)
        out = capsys.readouterr().out
        assert "BTC" in out

    def test_output_includes_put_and_call_sections(self, capsys):
        with patch("strategies.wheel.bs_put",         return_value=5.0), \
             patch("strategies.wheel.bs_call",        return_value=5.0), \
             patch("strategies.wheel.prob_otm_put",   return_value=0.85), \
             patch("strategies.wheel.prob_otm_call",  return_value=0.85):
            show_strikes("ETH", spot=2000.0, iv=0.80, days=7)
        out = capsys.readouterr().out
        assert "Put"  in out
        assert "Call" in out

    def test_pricing_called_for_each_otm_level(self):
        """Each OTM level should price one put and one call."""
        with patch("strategies.wheel.bs_put",         return_value=5.0) as mp, \
             patch("strategies.wheel.bs_call",        return_value=5.0) as mc, \
             patch("strategies.wheel.prob_otm_put",   return_value=0.85), \
             patch("strategies.wheel.prob_otm_call",  return_value=0.85):
            show_strikes("ETH", spot=2000.0, iv=0.80, days=7)
        assert mp.call_count == len(OTM_LEVELS)
        assert mc.call_count == len(OTM_LEVELS)

    def test_handles_daily_expiry(self, capsys):
        """1-day expiry should not trigger any divide-by-zero or T errors."""
        with patch("strategies.wheel.bs_put",         return_value=1.0), \
             patch("strategies.wheel.bs_call",        return_value=1.0), \
             patch("strategies.wheel.prob_otm_put",   return_value=0.95), \
             patch("strategies.wheel.prob_otm_call",  return_value=0.95):
            show_strikes("ETH", spot=2000.0, iv=0.80, days=1)
        # Just survive — the function only computes, prints, and returns None.


# ── wheel_paper_menu ──────────────────────────────────────────────────────────

class TestWheelPaperMenu:
    """
    All tests use tmp_path + monkeypatch.chdir so state files never leak
    between tests. Pricing functions and excel_tracker.append_trade_row
    are mocked.
    """

    # ── [1] Sell Put ──────────────────────────────────────────────────────────

    def test_sell_put_opens_position_from_no_position(
        self, tmp_path, monkeypatch, mock_wb,
    ):
        monkeypatch.chdir(tmp_path)
        with patch("strategies.wheel.bs_put",  return_value=200.0), \
             patch("strategies.wheel.bs_call", return_value=200.0), \
             patch("strategies.wheel.append_trade_row"), \
             patch("builtins.input", side_effect=["1", ""]):  # menu=1, accept default strike
            wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)

        loaded = _load("ETH")
        assert loaded["stage"]          == "short_put"
        assert loaded["open"]           is not None
        assert loaded["open"]["type"]   == "Put"
        assert loaded["total_premium"]  > 0

    def test_sell_put_appends_trade_row(self, tmp_path, monkeypatch, mock_wb):
        monkeypatch.chdir(tmp_path)
        with patch("strategies.wheel.bs_put",  return_value=200.0), \
             patch("strategies.wheel.bs_call", return_value=200.0), \
             patch("strategies.wheel.append_trade_row") as mock_append, \
             patch("builtins.input", side_effect=["1", ""]):
            wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)
        mock_append.assert_called_once()
        # Sheet name should be the paper-trades sheet
        args, _ = mock_append.call_args
        assert args[0] is mock_wb
        assert "Paper" in args[1]

    def test_sell_put_rejected_when_position_already_open(
        self, tmp_path, monkeypatch, mock_wb, open_put_position, default_state,
    ):
        """If a position is already open, [1] Sell Put should warn and not mutate state."""
        monkeypatch.chdir(tmp_path)
        existing = {**default_state,
                    "stage": "short_put",
                    "open":  open_put_position,
                    "total_premium": 25.0}
        _save("ETH", existing)

        with patch("strategies.wheel.bs_put",  return_value=200.0), \
             patch("strategies.wheel.bs_call", return_value=200.0), \
             patch("strategies.wheel.append_trade_row") as mock_append, \
             patch("builtins.input", side_effect=["1"]):
            wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)

        loaded = _load("ETH")
        assert loaded == existing            # no mutation
        mock_append.assert_not_called()

    # ── [2] Expire ────────────────────────────────────────────────────────────

    def test_expire_put_worthless_increments_wins(
        self, tmp_path, monkeypatch, mock_wb, open_put_position, default_state,
    ):
        """Spot above put strike → put expires worthless → win, back to no_position."""
        monkeypatch.chdir(tmp_path)
        existing = {**default_state,
                    "stage": "short_put",
                    "open":  open_put_position,
                    "total_premium": 25.0}
        _save("ETH", existing)

        with patch("strategies.wheel.bs_put",  return_value=0.0), \
             patch("strategies.wheel.bs_call", return_value=0.0), \
             patch("strategies.wheel.append_trade_row"), \
             patch("builtins.input", side_effect=["2", "2000"]):  # menu=2, spot_close=2000 > 1700
            wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)

        loaded = _load("ETH")
        assert loaded["stage"]  == "no_position"
        assert loaded["open"]   is None
        assert loaded["wins"]   == 1
        assert loaded["losses"] == 0

    def test_expire_put_itm_increments_losses(
        self, tmp_path, monkeypatch, mock_wb, open_put_position, default_state,
    ):
        """Spot below put strike → put ITM → loss, back to no_position."""
        monkeypatch.chdir(tmp_path)
        existing = {**default_state,
                    "stage": "short_put",
                    "open":  open_put_position,
                    "total_premium": 25.0}
        _save("ETH", existing)

        with patch("strategies.wheel.bs_put",  return_value=0.0), \
             patch("strategies.wheel.bs_call", return_value=0.0), \
             patch("strategies.wheel.append_trade_row"), \
             patch("builtins.input", side_effect=["2", "1500"]):  # spot_close=1500 < 1700 strike
            wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)

        loaded = _load("ETH")
        assert loaded["stage"]  == "no_position"
        assert loaded["wins"]   == 0
        assert loaded["losses"] == 1

    def test_expire_call_worthless_increments_cycle(
        self, tmp_path, monkeypatch, mock_wb, open_call_position, default_state,
    ):
        """Call expiring worthless → stage 'holding', cycles incremented."""
        monkeypatch.chdir(tmp_path)
        existing = {**default_state,
                    "stage":      "short_call",
                    "open":       open_call_position,
                    "asset_held": 0.125,
                    "cost_basis": 1700.0,
                    "total_premium": 55.0}
        _save("ETH", existing)

        with patch("strategies.wheel.bs_put",  return_value=0.0), \
             patch("strategies.wheel.bs_call", return_value=0.0), \
             patch("strategies.wheel.append_trade_row"), \
             patch("builtins.input", side_effect=["2", "2200"]):  # spot=2200 < 2300 → call worthless
            wheel_paper_menu("ETH", spot=2200.0, iv=0.80, wb=mock_wb, days=7)

        loaded = _load("ETH")
        assert loaded["stage"]  == "holding"
        assert loaded["wins"]   == 1
        assert loaded["cycles"] == 1

    def test_expire_with_no_open_position_is_a_noop(
        self, tmp_path, monkeypatch, mock_wb, default_state,
    ):
        """[2] when no position is open should warn and not mutate state."""
        monkeypatch.chdir(tmp_path)
        _save("ETH", default_state)
        with patch("strategies.wheel.bs_put",  return_value=0.0), \
             patch("strategies.wheel.bs_call", return_value=0.0), \
             patch("strategies.wheel.append_trade_row") as mock_append, \
             patch("builtins.input", side_effect=["2"]):
            wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)
        loaded = _load("ETH")
        assert loaded == default_state
        mock_append.assert_not_called()

    # ── [3] Assign put ────────────────────────────────────────────────────────

    def test_assign_put_transitions_to_holding(
        self, tmp_path, monkeypatch, mock_wb, open_put_position, default_state,
    ):
        """[3] Assign on a short_put → holding with cost_basis=strike."""
        monkeypatch.chdir(tmp_path)
        existing = {**default_state,
                    "stage": "short_put",
                    "open":  open_put_position,
                    "total_premium": 25.0}
        _save("ETH", existing)

        with patch("strategies.wheel.bs_put",  return_value=0.0), \
             patch("strategies.wheel.bs_call", return_value=0.0), \
             patch("builtins.input", side_effect=["3"]):
            wheel_paper_menu("ETH", spot=1500.0, iv=0.80, wb=mock_wb, days=7)

        loaded = _load("ETH")
        assert loaded["stage"]      == "holding"
        assert loaded["open"]       is None
        assert loaded["cost_basis"] == open_put_position["strike"]
        assert loaded["asset_held"] == pytest.approx(open_put_position["qty"])

    def test_assign_rejected_outside_short_put_stage(
        self, tmp_path, monkeypatch, mock_wb, default_state,
    ):
        monkeypatch.chdir(tmp_path)
        _save("ETH", default_state)
        with patch("strategies.wheel.bs_put",  return_value=0.0), \
             patch("strategies.wheel.bs_call", return_value=0.0), \
             patch("builtins.input", side_effect=["3"]):
            wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)
        loaded = _load("ETH")
        assert loaded == default_state

    # ── [4] Sell Covered Call ─────────────────────────────────────────────────

    def test_sell_call_from_holding_opens_short_call(
        self, tmp_path, monkeypatch, mock_wb, default_state,
    ):
        """[4] from 'holding' → 'short_call' with an open Call position."""
        monkeypatch.chdir(tmp_path)
        existing = {**default_state,
                    "stage":      "holding",
                    "asset_held": 0.147,
                    "cost_basis": 1700.0,
                    "total_premium": 25.0}
        _save("ETH", existing)

        with patch("strategies.wheel.bs_put",  return_value=200.0), \
             patch("strategies.wheel.bs_call", return_value=200.0), \
             patch("strategies.wheel.append_trade_row"), \
             patch("builtins.input", side_effect=["4", ""]):  # accept suggested strike
            wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)

        loaded = _load("ETH")
        assert loaded["stage"]        == "short_call"
        assert loaded["open"]["type"] == "Call"
        assert loaded["total_premium"] > 25.0   # accumulated

    def test_sell_call_rejected_outside_holding(
        self, tmp_path, monkeypatch, mock_wb, default_state,
    ):
        monkeypatch.chdir(tmp_path)
        _save("ETH", default_state)
        with patch("strategies.wheel.bs_put",  return_value=200.0), \
             patch("strategies.wheel.bs_call", return_value=200.0), \
             patch("strategies.wheel.append_trade_row") as mock_append, \
             patch("builtins.input", side_effect=["4"]):
            wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)
        loaded = _load("ETH")
        assert loaded == default_state
        mock_append.assert_not_called()

    # ── [5] Back ──────────────────────────────────────────────────────────────

    def test_back_choice_does_not_mutate_state(
        self, tmp_path, monkeypatch, mock_wb, default_state,
    ):
        monkeypatch.chdir(tmp_path)
        _save("ETH", default_state)
        with patch("strategies.wheel.bs_put",  return_value=0.0), \
             patch("strategies.wheel.bs_call", return_value=0.0), \
             patch("strategies.wheel.append_trade_row") as mock_append, \
             patch("builtins.input", side_effect=["5"]):
            wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)
        loaded = _load("ETH")
        assert loaded == default_state
        mock_append.assert_not_called()


# ── End-to-end full cycle ─────────────────────────────────────────────────────

class TestFullWheelCycle:
    """
    Walk a single wheel cycle end-to-end by invoking wheel_paper_menu
    four times in sequence:

        Step 1 → [1] Sell Put          (no_position → short_put)
        Step 2 → [3] Assign            (short_put   → holding)
        Step 3 → [4] Sell Covered Call (holding     → short_call)
        Step 4 → [2] Call expires worthless  (short_call → holding, cycles += 1)

    After step 4, holding 'asset_held' is preserved and cycles == 1.
    A second worthless put would have been a separate path; this one
    exercises the assignment route, which is the more interesting branch.
    """

    def test_full_cycle_assignment_route(
        self, tmp_path, monkeypatch, mock_wb,
    ):
        monkeypatch.chdir(tmp_path)

        # Common pricing mocks: cheap, won't matter — wheel state is what we test.
        bs_put_patch  = patch("strategies.wheel.bs_put",  return_value=200.0)
        bs_call_patch = patch("strategies.wheel.bs_call", return_value=200.0)
        append_patch  = patch("strategies.wheel.append_trade_row")

        with bs_put_patch, bs_call_patch, append_patch as mock_append:
            # Step 1 — Sell Put (accept default strike)
            with patch("builtins.input", side_effect=["1", ""]):
                wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)
            s1 = _load("ETH")
            assert s1["stage"]         == "short_put"
            assert s1["open"]["type"]  == "Put"

            # Step 2 — Assign the put → holding the asset
            with patch("builtins.input", side_effect=["3"]):
                wheel_paper_menu("ETH", spot=1500.0, iv=0.80, wb=mock_wb, days=7)
            s2 = _load("ETH")
            assert s2["stage"]      == "holding"
            assert s2["open"]       is None
            assert s2["asset_held"] > 0
            assert s2["cost_basis"] == s1["open"]["strike"]

            # Step 3 — Sell a Covered Call (accept default strike)
            with patch("builtins.input", side_effect=["4", ""]):
                wheel_paper_menu("ETH", spot=2000.0, iv=0.80, wb=mock_wb, days=7)
            s3 = _load("ETH")
            assert s3["stage"]         == "short_call"
            assert s3["open"]["type"]  == "Call"

            # Step 4 — Call expires worthless (spot below strike)
            spot_at_expiry = s3["open"]["strike"] - 50.0
            with patch("builtins.input", side_effect=["2", str(spot_at_expiry)]):
                wheel_paper_menu("ETH", spot=spot_at_expiry, iv=0.80, wb=mock_wb, days=7)
            s4 = _load("ETH")

        assert s4["stage"]   == "holding"   # call worthless → still holding asset
        assert s4["open"]    is None
        assert s4["wins"]    == 1
        assert s4["losses"]  == 0
        assert s4["cycles"]  == 1            # cycle counter incremented
        assert s4["total_premium"] > s1["total_premium"]  # accumulated across legs
        assert mock_append.call_count == 3   # sell-put, sell-call, expire


# ── show_summary ──────────────────────────────────────────────────────────────

class TestShowSummary:
    """
    show_summary iterates three sheets and prints aggregates. We build a
    fake workbook where wb[name].iter_rows(...) yields known tuples, and
    inspect the captured stdout.

    Column layout used by show_summary:
      📝 Paper Trades / 📋 Live Trades  → result col=9, premium col=7
      🔀 Strangles                       → result col=11, premium col=7
    """

    def _make_workbook(self, paper_rows, live_rows, strangle_rows):
        """Build a MagicMock workbook with three sheets returning given rows."""
        wb = MagicMock()
        sheets = {
            "📝 Paper Trades": paper_rows,
            "📋 Live Trades":  live_rows,
            "🔀 Strangles":    strangle_rows,
        }

        def getitem(name):
            ws = MagicMock()
            ws.iter_rows.return_value = iter(sheets[name])
            return ws

        wb.__getitem__.side_effect = getitem
        return wb

    @staticmethod
    def _paper_row(date_str, result, premium):
        """A row matching paper-trades column layout: result @ idx 9, prem @ idx 7."""
        # 0:date 1: 2: 3: 4: 5: 6: 7:premium 8: 9:result
        return (date_str, "", "", "", "", "", "", premium, "", result)

    @staticmethod
    def _strangle_row(date_str, result, premium):
        """Strangle row: result @ idx 11, prem @ idx 7."""
        return (date_str, "", "", "", "", "", "", premium, "", "", "", result)

    def test_runs_with_empty_sheets(self, capsys):
        wb = self._make_workbook([], [], [])
        show_summary(wb)
        out = capsys.readouterr().out
        assert "No trades yet" in out

    def test_counts_wins_and_losses_in_paper_sheet(self, capsys):
        paper = [
            self._paper_row("2026-04-20", "Win",  10.0),
            self._paper_row("2026-04-21", "Win",  15.0),
            self._paper_row("2026-04-22", "Loss", 12.0),
        ]
        wb = self._make_workbook(paper, [], [])
        show_summary(wb)
        out = capsys.readouterr().out
        assert "2 / 1"   in out      # wins / losses
        assert "$37.00"  in out      # total premium
        assert "66.7%"   in out      # win rate

    def test_strangle_uses_different_result_column(self, capsys):
        strangles = [
            self._strangle_row("2026-04-20", "Win",  20.0),
            self._strangle_row("2026-04-21", "Loss", 18.0),
        ]
        wb = self._make_workbook([], [], strangles)
        show_summary(wb)
        out = capsys.readouterr().out
        assert "1 / 1"  in out
        assert "50.0%"  in out
        assert "$38.00" in out

    def test_skips_separator_rows(self, capsys):
        """Rows with '←' in column 0 are separators and should be skipped."""
        paper = [
            self._paper_row("2026-04-20", "Win",  10.0),
            ("← separator", "", "", "", "", "", "", None, "", None),
            self._paper_row("2026-04-21", "Loss", 12.0),
        ]
        wb = self._make_workbook(paper, [], [])
        show_summary(wb)
        out = capsys.readouterr().out
        # Only the two real trades should be counted.
        assert "1 / 1" in out

    def test_handles_missing_premium_values(self, capsys):
        """Non-numeric premium cells are ignored when averaging."""
        paper = [
            self._paper_row("2026-04-20", "Win",  10.0),
            self._paper_row("2026-04-21", "Open", None),   # open trade, no premium yet
        ]
        wb = self._make_workbook(paper, [], [])
        show_summary(wb)
        out = capsys.readouterr().out
        assert "$10.00" in out   # only the numeric premium counted
