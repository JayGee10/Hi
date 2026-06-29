"""Build a labeled training table from intraday OHLCV bars.

Each row is one *test* of a prior session's volume-profile level against the next
session, exactly as the backtest measures it:

    features (profile shape + level + context)  ->  label (hold = 1 / break = 0)

Unresolved touches (neither held nor broke within the lookahead) are dropped —
they have no label. The result is a pandas DataFrame ready for `train.py`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import sessions
from backtest import (
    LOOKAHEAD_MIN,
    SESSION,
    _confluent,
    _resolve_touch,
    bar_minutes,
    session_atr,
)
from shapes import classify_shape, shape_features
from volume_profile import Profile, build_session_profiles, profile_arrays


def _profile_shape(group: pd.DataFrame, profile: Profile, n_bins: int = 50):
    """Recompute the histogram for `profile`'s session and derive shape features."""
    centers, vol = profile_arrays(group, n_bins)
    feats = shape_features(centers, vol, profile)
    return feats, classify_shape(feats)


def _rows_for_timeframe(
    profiles: list[Profile],
    groups: dict,
    timeframe: str,
    atr: pd.Series,
    bar_min: float,
    weekly_lookup: dict | None,
) -> list[dict]:
    rows: list[dict] = []
    look = max(1, round(LOOKAHEAD_MIN[timeframe] / bar_min))
    session_keys = sorted(groups)
    med_atr = float(atr.median()) if len(atr) else 0.0

    for prev in profiles:
        later = [s for s in session_keys if s > prev.session]
        if not later:
            continue
        cur_key = later[0]
        cur = groups[cur_key]
        prev_group = groups.get(prev.session)
        if cur.empty or prev_group is None or prev_group.empty:
            continue

        a = float(atr.get(cur_key, med_atr))
        if not np.isfinite(a) or a <= 0:
            a = med_atr or 1.0

        feats, shape = _profile_shape(prev_group, prev)

        weekly = None
        if weekly_lookup is not None:
            wk = [w for w in sorted(weekly_lookup) if w < cur_key]
            if wk:
                weekly = weekly_lookup[wk[-1]]

        for name, level in (("POC", prev.poc), ("VAH", prev.vah), ("VAL", prev.val)):
            touched = cur[(cur["low"] <= level) & (cur["high"] >= level)]
            if touched.empty:
                continue
            pos = cur.index.get_loc(touched.index[0])
            pos = pos if isinstance(pos, int) else pos.start
            pre_close = cur["close"].iloc[pos - 1] if pos > 0 else cur["open"].iloc[pos]
            from_below = pre_close < level
            fwd = cur.iloc[pos + 1: pos + 1 + look]
            if fwd.empty:
                continue
            outcome, _ = _resolve_touch(fwd, level, from_below, a)
            if outcome not in ("hold", "break"):
                continue  # unresolved -> no label

            conf = _confluent(level, weekly, a) if timeframe == "daily" else False
            rows.append({
                "session": cur_key,   # timestamp of the session tested (for time-ordered splits)
                "timeframe": timeframe,
                "level_name": name,
                "shape": shape,
                **feats.as_dict(),
                "from_below": int(from_below),
                "confluent": int(conf),
                "label": int(outcome == "hold"),
            })
    return rows


def build_dataset(bars_et: pd.DataFrame, session: str = SESSION) -> pd.DataFrame:
    """Build the labeled feature table for one instrument's bars (ET index)."""
    bar_min = bar_minutes(bars_et)
    atr = session_atr(bars_et, session)
    daily = build_session_profiles(bars_et, session, "D")
    weekly = build_session_profiles(bars_et, session, "W")
    weekly_lookup = {p.session: p for p in weekly}
    dgroups = sessions.session_groups(bars_et, session, "D")
    wgroups = sessions.session_groups(bars_et, session, "W")

    rows = _rows_for_timeframe(daily, dgroups, "daily", atr, bar_min, weekly_lookup)
    rows += _rows_for_timeframe(weekly, wgroups, "weekly", atr, bar_min, None)
    return pd.DataFrame(rows)
