"""
config.py
=========
Central configuration for the Crypto Options Strategy Tool.

All modules import their settings from here. To tune the tool's behaviour,
edit this file — no need to touch strategy or display code.

Settings
--------
SUPPORTED_ASSETS        Per-asset config: CoinGecko ID, Deribit ticker, strike rounding
DEFAULT_ASSET           Asset used on startup (ETH, BTC, SOL, or XRP)
BUDGET_USD              Capital allocated to each trade (USD)
EXCEL_FILE              Output workbook filename
RISK_FREE_RATE          Annualised risk-free rate (decimal)
OTM_LEVELS              OTM strike targets to analyse
STOP_LOSS_MULTIPLIER    Close strangle at this multiple of premium received
STOP_WARN_MULTIPLIER    Warn at this multiple of premium received
DAILY_DAYS              Days constant for daily expiry
WEEKLY_DAYS             Days constant for weekly expiry
IV_FALLBACK             Fallback implied volatility if Deribit fetch fails
MIN_YIELD_PCT           Default minimum annualised yield to qualify for recommendations
"""

# ── Supported assets ─────────────────────────────────────────────────────────
#
# Add a new asset here and market_data.py will support it automatically.
#
# Fields per asset:
#   binance_symbol: str    Binance USDT pair symbol (primary price source)
#   coingecko_id  : str    CoinGecko coin ID (fallback price source)
#   deribit_ticker: str    Deribit instrument prefix
#   strike_round  : int    Round ATM strike to nearest N dollars
#
# Deribit ticker notes:
#   BTC, ETH, XRP  — inverse (USD-settled) contracts, ticker = asset symbol
#   SOL       — linear USDC-settled contracts, ticker = "SOL_USDC"
#   BTC strike increments: ~$90k price → $1000 increments
#   ETH strike increments: ~$2k  price → $100  increments
#   SOL strike increments: ~$150 price → $1    increments
#   XRP strike increments: ~$2.5 price → $0.10 increments
 
SUPPORTED_ASSETS = {
    "ETH": {
        "binance_symbol": "ETHUSDT",
        "coingecko_id":   "ethereum",
        "deribit_ticker": "ETH",
        "strike_round":   100,
    },
    "BTC": {
        "binance_symbol": "BTCUSDT",
        "coingecko_id":   "bitcoin",
        "deribit_ticker": "BTC",
        "strike_round":   1000,
    },
    "SOL": {
        "binance_symbol": "SOLUSDT",
        "coingecko_id":   "solana",
        "deribit_ticker": "SOL_USDC",
        "strike_round":   1,
    },
    "XRP": {
        "binance_symbol": "XRPUSDT",
        "coingecko_id":   "ripple",
        "deribit_ticker": "XRP_USDC",
        "strike_round":   0.05,
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

# ── Calendar Spread ───────────────────────────────────────────────────────────

CALENDAR_NEAR_DAYS = 7    # near (short) leg expiry for calendar spreads (days)
CALENDAR_FAR_DAYS  = 30   # far  (long)  leg expiry for calendar spreads (days)
CALENDAR_STOP_PCT  = 0.50 # stop if spread value drops to 50% of net debit paid

# Cycle order for the main-menu toggles ([4] near leg, [5] far leg).
# Pressing the toggle steps through these in order; when the current
# value isn't in the list the toggle resets to the first one.
CALENDAR_NEAR_OPTIONS = [1, 3, 7, 14]              # short-leg horizon choices
CALENDAR_FAR_OPTIONS  = [14, 30, 60, 90]           # long-leg horizon choices

# ── Files ─────────────────────────────────────────────────────────────────────
'''
EXCEL_FILE           = "crypto_options_trade_tracker.xlsx"
PAPER_STATE_FILE     = "paper_state.json"
STRANGLE_STATE_FILE  = "strangle_state.json"
'''

# ── Scanner ───────────────────────────────────────────────────────────────────

MIN_YIELD_PCT = 20.0    # minimum annualised yield for ranking ① in scanner

# ── Paper/Live ────────────────────────────────────────────────────────────────

TRADING_MODE = "paper"  # "paper" or "live" - affects which state file is used and whether stop-loss is enforced

# ── Deribit API ───────────────────────────────────────────────────────────────
#
# DERIBIT_PAPER = True  → connects to test.deribit.com (Deribit Testnet)
# DERIBIT_PAPER = False → connects to www.deribit.com  (live trading)
#
# Credentials must be supplied via environment variables (never hard-code):
#   DERIBIT_CLIENT_ID      — API key client id from Deribit account settings
#   DERIBIT_CLIENT_SECRET  — API key client secret
#
# To get testnet credentials: register at https://test.deribit.com and create
# an API key under Account → API.

DERIBIT_PAPER = True   # always paper until explicitly switched to live