"""
config.py
=========
Central configuration for the Crypto Options Strategy Tool.

All modules import their settings from here. To tune the tool's behaviour,
edit this file — no need to touch strategy or display code.

Settings
--------
SUPPORTED_ASSETS        Per-asset config: CoinGecko ID, Deribit ticker, strike rounding
DEFAULT_ASSET           Asset used on startup (ETH, BTC, or SOL)
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

# ── Supported assets ─────────────────────────────────────────────────────────
#
# Add a new asset here and market_data.py will support it automatically.
#
# Fields per asset:
#   coingecko_id  : str    CoinGecko coin ID for spot price fetch
#   deribit_ticker: str    Deribit instrument prefix (e.g. "ETH", "BTC", "SOL")
#   strike_round  : int    Round ATM strike to nearest N dollars
#                          (ETH ~$2k → $100 increments, BTC ~$90k → $500,
#                           SOL ~$150 → $1 increments)
 
SUPPORTED_ASSETS = {
    "ETH": {
        "coingecko_id":   "ethereum",
        "deribit_ticker": "ETH",
        "strike_round":   100,
    },
    "BTC": {
        "coingecko_id":   "bitcoin",
        "deribit_ticker": "BTC",
        "strike_round":   500,
    },
    "SOL": {
        "coingecko_id":   "solana",
        "deribit_ticker": "SOL",
        "strike_round":   1,
    },
}
 
DEFAULT_ASSET = "ETH"   # used by main.py if no asset is selected

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
