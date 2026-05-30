# Crypto Options Trading Tool

Personal paper trading and planning tool for crypto options strategies on Deribit.
Supports **ETH**, **BTC**, **SOL**, and **XRP** strategies.
Built to practice the **Wheel Strategy**, **Short Strangle**, **Calendar Spreads**, and **Credit Spreads** before trading with real money.

---

## Files

| File | Purpose |
|---|---|
| `main.py` | Main entry point — delegates to `ui.menus` |
| `automate.py` | One-shot automated strategy runner (cron / scheduler entry point) |
| `config.py` | Central configuration — all settings in one place |
| `optionsStrat.db` | SQLite database — trade history, state, and ledger (excluded from git) |

### Packages

| Package | Purpose |
|---|---|
| `access/` | Broker access layer — abstract interface + Deribit adapter (paper & live) |
| `models/` | SQLAlchemy ORM models — trade tables and state (see below) |
| `automation/` | Strategy automation (`automator.py`, `monitor.py`) |
| `trading/` | Order execution and portfolio management |
| `market/` | Market data fetching (`market_data.py`, `pricing.py`) |
| `ui/` | User interface (`display.py`, `menus.py`) |
| `strategies/` | Trading strategy implementations (wheel, strangle, calendar, spread, scanner, calendar_analysis) |
| `tests/` | Comprehensive test suite |

### Broker access layer (`access/`)

The `access` package provides a platform-agnostic interface for submitting orders to exchanges.

| Module | Purpose |
|---|---|
| `access/base.py` | `BrokerBase` abstract class + `OrderResult` dataclass |
| `access/deribit.py` | Deribit REST adapter — paper (`test.deribit.com`) and live (`www.deribit.com`) |

**Credentials** — set environment variables before running live or paper execution:

```
DERIBIT_CLIENT_ID=<your client id>
DERIBIT_CLIENT_SECRET=<your client secret>
```

Generate testnet keys at <https://test.deribit.com> → Account → API.

**Usage:**

```python
from access import DeribitClient, make_instrument
from datetime import date

client = DeribitClient(paper=True)   # paper=False for live
instrument = make_instrument("ETH", date(2025, 5, 30), 2000, "put")
result = client.place_order(instrument, "sell", 1, "limit", price=0.05)
```

`make_instrument(asset, expiry, strike, option_type)` builds Deribit instrument names
(e.g. `"ETH-30MAY25-2000-P"`) from trade parameters.

The `DERIBIT_PAPER` flag in `config.py` controls which environment the adapter targets
(`True` → testnet, `False` → live). Tokens are cached and refreshed automatically.

---

### Trade executor (`trading/executor.py`)

`enter_trade(candidate, days, broker)` is the single entry point for opening a position.

- **Database record always written** — every call persists the trade to SQLite regardless of whether a broker is supplied.
- **Optional live order** — pass a `BrokerBase` adapter (e.g. `DeribitClient`) to also submit the order to the exchange.

```python
from trading.executor import enter_trade
from access import DeribitClient

broker = DeribitClient(paper=True)          # omit for paper-only
result = enter_trade(candidate, broker=broker)
# result["broker_order_id"] contains the exchange order ID when a broker is used
```

For multi-leg strategies the result dict contains per-leg order IDs:
- Strangle: `broker_put_order_id`, `broker_call_order_id`
- Calendar: `broker_near_order_id`, `broker_far_order_id`

**Price conversion** — Black-Scholes premiums (USD) are converted to Deribit index prices (`price / spot`) before submission. Amounts follow Deribit conventions: USD notional for inverse contracts (BTC/ETH), contract count for linear contracts (SOL/XRP).

---

### Database models (`models/`)

All trade data is persisted in `optionsStrat.db` (SQLite). Call `models.init_db()` once on startup to create the schema.

| Model | Table | Purpose |
|---|---|---|
| `Single` | `singles` | Wheel strategy trades — replaces the Paper Trades Excel tab |
| `Strangle` | `strangles` | Short strangle trades — replaces the Strangles Excel tab |
| `Calendar` | `calendars` | Calendar spread trades — replaces the Calendars Excel tab |
| `Spread` | `spreads` | Credit spread trades (Bull Put Spread, Bear Call Spread) |

> `optionsStrat.db` and the old `*_state_*.json` files are excluded from git — they store local trading state only.

---

### Trading Fees

Deribit charges trading fees on options that depend on the underlying spot price and option premium.

**Deribit fee structure:**
- Base: **0.04%** of underlying spot price
- Cap: **12.5%** of option premium (whichever is lower)

Example: for an ETH option with spot $2000 and premium $50:
- Base fee = $2000 × 0.04% = $0.80
- Cap = $50 × 12.5% = $6.25
- **Actual fee = min($0.80, $6.25) = $0.80**

Every trade (entry and exit) incurs these fees, which are tracked as `open_fees` and `close_fees` in the trade records and subtracted from P&L calculations.

---

---

## What It Does

- **Fetches live crypto prices** from Binance (primary) and CoinGecko (fallback) for ETH, BTC, SOL, XRP
- **Fetches live Implied Volatility** from Deribit's public API
- **Strike & premium analysis** — shows 10%, 15%, 20% OTM options with estimated premiums, annualised yield and probability of profit
- **Wheel paper trading simulator** — tracks the full Put → Assign → Call cycle
- **Short Strangle paper trading** — sell both sides, with a live profit zone chart
- **Calendar Spread paper trading** — multiple near/far leg combinations (1d/7d, 1d/30d, 7d/30d) with ATM call or put calendar and spread-value monitor
- **Credit Spread paper trading** — Bull Put Spread (BPS) and Bear Call Spread (BCS) with max-loss bar
- **Portfolio view** — list open positions with live unrealised P&L
- **Stop-loss monitor** — warns at 1.5x and triggers at 2.0x premium (adjustable), take-profit at 5% remaining value (>2 days from expiry)
- **Daily or weekly expiry** — switchable in the menu
- **Multiple calendar spread types** — scanner generates 1d/7d, 1d/30d, and 7d/30d pairs for each asset and option type
- **SQLite database** — all trades persisted to `optionsStrat.db` via SQLAlchemy ORM (strangle and wheel strategies use SQLite; legacy Excel workbook retained for wheel-specific UI columns).

### Excel Workbook Sheets (Wheel & Calendar only)

| Sheet | Contents |
|---|---|
| 📊 Dashboard | Live KPIs — total premium, win rate, cycles completed |
| 📋 Live Trades | Real trades (manually recorded) |
| 📝 Paper Trades | Wheel paper trading history |
| 📈 Summary | Cycle-by-cycle performance |

> **Note:** Strangle trades are stored exclusively in the `strangles` SQLite table. The legacy `crypto_options_trade_tracker.xlsx` Strangles sheet is no longer written to.

---

## Strategies

### 1. Wheel Strategy
A two-leg income strategy suited to assets you're happy to hold long-term.

```
Step 1 → Sell a Cash-Secured Put below current price. Collect premium.
Step 2 → If it expires worthless: keep premium, repeat Step 1.
Step 3 → If assigned: you buy the asset at the strike price.
Step 4 → Sell a Covered Call above your cost basis. Collect premium.
Step 5 → If it expires worthless: keep premium, repeat Step 4.
Step 6 → If called away: sell the asset at strike. Cycle complete. Go to Step 1.
```

**Key settings used:**
- Budget: $250 USD per trade
- Strike target: 15% OTM (adjustable)
- Expiry: weekly (7-day)
- Platform: Deribit (cash-settled) or Coinbase Advanced (physically settled)
- Supported assets: ETH, BTC, SOL, XRP

**Note on Deribit:** Most options on Deribit are *cash-settled* — you don't automatically receive the asset on assignment. If assigned, buy the asset spot manually to continue the wheel.

---

### 4. Credit Spreads (Bull Put Spread / Bear Call Spread)
Sell an OTM option and buy a further OTM option at the same expiry. Limits both max profit and max loss.

**Bull Put Spread (BPS)** — bullish/neutral outlook:
```
Sell put at strike A (e.g. 10% OTM)
Buy  put at strike B (e.g. 15% OTM, lower)
→ Collect net credit (A premium − B premium)
→ Max profit = net credit (if spot > strike A at expiry)
→ Max loss   = (A − B) × qty − net credit
→ Breakeven  = A − (net credit / qty)
```

**Bear Call Spread (BCS)** — bearish/neutral outlook:
```
Sell call at strike A (e.g. 10% OTM above spot)
Buy  call at strike B (e.g. 15% OTM above spot, higher)
→ Collect net credit (A premium − B premium)
→ Max profit = net credit (if spot < strike A at expiry)
→ Max loss   = (B − A) × qty − net credit
→ Breakeven  = A + (net credit / qty)
```

**Key advantage over naked shorts:** max loss is bounded — you can never lose more than the strike width minus the credit received, regardless of how far the market moves.

**Monitor thresholds:**
- **75% of max loss** — warning displayed
- **100% of max loss** — auto stop-loss triggered
- **≤10% of credit remaining** — auto take-profit (position nearly worthless)

**Spread width:** configurable via `SPREAD_WIDTH_PCT` in `config.py` (default 5% OTM offset for the long protection leg).

---

### 3. Calendar Spread
Buy a longer-dated option and sell a shorter-dated option at the same strike. Profit from time decay and volatility term structure.

The scanner generates multiple calendar spread combinations to maximize flexibility:
```
Near/Far pairs (configurable):
  • 1d/7d   — quick decay trade (high theta, short duration)
  • 1d/30d  — long-term vega exposure (catch volatility expansion)
  • 7d/30d  — balanced trade (default, moderate risk/reward)

For each pair, both call and put calendars are generated:
Sell Put (N days)    +  Buy Put (F days)  [Put Calendar]
  or
Sell Call (N days)   +  Buy Call (F days) [Call Calendar]
→ Collect net debit from the spread (maximum loss)
→ Profit if time decay on the near leg is faster than the far leg
→ Best case: spot pins the strike at near-leg expiry, near expires worthless, far retains time value
```

**Far-leg pricing adjustment:** To improve order fill rates on far-leg purchases, the tool automatically adjusts Black-Scholes prices to account for:
- **Wider bid/ask spreads** on far-dated options (~0.5–2.5% depending on DTE)
- **Lower liquidity** on longer-dated contracts — additional 0.5–1% penalty for very far expirations (>30 days)

This means the net debit shown reflects a more realistic market price, improving the likelihood of getting the order filled rather than having the far leg sit unfilled as shown in the screenshot.

**Monitor thresholds:**
- **Stop-loss at 50% of debit paid** — max loss acceptable
- **Take-profit at 150% of debit** — exit to lock in gains

**Near-leg expiry handling:**
When the near leg expires, the monitor detects whether it expired OTM (worthless) or ITM:
- **OTM expiry (worthless):** The near leg is marked as a win (free premium kept). Instead of closing the far leg, the monitor fetches current far-leg data from Deribit and displays:
  - **Implied volatility** (mark, bid, ask) and classification (very low / low / normal / high / very high)
  - **Greeks:** delta, gamma, vega, theta, rho — with plain-English interpretation of what they mean
  - **Current mark value and P&L** — how much the far leg is worth now
  - **Recommendation:** Close the far leg, hold it for more theta decay, or roll in a new near leg
  - **Suggested roll options:** Up to 3 near-leg expirations (1d, 3d, 7d) that are valid before the far leg expires, with IV/greeks analysis to help decide
  
- **ITM expiry:** The position closes normally, buying back the near leg at intrinsic value and selling the far leg.

**Why this matters:** When the near leg expires worthless, you've already won — you've kept 100% of the premium. The remaining far leg is pure profit potential. The API analysis helps you decide whether to lock in gains immediately, hold for more decay, or add a new short (near) leg to fund further income.

---

### 2. Short Strangle
Sell an OTM put AND an OTM call simultaneously. Profit when the asset stays within a range.

```
Sell Put (15% below spot)  +  Sell Call (15% above spot)
→ Collect combined premium from both sides
→ Profit if asset stays between the two breakevens at expiry
→ Loss if asset breaks out hard in either direction
```

**Profit zone:**
```
Loss   |   Profit zone   |   Loss
       ↑                 ↑
   Lower B/E         Upper B/E
  (Put strike        (Call strike
  − premium)         + premium)
```

**Stop-loss rule (important):**
- **1.5x warning** — strangle has doubled in cost, plan your exit
- **2.0x stop** — buy back the strangle and take the loss
- Why: closing at 2x limits max loss to ~1x the premium collected. Holding further risks exponentially larger losses on a crypto move.

**Expected stats (15% OTM, weekly, ~80% IV):**
- ~86% probability of profit
- ~38% annualised yield on budget
- Breakeven range: roughly ±20% from current spot price

---

## Setup & Installation

### Requirements
```bash
pip install requests openpyxl
```

### Run
```bash
python main.py
```

### First run
The tool will:
1. Fetch live crypto price and IV automatically (ETH by default)
2. Create `crypto_options_trade_tracker.xlsx` if it doesn't exist
3. Drop you into the main menu — use `[2]` to switch between ETH, BTC, SOL, and XRP

---

## Menu Reference

```
[S]  Strategies (sub-menu)
[R]  Recommendations scanner
[A]  Auto-enter best paper trade  (prob >90%, yield ≥10%/yr, liquidity Med/High)
[M]  Monitor all positions  (available in both main and strategies menus)
[P]  Performance summary & stats
[O]  Portfolio positions & P&L
[H]  Trade history & cumulative P&L
[L]  Trading mode (paper/live)
[Y]  Set min yield filter
[1]  Switch expiry (daily / weekly)
[2]  Switch asset
[3]  Refresh market data
[4]  Cycle calendar near-leg expiry
[5]  Cycle calendar far-leg expiry
[0]  Exit
```

---

## Automated Strategy Selection

The tool can pick a trade for you and open the paper position automatically.

### What it does
1. Runs the scanner across every supported asset and OTM level.
2. Filters candidates by:
   - **Annualised yield ≥ 10 %/yr** (configurable)
   - **Probability of profit > 90 %** (configurable)
   - **Liquidity tag of Medium or High** (Low and unrated excluded)
3. Skips strategies that conflict with positions you already have open (e.g. won't try to open a second strangle).
4. Picks the candidate with the **highest probability of profit** (yield breaks ties).
5. Opens the paper trade, persists the per-asset state to the database, and logs the trade record with an `AUTO` note.
6. **If no candidate qualifies, it does nothing and exits cleanly.** Run it again later (an hourly schedule is the intended pattern).

### Liquidity rating

Every candidate the scanner produces carries a liquidity tag derived from Deribit order book data:

| Tag | Criteria |
|---|---|
| **High** | Open interest ≥ 1,000 contracts AND tight IV bid/ask spread (≤ 2 pp) |
| **Med** | Open interest ≥ 100 contracts OR 24h volume ≥ $50k |
| **Low** | Anything thinner — wider spreads, harder fills |
| _blank_ | Order book unavailable — automator treats as unrated, ineligible |

For multi-leg trades (Strangle, Cal-C, Cal-P, BPS, BCS) the tag is the **worst-of-legs** roll-up:
the smallest open interest, the smallest 24h volume, and the widest IV spread across
the two legs. A trade is only as fillable as its weakest leg, so the rating reflects
that bottleneck rather than averaging it away. If either leg's book is missing, the
tag is blank (the automator skips it rather than guessing).

You'll see the same `OI / Vol24h / Spread / Liq` columns in the scanner table for
every strategy, including strangle and calendar rows.

### Run it interactively
From the main menu, press `A` to invoke the automator once with the currently-selected asset and expiry; you can override the default minimum probability threshold when prompted.

### Run it from the shell
```bash
python automate.py                       # defaults: ETH, 7d, min yield 10%, Med+High liq
python automate.py --asset BTC --days 1
python automate.py --min-yield 15 --liquidity High
```

### Schedule it hourly
Any scheduler that runs `python automate.py` every hour from this directory will produce the "try again in 1 hour if nothing qualifies" behaviour described above.

Examples:

**cron (Linux/macOS)** — runs at the top of every hour:
```cron
0 * * * * cd /path/to/optionsStrat && /usr/bin/python3 automate.py >> automate.log 2>&1
```

**Windows Task Scheduler**: create a Basic Task with trigger _Daily, repeat every 1 hour for 24 hours_, action _Start a program_ → `python.exe`, arguments `automate.py`, start-in your repo path.

**Cowork scheduled task** (this app): create a scheduled task with cron `0 * * * *` and a prompt that runs `python automate.py` from the repo and reports the outcome.

---

## Version Control Workflow

```bash
# After making changes to the tool:
git add config.py
git commit -m "Short description of what changed"
git push origin main
```

**Don't commit:**
- `paper_state.json` / `strangle_state.json` — local state only
- `optionsStat.db` — SQLlite db

---

## Platform Notes

| Platform | Settlement | Min Size | AUD Deposits |
|---|---|---|---|
| Deribit | Cash-settled | 0.1 ETH | No — transfer from Kraken |
| Coinbase Advanced | Physical | Smaller | No |
| Kraken | Physical | Smaller | Yes ✅ |

**Recommended flow for Australia:**
```
Kraken (AUD → buy ETH/USDT) → transfer to Deribit → trade options
```

**Deribit testnet:** `test.deribit.com` — practice with fake funds, real market prices. No KYC required.

**Asset availability:** ETH and BTC have full options support on testnet. SOL and XRP options availability is limited on testnet — if you see "No SOL/XRP options available on Deribit" error, try BTC or ETH instead, or switch `DERIBIT_PAPER = False` in `config.py` to use live Deribit (requires verified account).

---

## Pricing Note

All premiums are estimated using **Black-Scholes** with live IV from Deribit.
Actual market prices will differ slightly due to skew, liquidity, and bid/ask spread.
Always check the actual order book before placing a trade.

---

## ⚠️ Disclaimer

This tool is for **personal paper trading and education only**.

- Not financial advice
- Options trading involves significant risk including total loss of capital
- Short strangles have **unlimited loss potential** on the call side
- Crypto markets can move 20–50% in a single day
- Never trade with money you cannot afford to lose entirely
- Past paper trading performance does not predict live trading results
- Consult a licensed financial adviser before trading with real money

---

*Last updated: May 2026 — improved far-leg pricing in calendar spreads to account for liquidity and bid/ask spreads*
