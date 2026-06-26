# Session Volume Profile backtest

Measures how price **tests** Session Volume Profile levels on **daily** vs
**weekly** sessions. For each session it takes the *previous* session's profile
(POC / VAH / VAL) and checks how price reacts to those levels in the next
session.

## Levels

- **POC** – Point of Control (most-traded price)
- **VAH / VAL** – Value Area High / Low (edges of the ~70% volume range)

## What it measures

- **hold rate** – how often a level rejects price (acts as support/resistance)
- **break rate** – how often price accepts through it
- **reaction** – favorable move (in ATR) after a hold
- **confluence edge** – do daily levels hold more when a weekly level sits on
  top of them?

## Run

```bash
pip install -r requirements.txt

python backtest.py                   # synthetic intraday data (runs out of the box)
python backtest.py data.csv          # one intraday OHLCV file
python backtest.py a.txt b.txt c.txt # many files -> per-file + pooled COMBINED
```

Pass several files (e.g. consecutive futures contracts) to pool them into one
`COMBINED` summary — useful because per-contract weekly samples are small.

### Accepted data formats

The loader auto-detects both of these:

1. **Comma + header** (case-insensitive columns):
   ```
   timestamp,open,high,low,close,volume
   2024-01-02 09:30:00,100.0,100.4,99.8,100.2,1200
   ```
2. **Headerless NinjaTrader export** (semicolon-delimited):
   ```
   20250919 040100;24712.5;24715;24709.5;24712.75;271
   ```

Finer bars → more accurate profiles. The lookahead auto-scales to the file's
bar size, so 1-min and 5-min data both work.

## Interactive viewer (phone-friendly)

```bash
python build_html.py out.html MNQ_*.Last.txt   # one self-contained HTML for all contracts
```

For each MNQ contract the viewer builds two trading-week windows:

- **This week (developing)** – anchored to this week's Sunday open, up to the
  file's last bar.
- **Last week (fixed)** – the prior *completed* Sunday-open → Friday-close week.

The trading-week boundary is auto-detected from the data's daily maintenance
gap (the builder prints the detected session-open time — `17:00` for
exchange/Central exports, `18:00` for Eastern), so no timezone needs hardcoding.

There is also a **Daily** mode (per-day Globex session, pick any day):

- full-day candles with a vertical line at the **9:30 cash open**,
- the **overnight** session VP with its LVN zones marked (the 9:30-open focus),
- the **RTH 9:30–3pm** VP shown side by side — it appears in replay once 3pm
  is reached,
- a label for where the 9:30 open landed vs overnight value (inside / above /
  below), plus an **aggregate** across all days (% open-in-value and how often
  the cash session closed up).

Cash-session times auto-adjust to the data's timezone (9:30 ET = 8:30 in CT
exports).

Dropdowns switch contract / window / day / timeframe. A **crosshair** follows
the cursor with a price/time readout and distance to the levels. A **replay**
control (play, step, reset, scrub, speed) reveals candles one at a time while
the volume profile (POC / value area / LVN boxes) rebuilds in step — like
TradingView bar replay.

## Files

- `volume_profile.py` – builds the per-session profile and extracts POC/VAH/VAL
- `data.py` – CSV/NinjaTrader loader, synthetic data, trading-week windowing
- `backtest.py` – runs the tests and prints the summary
- `build_html.py` – interactive HTML viewer (two week profiles + candle replay)

## Tuning

Thresholds live at the top of `backtest.py`:

- `BREAK_ATR` – distance past a level (in ATR) that counts as a break
- `REACTION_ATR` – move off a level (in ATR) that counts as a hold
- `CONFLUENCE_ATR` – how close daily & weekly levels must be to be "confluent"
- `LOOKAHEAD_MIN` – minutes allowed to resolve a touch (per timeframe), auto-
  scaled to the data's bar interval

## Notes

- A touch is **unresolved** if it neither holds nor breaks within `LOOKAHEAD_MIN`.
- "Session" is the calendar day / week. For 24h futures you may want to redefine
  it as the exchange RTH/ETH session — that will change the levels and results.
- The **confluence** metric needs varied/trending data to be meaningful.
