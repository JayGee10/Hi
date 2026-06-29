"""Sanity tests for volume-profile shape classification.

Run:  python test_shapes.py   (no test framework required)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from shapes import classify_shape, shape_features
from volume_profile import profile_arrays, session_profile


def _bars_from_volume(vol_curve: np.ndarray) -> pd.DataFrame:
    """Build synthetic bars whose volume-at-price matches `vol_curve`."""
    centers = np.linspace(100.0, 110.0, len(vol_curve))
    rows = []
    for c, v in zip(centers, vol_curve):
        rows.append((pd.Timestamp("2024-01-01"), c, c + 0.05, c - 0.05, c, max(v, 0.0)))
    return pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    ).set_index("timestamp")


def _label(vol_curve: np.ndarray) -> str:
    df = _bars_from_volume(vol_curve * 1000.0)
    prof = session_profile(df)
    centers, vol = profile_arrays(df)
    return classify_shape(shape_features(centers, vol, prof))


def main() -> None:
    x = np.linspace(0.0, 1.0, 50)
    cases = {
        "D": np.exp(-((x - 0.50) / 0.12) ** 2),                       # mid bump
        "P": np.exp(-((x - 0.80) / 0.10) ** 2) + 0.15,                # node high
        "b": np.exp(-((x - 0.20) / 0.10) ** 2) + 0.15,                # node low
        "B": np.exp(-((x - 0.25) / 0.07) ** 2)                        # two nodes
             + np.exp(-((x - 0.75) / 0.07) ** 2),
        "trend": np.ones_like(x) + 0.05 * x,                          # flat/thin
    }
    failures = 0
    for expected, curve in cases.items():
        got = _label(curve)
        status = "OK" if got == expected else "FAIL"
        if got != expected:
            failures += 1
        print(f"  {expected:6s} -> {got:6s}  {status}")
    if failures:
        raise SystemExit(f"{failures} shape test(s) failed")
    print("all shape tests passed")


if __name__ == "__main__":
    main()
