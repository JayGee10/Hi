"""Session Volume Profile computation.

Builds a per-session volume profile from intraday OHLCV bars and extracts the
key levels traders watch: POC, Value Area High (VAH) and Value Area Low (VAL).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Profile:
    """Key levels for one session's volume profile."""

    session: pd.Timestamp  # session label (start of day / week)
    poc: float             # Point of Control - price with most volume
    vah: float             # Value Area High
    val: float             # Value Area Low
    high: float            # session high
    low: float             # session low
    total_volume: float


def _bin_edges(low: float, high: float, n_bins: int) -> np.ndarray:
    if high <= low:
        high = low + 1e-9
    return np.linspace(low, high, n_bins + 1)


def session_profile(
    bars: pd.DataFrame,
    n_bins: int = 50,
    value_area_pct: float = 0.70,
) -> Profile | None:
    """Compute a volume profile for a single session's intraday bars.

    Each bar's volume is spread uniformly across the price bins its high-low
    range overlaps. This is the standard approximation when tick data isn't
    available.

    `bars` must have columns: high, low, volume (and a DatetimeIndex).
    """
    if bars.empty:
        return None

    s_low = float(bars["low"].min())
    s_high = float(bars["high"].max())
    edges = _bin_edges(s_low, s_high, n_bins)
    centers = (edges[:-1] + edges[1:]) / 2.0
    vol_at_price = np.zeros(n_bins)

    for high, low, vol in zip(bars["high"].values, bars["low"].values, bars["volume"].values):
        if vol <= 0:
            continue
        # bins the bar's range overlaps
        lo_idx = np.searchsorted(edges, low, side="right") - 1
        hi_idx = np.searchsorted(edges, high, side="right") - 1
        lo_idx = max(0, min(lo_idx, n_bins - 1))
        hi_idx = max(0, min(hi_idx, n_bins - 1))
        span = hi_idx - lo_idx + 1
        vol_at_price[lo_idx:hi_idx + 1] += vol / span

    total = vol_at_price.sum()
    if total <= 0:
        return None

    poc_idx = int(np.argmax(vol_at_price))
    poc = float(centers[poc_idx])

    # Expand the value area outward from the POC until it holds the target %.
    target = total * value_area_pct
    captured = vol_at_price[poc_idx]
    lo = hi = poc_idx
    while captured < target and (lo > 0 or hi < n_bins - 1):
        below = vol_at_price[lo - 1] if lo > 0 else -1.0
        above = vol_at_price[hi + 1] if hi < n_bins - 1 else -1.0
        if above >= below:
            hi += 1
            captured += vol_at_price[hi]
        else:
            lo -= 1
            captured += vol_at_price[lo]

    return Profile(
        session=bars.index[0],
        poc=poc,
        vah=float(centers[hi]),
        val=float(centers[lo]),
        high=s_high,
        low=s_low,
        total_volume=float(total),
    )


def build_profiles(bars: pd.DataFrame, rule: str, **kwargs) -> list[Profile]:
    """Build one profile per session.

    `rule` is a pandas resample/grouping rule: 'D' for daily sessions,
    'W' for weekly sessions.
    """
    profiles: list[Profile] = []
    for _, group in bars.groupby(pd.Grouper(freq=rule)):
        p = session_profile(group, **kwargs)
        if p is not None:
            profiles.append(p)
    return profiles
