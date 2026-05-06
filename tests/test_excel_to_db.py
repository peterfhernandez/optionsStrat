"""Tests for excel_to_db migration module."""
import datetime

import pytest

from migration.excel_to_db import (
    _extract_asset_from_notes,
    _parse_date,
    _parse_near_far_days,
)


class TestHelpers:
    def test_extract_asset_from_notes_eth(self):
        assert _extract_asset_from_notes("ETH position, 15% OTM") == "ETH"
        assert _extract_asset_from_notes("Some notes eth more notes") == "ETH"

    def test_extract_asset_from_notes_btc(self):
        assert _extract_asset_from_notes("BTC strangle") == "BTC"

    def test_extract_asset_from_notes_sol(self):
        assert _extract_asset_from_notes("Short strangle on SOL") == "SOL"

    def test_extract_asset_from_notes_xrp(self):
        assert _extract_asset_from_notes("XRP position") == "XRP"

    def test_extract_asset_default_if_not_found(self):
        assert _extract_asset_from_notes("some random notes") == "ETH"
        assert _extract_asset_from_notes(None) == "ETH"
        assert _extract_asset_from_notes("") == "ETH"

    def test_parse_date_datetime_object(self):
        dt = datetime.datetime(2026, 5, 6, 10, 30)
        assert _parse_date(dt) == datetime.date(2026, 5, 6)

    def test_parse_date_date_object(self):
        d = datetime.date(2026, 5, 6)
        assert _parse_date(d) == d

    def test_parse_date_string_dd_mmm_yyyy(self):
        assert _parse_date("06-May-2026") == datetime.date(2026, 5, 6)

    def test_parse_date_string_yyyy_mm_dd(self):
        assert _parse_date("2026-05-06") == datetime.date(2026, 5, 6)

    def test_parse_date_string_mm_dd_yyyy(self):
        assert _parse_date("05/06/2026") == datetime.date(2026, 5, 6)

    def test_parse_date_none(self):
        assert _parse_date(None) is None

    def test_parse_date_invalid_string(self):
        assert _parse_date("invalid date") is None

    def test_parse_near_far_days_valid(self):
        assert _parse_near_far_days("7/30") == (7, 30)
        assert _parse_near_far_days("1/7") == (1, 7)
        assert _parse_near_far_days("14/60") == (14, 60)

    def test_parse_near_far_days_with_spaces(self):
        assert _parse_near_far_days("7 / 30") == (7, 30)
        assert _parse_near_far_days(" 7/ 30 ") == (7, 30)

    def test_parse_near_far_days_single_value(self):
        near, far = _parse_near_far_days("7")
        assert near == 7
        assert far is None

    def test_parse_near_far_days_none(self):
        near, far = _parse_near_far_days(None)
        assert near is None
        assert far is None

    def test_parse_near_far_days_empty_string(self):
        near, far = _parse_near_far_days("")
        assert near is None
        assert far is None

    def test_parse_near_far_days_invalid(self):
        near, far = _parse_near_far_days("abc/def")
        assert near is None
        assert far is None
