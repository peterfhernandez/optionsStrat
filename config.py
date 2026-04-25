"""
config.py
=========
Central configuration for the Crypto Options Strategy Tool.

All modules import their settings from here. To tune the tool's behaviour,
edit this file — no need to touch strategy or display code.

Settings
--------
BUDGET_USD              Capital allocated to each trade (USD)
EXCEL_FILE              Output workbook filename
RISK_FREE_RATE          Annualised risk-free rate (decimal)
OTM_LEVELS              OTM strike targets to analyse
STOP_LOSS_MULTIPLIER    Close strangle at this multiple of premium received
STOP_WARN_MULTIPLIER    Warn at this multiple of premium received
DAILY_DAYS              Days constant for daily expiry
WEEKLY_DAYS             Days constant for weekly expiry
IV_FALLBACK             Fallback implied volatility if Deribit fetch fails
"""

# ── Trading ───────────────────────────────────────────────────────────────────

BUDGET_USD   = 250.0    # total capital allocated per trade (USD)
RISK_FREE_RATE = 0.05   # annualised risk-free rate (5%)
OTM_LEVELS   = [0.10, 0.15, 0.20]  # OTM strike targets: 10%, 15%, 20%
IV_FALLBACK  = 0.80     # fallback IV (80%) if Deribit fetch fails

# ── Expiry ────────────────────────────────────────────────────────────────────

DAILY_DAYS  = 1
WEEKLY_DAYS = 7

# ── Stop-loss ─────────────────────────────────────────────────────────────────

STOP_LOSS_MULTIPLIER = 2.0  # close strangle when value reaches 2x premium received
STOP_WARN_MULTIPLIER = 1.5  # warn when value reaches 1.5x premium received

# ── Files ─────────────────────────────────────────────────────────────────────

EXCEL_FILE           = "crypto_options_trade_tracker.xlsx"
PAPER_STATE_FILE     = "paper_state.json"
STRANGLE_STATE_FILE  = "strangle_state.json"
