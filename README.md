# Crypto Options Trading Tool

Personal paper trading and planning tool for ETH options strategies on Deribit.
Built to practice the **Wheel Strategy** and **Short Strangle** before trading with real money.

---

## Files

| File | Purpose |
|---|---|
| `main.py` | Main interactive tool — run this |
| `automate.py` | One-shot automated strategy runner (cron / scheduler entry point) |
| `crypto_options_trade_tracker.xlsx` | Excel workbook — auto-updated by the tool |
| `paper_state_<ASSET>.json` | Wheel paper trading state (auto-created) |
| `strangle_state_<ASSET>.json` | Strangle paper trading state (auto-created) |
| `calendar_state_<ASSET>.json` | Calendar spread paper trading state (auto-created) |
| `strategies/automator.py` | Auto-selects highest-probability candidate and opens the trade |

> `paper_state.json` and `strangle_state.json` are excluded from git (see `.gitignore`) — they store local paper trading progress only.

---

## What It Does

- **Fetches live ETH price** from CoinGecko (free, no API key)
- **Fetches live Implied Volatility** from Deribit's public API
- **Strike & premium analysis** — shows 10%, 15%, 20% OTM options with estimated premiums, annualised yield and probability of profit
- **Wheel paper trading simulator** — tracks the full Put → Assign → Call cycle
- **Short Strangle paper trading** — sell both sides, with a live profit zone chart
- **Stop-loss monitor** — warns at 1.5x and triggers at 2.0x premium (adjustable)
- **Daily or weekly expiry** — switchable in the menu
- **Excel tracker** — all trades written automatically to `crypto_options_trade_tracker.xlsx`

### Excel Workbook Sheets

| Sheet | Contents |
|---|---|
| 📊 Dashboard | Live KPIs — total premium, win rate, cycles completed |
| 📋 Live Trades | Real trades (manually recorded) |
| 📝 Paper Trades | Wheel paper trading history |
| 🔀 Strangles | Strangle paper trading history |
| 📈 Summary | Cycle-by-cycle performance |

---

## Strategies

### 1. Wheel Strategy
A two-leg income strategy suited to assets you're happy to hold long-term.

```
Step 1 → Sell a Cash-Secured Put below current price. Collect premium.
Step 2 → If it expires worthless: keep premium, repeat Step 1.
Step 3 → If assigned: you buy ETH at the strike price.
Step 4 → Sell a Covered Call above your cost basis. Collect premium.
Step 5 → If it expires worthless: keep premium, repeat Step 4.
Step 6 → If called away: sell ETH at strike. Cycle complete. Go to Step 1.
```

**Key settings used:**
- Budget: $250 USD
- Strike target: 15% OTM (adjustable)
- Expiry: weekly (7-day)
- Platform: Deribit (cash-settled) or Coinbase Advanced (physically settled)

**Note on Deribit:** ETH options on Deribit are *cash-settled* — you don't automatically receive ETH on assignment. If assigned, buy ETH spot manually to continue the wheel.

---

### 2. Short Strangle
Sell an OTM put AND an OTM call simultaneously. Profit when ETH stays within a range.

```
Sell Put (15% below spot)  +  Sell Call (15% above spot)
→ Collect combined premium from both sides
→ Profit if ETH stays between the two breakevens at expiry
→ Loss if ETH breaks out hard in either direction
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
- Breakeven range: roughly ±20% from current ETH price

---

## Setup & Installation

### Requirements
```bash
pip install requests openpyxl
```

### Run
```bash
python crypto_options_trade.py
```

### First run
The tool will:
1. Fetch live ETH price and IV automatically
2. Create `crypto_options_trade_tracker.xlsx` if it doesn't exist
3. Drop you into the main menu

---

## Menu Reference

```
[S]  Strategies (sub-menu)
[R]  Recommendations scanner
[A]  Auto-enter best paper trade  (yield ≥10%/yr, liquidity Med/High)
[M]  Monitor all positions
[P]  Performance summary & stats
[Y]  Set min yield filter
[1]  Switch expiry (daily / weekly)
[2]  Switch asset
[3]  Refresh market data
[0]  Exit
```

---

## Automated Strategy Selection

The tool can pick a trade for you and open the paper position automatically.

### What it does
1. Runs the scanner across every supported asset and OTM level.
2. Filters candidates by:
   - **Annualised yield ≥ 10 %/yr** (configurable)
   - **Liquidity tag of Medium or High** (Low and unrated excluded)
3. Skips strategies that conflict with positions you already have open (e.g. won't try to open a second strangle).
4. Picks the candidate with the **highest probability of profit** (yield breaks ties).
5. Opens the paper trade, persists the per-asset state file, and logs the row to the Excel workbook with an `AUTO` note.
6. **If no candidate qualifies, it does nothing and exits cleanly.** Run it again later (an hourly schedule is the intended pattern).

### Liquidity rating

Every candidate the scanner produces carries a liquidity tag derived from Deribit order book data:

| Tag | Criteria |
|---|---|
| **High** | Open interest ≥ 1,000 contracts AND tight IV bid/ask spread (≤ 2 pp) |
| **Med** | Open interest ≥ 100 contracts OR 24h volume ≥ $50k |
| **Low** | Anything thinner — wider spreads, harder fills |
| _blank_ | Order book unavailable — automator treats as unrated, ineligible |

For multi-leg trades (Strangle, Cal-C, Cal-P) the tag is the **worst-of-legs** roll-up:
the smallest open interest, the smallest 24h volume, and the widest IV spread across
the two legs. A trade is only as fillable as its weakest leg, so the rating reflects
that bottleneck rather than averaging it away. If either leg's book is missing, the
tag is blank (the automator skips it rather than guessing).

You'll see the same `OI / Vol24h / Spread / Liq` columns in the scanner table for
every strategy, including strangle and calendar rows.

### Run it interactively
From the main menu, press `A` to invoke the automator once with the currently-selected asset and expiry.

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
git add crypto_options_trade.py
git commit -m "Short description of what changed"
git push origin main
```

**Don't commit:**
- `paper_state.json` / `strangle_state.json` — local state only
- `~$crypto_options_trade_tracker.xlsx` — Excel lock file (auto-excluded)

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

*Last updated: April 2026*
