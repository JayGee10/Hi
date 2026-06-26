"""Data loading for the Session VP backtest.

Either load your own intraday OHLCV CSV, or generate synthetic intraday data so
the backtest runs out of the box.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    """Load intraday OHLCV bars.

    Handles two shapes automatically:
      1. Comma-separated with a header row containing timestamp/open/high/...
      2. Headerless NinjaTrader-style exports:
         `yyyyMMdd HHmmss;open;high;low;close;volume` (semicolon-delimited).

    Returns a DataFrame indexed by timestamp with columns
    open, high, low, close, volume.
    """
    with open(path) as fh:
        first = fh.readline().strip()

    delim = ";" if first.count(";") >= first.count(",") else ","
    fields = first.split(delim)
    # treat as headerless if the 2nd field parses as a number
    try:
        float(fields[1])
        has_header = False
    except (ValueError, IndexError):
        has_header = True

    if has_header:
        df = pd.read_csv(path, sep=delim)
        df.columns = [c.strip().lower() for c in df.columns]
    else:
        cols = ["timestamp", "open", "high", "low", "close", "volume"][: len(fields)]
        df = pd.read_csv(path, sep=delim, header=None, names=cols)

    ts_col = next(
        (c for c in df.columns if c in ("timestamp", "date", "datetime", "time")),
        df.columns[0],
    )
    raw_ts = df[ts_col].astype(str)
    # NinjaTrader stamps look like "20250919 040100"; fall back to generic parse
    ts = pd.to_datetime(raw_ts, format="%Y%m%d %H%M%S", errors="coerce")
    if ts.isna().all():
        ts = pd.to_datetime(raw_ts, errors="coerce")
    df[ts_col] = ts
    df = df.dropna(subset=[ts_col]).set_index(ts_col).sort_index()

    needed = ["open", "high", "low", "close", "volume"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")
    return df[needed].astype(float)


def synthetic(
    days: int = 180,
    bars_per_day: int = 78,        # ~6.5h of 5-min bars
    seed: int = 7,
    start: str = "2024-01-01",
) -> pd.DataFrame:
    """Generate plausible intraday OHLCV with intraday mean-reversion plus
    a slow drift, and a volume profile that bulges around the session mean
    (so POC/value-area structure is realistic)."""
    rng = np.random.default_rng(seed)
    rows = []
    price = 100.0
    dates = pd.bdate_range(start=start, periods=days)  # business days only
    for d in dates:
        day_open = price
        # session anchor that price mean-reverts toward intraday
        anchor = day_open * (1 + rng.normal(0, 0.004))
        session_start = pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=30)
        for i in range(bars_per_day):
            ts = session_start + pd.Timedelta(minutes=5 * i)
            # mean-revert to anchor + small noise
            drift = (anchor - price) * 0.03
            shock = rng.normal(0, 0.0015) * price
            close = price + drift + shock
            high = max(price, close) + abs(rng.normal(0, 0.0008)) * price
            low = min(price, close) - abs(rng.normal(0, 0.0008)) * price
            # volume: U-shaped intraday + heavier near the anchor (builds a POC)
            tod = 1.4 if (i < 8 or i > bars_per_day - 8) else 1.0
            proximity = np.exp(-((close - anchor) / (0.004 * price)) ** 2)
            vol = max(1.0, rng.normal(1000, 200) * tod * (0.5 + proximity))
            rows.append((ts, price, high, low, close, vol))
            price = close
        # carry a small overnight gap into next day
        price *= (1 + rng.normal(0.0003, 0.003))

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return df.set_index("timestamp").sort_index()
