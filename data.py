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


def detect_session_open_hour(bars: pd.DataFrame, default: int = 18) -> int:
    """Infer the hour of day the trading session/day opens.

    CME index futures (MNQ) halt for ~60 min once a day. The bar right after
    that daily break marks the session open. We look at the gaps between
    consecutive bars, keep the "daily break" sized ones (roughly 45 min to 5 h,
    which excludes the much larger weekend gap), and return the most common
    hour-of-day of the bar that *follows* such a gap.

    Returns 17 for exchange/Central-time exports, 18 for Eastern-time exports.
    Falls back to `default` (18) when no clear daily break is present.
    """
    if len(bars) < 2:
        return default
    idx = bars.index
    gaps = idx.to_series().diff()
    secs = gaps.dt.total_seconds()
    is_break = (secs >= 45 * 60) & (secs <= 5 * 3600)
    open_hours = idx[is_break.to_numpy()].hour
    if len(open_hours) == 0:
        return default
    return int(pd.Series(open_hours).mode().iloc[0])


def trade_session_date(index: pd.DatetimeIndex, open_hour: int) -> pd.DatetimeIndex:
    """Map each timestamp to the calendar date of its trading session.

    The evening session rolls into the next day (e.g. Sunday 18:00 belongs to
    Monday's session), so shifting forward by ``24 - open_hour`` hours and
    normalizing puts every bar on its session date.
    """
    return (index + pd.Timedelta(hours=24 - open_hour)).normalize()


def trading_week_windows(bars: pd.DataFrame, open_hour: int | None = None):
    """Split bars into the last completed and the current trading week.

    A trading week runs from the Sunday-evening open through Friday's close.
    Using the session date (see ``trade_session_date``), each bar is assigned a
    Monday-based week key; the week containing the final bar is "this week" and
    the week before it is "last week".

    Returns ``(last_week_bars, this_week_bars, info)`` where either frame may be
    empty (e.g. a contract that ends mid-week or lacks a full prior week near
    rollover) and ``info`` carries the detected open hour plus date ranges.
    """
    if open_hour is None:
        open_hour = detect_session_open_hour(bars)

    sd = trade_session_date(bars.index, open_hour)
    week_key = sd - pd.to_timedelta(sd.weekday, unit="D")  # Monday of the session week

    this_key = week_key[-1]
    last_key = this_key - pd.Timedelta(days=7)

    this_week = bars[week_key == this_key]
    last_week = bars[week_key == last_key]

    def _range(df: pd.DataFrame) -> str:
        if df.empty:
            return ""
        return f"{df.index[0]:%Y-%m-%d %H:%M} → {df.index[-1]:%Y-%m-%d %H:%M}"

    info = {
        "open_hour": open_hour,
        "this_range": _range(this_week),
        "last_range": _range(last_week),
    }
    return last_week, this_week, info


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
