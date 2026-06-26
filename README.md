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

python backtest.py            # synthetic intraday data (runs out of the box)
python backtest.py data.csv   # your own intraday OHLCV
```

### CSV format

Intraday bars (5-min, 1-min, etc.). Columns (case-insensitive):

```
timestamp,open,high,low,close,volume
2024-01-02 09:30:00,100.0,100.4,99.8,100.2,1200
```

Finer bars → more accurate profiles.

## Files

- `volume_profile.py` – builds the per-session profile and extracts POC/VAH/VAL
- `data.py` – CSV loader + synthetic data generator
- `backtest.py` – runs the tests and prints the summary

## Tuning

Thresholds live at the top of `backtest.py`:

- `BREAK_ATR` – distance past a level (in ATR) that counts as a break
- `REACTION_ATR` – move off a level (in ATR) that counts as a hold
- `CONFLUENCE_ATR` – how close daily & weekly levels must be to be "confluent"
- `LOOKAHEAD` – bars allowed to resolve a touch (per timeframe)

## Notes

- A touch is **unresolved** if it neither holds nor breaks within `LOOKAHEAD`.
- The **confluence** metric needs varied/trending data to be meaningful. On the
  range-bound synthetic data nearly every daily level overlaps a weekly one, so
  feed real data to read that number.
