"""
conftest.py
===========
Shared pytest fixtures for the Crypto Options Strategy Tool test suite.

Fixtures defined here are automatically available to all test files
without needing an explicit import.
"""

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.base import Base, engine as real_engine, SessionLocal


# ── Standard market parameters ────────────────────────────────────────────────
# Realistic ETH-like values used consistently across tests.
# Keeping them in one place means a single edit updates all tests.

@pytest.fixture
def spot():
    """Current underlying price in USD."""
    return 2000.0


@pytest.fixture
def strike_atm(spot):
    """At-the-money strike — equal to spot."""
    return spot


@pytest.fixture
def strike_otm_put(spot):
    """10% OTM put strike."""
    return spot * 0.90


@pytest.fixture
def strike_otm_call(spot):
    """10% OTM call strike."""
    return spot * 1.10


@pytest.fixture
def T_weekly():
    """7-day expiry in years."""
    return 7 / 365.0


@pytest.fixture
def T_daily():
    """1-day expiry in years."""
    return 1 / 365.0


@pytest.fixture
def r():
    """Risk-free rate (5% annualised)."""
    return 0.05


@pytest.fixture
def iv():
    """Implied volatility (80% — typical for crypto)."""
    return 0.80


@pytest.fixture
def std_params(spot, T_weekly, r, iv):
    """
    Convenience bundle of standard pricing parameters.
    Returns (S, T, r, v) — strike must be supplied per test.
    """
    return spot, T_weekly, r, iv


@pytest.fixture
def mock_wb():
    """A mock openpyxl workbook — never actually written to."""
    return MagicMock()


@pytest.fixture(autouse=True)
def use_in_memory_db(monkeypatch):
    """
    Patch database functions to use an in-memory SQLite database.
    This fixture runs for all tests automatically (autouse=True).
    """
    # Create in-memory engine and session factory
    mem_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(mem_engine)
    MemSessionLocal = sessionmaker(bind=mem_engine)

    # Patch the models.get_session function to use in-memory DB
    def mock_get_session():
        return MemSessionLocal()

    monkeypatch.setattr("models.get_session", mock_get_session)
    monkeypatch.setattr("database.wheel_db.get_session", mock_get_session)
    monkeypatch.setattr("database.strangle_db.get_session", mock_get_session)
    monkeypatch.setattr("models.base.get_session", mock_get_session, raising=False)

    yield
