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

## Files

- `volume_profile.py` – builds the per-session profile and extracts POC/VAH/VAL
- `data.py` – CSV/NinjaTrader loader + synthetic data generator
- `backtest.py` – runs the tests and prints the summary

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
