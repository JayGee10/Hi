"""CME session boundaries for intraday analysis.

Raw data timestamps are UTC; we trade/think in Eastern, so everything is
converted to America/New_York (DST-aware via stdlib zoneinfo through pandas).

- RTH  = regular cash session, 09:30-16:00 ET (the intraday index-futures
         session most levels are read from).
- ETH  = the full electronic Globex day, which rolls at 18:00 ET.
"""

from __future__ import annotations

from datetime import time

import pandas as pd

EASTERN = "America/New_York"
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
ETH_ROLL_HOURS = 6  # Globex reopens 18:00 ET; +6h moves that boundary to midnight


def to_eastern(bars: pd.DataFrame, src_tz: str = "UTC") -> pd.DataFrame:
    """Return bars with a tz-aware Eastern index (DST handled automatically)."""
    out = bars.copy()
    idx = out.index
    if idx.tz is None:
        idx = idx.tz_localize(src_tz)
    out.index = idx.tz_convert(EASTERN)
    return out


def assume_eastern(bars: pd.DataFrame) -> pd.DataFrame:
    """Treat naive timestamps as already being Eastern wall-clock (synthetic)."""
    out = bars.copy()
    out.index = out.index.tz_localize(EASTERN) if out.index.tz is None else out.index
    return out


def rth_bars(bars_et: pd.DataFrame) -> pd.DataFrame:
    """Keep only Regular Trading Hours bars (09:30-16:00 ET)."""
    return bars_et.between_time(RTH_OPEN, RTH_CLOSE, inclusive="left")


def _trade_dates(bars_et: pd.DataFrame, session: str) -> pd.DatetimeIndex:
    """tz-naive normalized trade-date for each bar of `bars_et`."""
    if session == "RTH":
        return bars_et.index.tz_localize(None).normalize()
    if session == "ETH":
        shifted = bars_et.index + pd.Timedelta(hours=ETH_ROLL_HOURS)
        return shifted.tz_localize(None).normalize()
    raise ValueError(f"unknown session {session!r}")


def _ensure_et(bars: pd.DataFrame, src_tz: str) -> pd.DataFrame:
    return bars if bars.index.tz is not None else to_eastern(bars, src_tz)


def iter_sessions(bars: pd.DataFrame, session: str = "RTH", freq: str = "D",
                  src_tz: str = "UTC"):
    """Yield (session_key, sub_df) per trading session.

    `freq` is 'D' (one session per trading day) or 'W' (Monday-anchored week).
    Replaces calendar `pd.Grouper(freq=...)` grouping. For RTH, only RTH bars
    are included.
    """
    b = _ensure_et(bars, src_tz)
    if session == "RTH":
        b = rth_bars(b)
    day = _trade_dates(b, session)
    if freq == "D":
        keys = day
    elif freq == "W":
        keys = day - pd.to_timedelta(day.weekday, unit="D")  # Monday of that week
    else:
        raise ValueError(f"unknown freq {freq!r}")
    for key, sub in b.groupby(keys):
        yield pd.Timestamp(key), sub


def session_groups(bars: pd.DataFrame, session: str = "RTH", freq: str = "D",
                   src_tz: str = "UTC") -> dict:
    """Dict {session_key: sub_df} for the requested session grouping."""
    return {k: sub for k, sub in iter_sessions(bars, session, freq, src_tz)}
