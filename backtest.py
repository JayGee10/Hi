"""Backtest: how does price test Session Volume Profile levels?

For each timeframe (daily, weekly) we take the *prior* session's profile levels
(POC, VAH, VAL) and measure how price interacts with them in the *next* session:

  - touch     : price trades into the level
  - hold      : level rejects price (acts as support/resistance)
  - break     : price accepts through the level
  - reaction  : favorable excursion (in ATR) after a hold

We also measure the "confluence edge": do daily levels hold more often when a
weekly level sits right on top of them?

Run:  python backtest.py            # synthetic data
      python backtest.py data.csv   # your own intraday OHLCV
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

import data as data_mod
from volume_profile import Profile, build_profiles


# --- tunable test parameters -------------------------------------------------
BREAK_ATR = 0.5      # close this far past a level (in ATR) => break/acceptance
REACTION_ATR = 0.5   # move this far back off a level (in ATR) => hold/rejection
CONFLUENCE_ATR = 0.25  # daily & weekly level within this distance => confluence
LOOKAHEAD = {"daily": 24, "weekly": 78}  # bars to resolve a touch


@dataclass
class TestResult:
    timeframe: str
    level_name: str
    level: float
    outcome: str          # 'hold' | 'break' | 'unresolved'
    reaction_atr: float    # favorable excursion if hold, else 0
    confluent: bool


def daily_atr(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    """ATR(n) on daily bars, indexed by date (normalized to midnight)."""
    d = bars.resample("D").agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last")
    ).dropna()
    prev_close = d["close"].shift(1)
    tr = pd.concat(
        [d["high"] - d["low"], (d["high"] - prev_close).abs(), (d["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(n, min_periods=1).mean()
    atr.index = atr.index.normalize()
    return atr


def _resolve_touch(
    fwd: pd.DataFrame, level: float, from_below: bool, atr: float
) -> tuple[str, float]:
    """Classify what happens after price touches `level`."""
    brk = BREAK_ATR * atr
    react = REACTION_ATR * atr
    if from_below:  # level acts as resistance; hold = rejection downward
        for _, bar in fwd.iterrows():
            if bar["close"] > level + brk:
                return "break", 0.0
            if level - bar["low"] >= react:
                return "hold", (level - bar["low"]) / atr
    else:           # level acts as support; hold = rejection upward
        for _, bar in fwd.iterrows():
            if bar["close"] < level - brk:
                return "break", 0.0
            if bar["high"] - level >= react:
                return "hold", (bar["high"] - level) / atr
    return "unresolved", 0.0


def _confluent(level: float, weekly: Profile | None, atr: float) -> bool:
    if weekly is None:
        return False
    return any(abs(level - lv) <= CONFLUENCE_ATR * atr for lv in (weekly.poc, weekly.vah, weekly.val))


def test_timeframe(
    bars: pd.DataFrame,
    profiles: list[Profile],
    timeframe: str,
    atr: pd.Series,
    weekly_lookup: dict | None = None,
) -> list[TestResult]:
    """Test each session's price action against the *previous* session's levels."""
    results: list[TestResult] = []
    look = LOOKAHEAD[timeframe]
    rule = "D" if timeframe == "daily" else "W"
    sessions = {s: g for s, g in bars.groupby(pd.Grouper(freq=rule))}
    session_keys = sorted(sessions.keys())

    for prev in profiles:
        # find the session immediately after the one this profile was built on
        later = [s for s in session_keys if s > prev.session]
        if not later:
            continue
        cur_key = later[0]
        cur = sessions[cur_key]
        if cur.empty:
            continue

        a = float(atr.get(cur_key.normalize(), atr.median()))
        if a <= 0:
            a = atr.median()

        # active weekly profile for confluence (most recent weekly before cur)
        weekly = None
        if weekly_lookup is not None:
            wk = [w for w in sorted(weekly_lookup) if w < cur_key]
            if wk:
                weekly = weekly_lookup[wk[-1]]

        for name, level in (("POC", prev.poc), ("VAH", prev.vah), ("VAL", prev.val)):
            # first bar that trades into the level
            touched = cur[(cur["low"] <= level) & (cur["high"] >= level)]
            if touched.empty:
                continue
            t_idx = cur.index.get_loc(touched.index[0])
            pos = t_idx if isinstance(t_idx, int) else t_idx.start
            pre_close = cur["close"].iloc[pos - 1] if pos > 0 else cur["open"].iloc[pos]
            from_below = pre_close < level
            fwd = cur.iloc[pos + 1: pos + 1 + look]
            if fwd.empty:
                continue
            outcome, reaction = _resolve_touch(fwd, level, from_below, a)
            conf = _confluent(level, weekly, a) if timeframe == "daily" else False
            results.append(TestResult(timeframe, name, level, outcome, reaction, conf))
    return results


def summarize(results: list[TestResult], timeframe: str) -> None:
    rows = [r for r in results if r.timeframe == timeframe]
    resolved = [r for r in rows if r.outcome in ("hold", "break")]
    print(f"\n=== {timeframe.upper()} session VP — level tests ===")
    print(f"touches: {len(rows)}   resolved: {len(resolved)}   "
          f"unresolved: {len(rows) - len(resolved)}")
    if resolved:
        holds = [r for r in resolved if r.outcome == "hold"]
        rate = len(holds) / len(resolved)
        avg_react = np.mean([r.reaction_atr for r in holds]) if holds else 0.0
        print(f"hold rate: {rate:5.1%}   avg reaction on hold: {avg_react:.2f} ATR")

    for name in ("POC", "VAH", "VAL"):
        sub = [r for r in resolved if r.level_name == name]
        if sub:
            hr = sum(r.outcome == "hold" for r in sub) / len(sub)
            print(f"  {name}: {hr:5.1%} hold  ({len(sub)} tests)")


def summarize_confluence(results: list[TestResult]) -> None:
    daily = [r for r in results if r.timeframe == "daily" and r.outcome in ("hold", "break")]
    conf = [r for r in daily if r.confluent]
    non = [r for r in daily if not r.confluent]
    print("\n=== Confluence edge (daily level backed by a weekly level) ===")
    for label, grp in (("with weekly confluence", conf), ("without", non)):
        if grp:
            hr = sum(r.outcome == "hold" for r in grp) / len(grp)
            print(f"  {label:24s}: {hr:5.1%} hold  ({len(grp)} tests)")
    if conf and non:
        edge = (sum(r.outcome == "hold" for r in conf) / len(conf)) - (
            sum(r.outcome == "hold" for r in non) / len(non))
        print(f"  edge from confluence    : {edge:+.1%}")


def main(argv: list[str]) -> None:
    if len(argv) > 1:
        print(f"loading {argv[1]} ...")
        bars = data_mod.load_csv(argv[1])
    else:
        print("no CSV given — using synthetic intraday data")
        bars = data_mod.synthetic()
    print(f"{len(bars):,} bars  {bars.index[0]} -> {bars.index[-1]}")

    atr = daily_atr(bars)
    daily_profiles = build_profiles(bars, "D")
    weekly_profiles = build_profiles(bars, "W")
    weekly_lookup = {p.session: p for p in weekly_profiles}
    print(f"built {len(daily_profiles)} daily and {len(weekly_profiles)} weekly profiles")

    results: list[TestResult] = []
    results += test_timeframe(bars, daily_profiles, "daily", atr, weekly_lookup)
    results += test_timeframe(bars, weekly_profiles, "weekly", atr)

    summarize(results, "daily")
    summarize(results, "weekly")
    summarize_confluence(results)


if __name__ == "__main__":
    main(sys.argv)
