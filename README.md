# Session Volume Profile backtest

Measures how price **tests** Session Volume Profile levels on **daily** vs
**weekly** sessions. For each session it takes the *previous* session's profile
(POC / VAH / VAL) and checks how price reacts to those levels in the next
session.

Sessions are the **real CME trading sessions in Eastern time** — by default the
**RTH cash session (09:30–16:00 ET)** — not calendar days. Raw data timestamps
are assumed to be UTC and converted to America/New_York (DST-aware). See
`sessions.py`.

## Levels

- **POC** – Point of Control (most-traded price)
- **VAH / VAL** – Value Area High / Low (edges of the ~70% volume range)
- **LVN** – Low-Volume Node (thin zones price tends to move through)

## What it measures

- **hold rate** – how often a level rejects price (acts as support/resistance)
- **break rate** – how often price accepts through it
- **reaction** – favorable move (in ATR) after a hold
- **confluence edge** – do daily levels hold more when a weekly level sits on
  top of them?

## Run

```bash
pip install -r requirements.txt

python backtest.py                   # synthetic intraday data (out of the box)
python backtest.py data.txt          # one intraday OHLCV file (UTC stamps)
python backtest.py a.txt b.txt c.txt # many files -> per-file + pooled COMBINED
```

### Interactive chart (phone-friendly)

```bash
python build_html.py out.html file1.txt file2.txt ...
```

Self-contained HTML (Plotly inlined, works offline). Dropdowns switch
**contract**, **session (RTH / Full)**, and **timeframe (5m–4h)**; candles are in
ET, with POC / value area and LVN boxes drawn.

### Accepted data formats

The loader auto-detects both:

1. **Comma + header** (case-insensitive): `timestamp,open,high,low,close,volume`
2. **Headerless NinjaTrader export** (semicolon): `20250919 040100;24712.5;...`

Finer bars → more accurate profiles. The backtest lookahead auto-scales to the
file's bar size.

## Profile shapes & ML

On top of the backtest there's a small ML layer built around the four canonical
volume-profile **shapes**:

- **D** – balanced/normal: POC mid-range, symmetric → balance, mean-revert to POC
- **P** – POC high with a thin tail below → bullish (short-covering / accumulation)
- **b** – POC low with a thin tail above → bearish (long liquidation / distribution)
- **B** – double distribution: two nodes split by an LVN → the LVN is the decision point
- **trend** – elongated/thin, no dominant node → one-timeframe directional day

`shapes.py` classifies each session's histogram and exposes the numeric features
behind the call (POC position in range, volume skew, mode count, value-area
width, concentration). `dataset.py` turns sessions into a labeled table —
shape + level + context features → does the next session **hold or break** the
prior level — and `train.py` fits sklearn models to predict it.

```bash
python train.py                    # synthetic data (out of the box)
python train.py a.txt b.txt ...    # one or more intraday OHLCV files (UTC)
python test_shapes.py              # sanity-check the shape classifier
```

`train.py` prints hold-rate **by shape** (the raw signal), compares a logistic
and a gradient-boosting model against a majority-class baseline (accuracy + AUC),
lists the top features, and saves the model to `vp_model.joblib`. Note the
synthetic generator is mean-reverting noise with no real shape→outcome
structure, so the models there sit near the baseline — point it at real intraday
futures data to see whether the shapes carry an edge.

## Files

- `sessions.py` – UTC→ET conversion and CME RTH/ETH session grouping
- `volume_profile.py` – per-session profile → POC/VAH/VAL, LVNs
- `shapes.py` – classify the profile shape (D/P/b/B/trend) + shape features
- `dataset.py` – build the labeled (features → hold/break) training table
- `train.py` – train & evaluate the hold/break classifier, save the model
- `test_shapes.py` – sanity tests for the shape classifier
- `data.py` – CSV / NinjaTrader loader + synthetic data generator
- `backtest.py` – runs the daily/weekly session tests and prints the summary
- `build_html.py` – builds the interactive phone chart
- `plot.py` – static matplotlib chart (PNG)

## Tuning

Thresholds live at the top of `backtest.py`:

- `SESSION` – "RTH" (cash hours) or "ETH" (full Globex day)
- `BREAK_ATR` – distance past a level (in ATR) that counts as a break
- `REACTION_ATR` – move off a level (in ATR) that counts as a hold
- `CONFLUENCE_ATR` – how close daily & weekly levels must be to be "confluent"
- `LOOKAHEAD_MIN` – minutes allowed to resolve a touch (per timeframe), auto-
  scaled to the data's bar interval

## Notes

- A touch is **unresolved** if it neither holds nor breaks within `LOOKAHEAD_MIN`.
- RTH excludes overnight bars from the profile; switch `SESSION="ETH"` (or the
  HTML's Full toggle) to include the full electronic day.
