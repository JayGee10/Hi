"""Backtest: how does price test Session Volume Profile levels?

Sessions are the real CME trading sessions in Eastern time (see sessions.py),
not calendar days. For each session we take the *previous* session's profile
levels (POC, VAH, VAL) and measure how price interacts with them in the *next*
session:

  - touch     : price trades into the level
  - hold      : level rejects price (acts as support/resistance)
  - break     : price accepts through the level
  - reaction  : favorable excursion (in ATR) after a hold

We also measure the "confluence edge": do daily levels hold more often when a
weekly level sits right on top of them?

Run:  python backtest.py                  # synthetic data
      python backtest.py data.txt         # one intraday OHLCV file (UTC stamps)
      python backtest.py a.txt b.txt ...  # many files, pooled COMBINED summary
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

import data as data_mod
import sessions
from volume_profile import Profile, build_session_profiles


# --- tunable test parameters -------------------------------------------------
SESSION = "RTH"      # "RTH" (09:30-16:00 ET cash session) or "ETH" (full Globex)
BREAK_ATR = 0.5      # close this far past a level (in ATR) => break/acceptance
REACTION_ATR = 0.5   # move this far back off a level (in ATR) => hold/rejection
CONFLUENCE_ATR = 0.25  # daily & weekly level within this distance => confluence
# How long a touch has to resolve, in MINUTES (auto-scaled to the file's bar
# size). Daily ~= one RTH session (390 min); weekly ~= a few sessions.
LOOKAHEAD_MIN = {"daily": 390, "weekly": 1440}


@dataclass
class TestResult:
    timeframe: str
    level_name: str
    level: float
    outcome: str          # 'hold' | 'break' | 'unresolved'
    reaction_atr: float    # favorable excursion if hold, else 0
    confluent: bool


def bar_minutes(bars: pd.DataFrame) -> float:
    """Infer the bar interval in minutes (ignoring overnight/session gaps)."""
    secs = bars.index.to_series().diff().dt.total_seconds()
    intraday = secs[(secs > 0) & (secs <= 3600)]
    if intraday.empty:
        return 1.0
    return max(1.0, float(intraday.median()) / 60.0)


def session_atr(bars_et: pd.DataFrame, session: str, n: int = 14) -> pd.Series:
    """ATR(n) computed on per-session daily bars, indexed by session key."""
    groups = sessions.session_groups(bars_et, session, "D")
    if not groups:
        return pd.Series(dtype=float)
    keys = sorted(groups)
    df = pd.DataFrame({
        "high": {k: groups[k]["high"].max() for k in keys},
        "low": {k: groups[k]["low"].min() for k in keys},
        "close": {k: groups[k]["close"].iloc[-1] for k in keys},
    }).sort_index()
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(),
         (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=1).mean()


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
    profiles: list[Profile],
    groups: dict,
    timeframe: str,
    atr: pd.Series,
    bar_min: float,
    weekly_lookup: dict | None = None,
) -> list[TestResult]:
    """Test each session's price action against the *previous* session's levels.

    `profiles` and `groups` share the same session keys; `groups[key]` holds the
    bars of that session.
    """
    results: list[TestResult] = []
    look = max(1, round(LOOKAHEAD_MIN[timeframe] / bar_min))
    session_keys = sorted(groups)
    med_atr = float(atr.median()) if len(atr) else 0.0

    for prev in profiles:
        later = [s for s in session_keys if s > prev.session]
        if not later:
            continue
        cur_key = later[0]
        cur = groups[cur_key]
        if cur.empty:
            continue

        a = float(atr.get(cur_key, med_atr))
        if not np.isfinite(a) or a <= 0:
            a = med_atr or 1.0

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
            outcome, reaction = _resolve_touch(fwd, level, from_below, a)
            conf = _confluent(level, weekly, a) if timeframe == "daily" else False
            results.append(TestResult(timeframe, name, level, outcome, reaction, conf))
    return results


def summarize(results: list[TestResult], timeframe: str) -> None:
    rows = [r for r in results if r.timeframe == timeframe]
    resolved = [r for r in rows if r.outcome in ("hold", "break")]
    print(f"\n=== {timeframe.upper()} {SESSION} session VP — level tests ===")
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


def run_one(bars_et: pd.DataFrame) -> list[TestResult]:
    """Build RTH session profiles and run daily + weekly level tests."""
    bar_min = bar_minutes(bars_et)
    atr = session_atr(bars_et, SESSION)
    daily_profiles = build_session_profiles(bars_et, SESSION, "D")
    weekly_profiles = build_session_profiles(bars_et, SESSION, "W")
    weekly_lookup = {p.session: p for p in weekly_profiles}
    dgroups = sessions.session_groups(bars_et, SESSION, "D")
    wgroups = sessions.session_groups(bars_et, SESSION, "W")
    print(f"built {len(daily_profiles)} {SESSION} daily and {len(weekly_profiles)} "
          f"weekly sessions ({bar_min:.0f}-min bars)")

    results: list[TestResult] = []
    results += test_timeframe(daily_profiles, dgroups, "daily", atr, bar_min, weekly_lookup)
    results += test_timeframe(weekly_profiles, wgroups, "weekly", atr, bar_min)
    return results


def report(results: list[TestResult]) -> None:
    summarize(results, "daily")
    summarize(results, "weekly")
    summarize_confluence(results)


def main(argv: list[str]) -> None:
    paths = argv[1:]
    if not paths:
        print("no CSV given — using synthetic intraday data")
        bars_et = sessions.assume_eastern(data_mod.synthetic())
        print(f"{len(bars_et):,} bars  {bars_et.index[0]} -> {bars_et.index[-1]}")
        report(run_one(bars_et))
        return

    pooled: list[TestResult] = []
    for path in paths:
        name = path.split("/")[-1]
        print(f"\n############## {name} ##############")
        bars_et = sessions.to_eastern(data_mod.load_csv(path), "UTC")
        print(f"{len(bars_et):,} bars  {bars_et.index[0]} -> {bars_et.index[-1]} (ET)")
        results = run_one(bars_et)
        report(results)
        pooled += results

    if len(paths) > 1:
        print(f"\n############## COMBINED ({len(paths)} files) ##############")
        report(pooled)


if __name__ == "__main__":
    main(sys.argv)
