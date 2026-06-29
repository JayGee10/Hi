"""Volume-profile *shape* classification.

The shape of a session's volume-at-price histogram carries information about who
was in control and how price is likely to behave around the resulting levels.
The four canonical shapes (plus a trend/thin profile) are:

- **D** – balanced / normal distribution. POC sits mid-range and volume is
  roughly symmetric around it. Two-sided auction => balance/acceptance; price
  tends to mean-revert back toward the POC.
- **P** – POC high in the range with a thin tail trailing *below* it. Typically
  short-covering or accumulation after a move up (bullish lean). The fat node
  acts as support; the thin lower tail is a "rejected" zone.
- **b** – mirror of P: POC low in the range with a thin tail *above*. Long
  liquidation / distribution after a move down (bearish lean). The fat node acts
  as resistance.
- **B** – double distribution: two separate high-volume nodes split by a
  low-volume node (LVN). Two balance areas stacked; the LVN between them is the
  decision point price accepts or rejects.
- **trend** – elongated/thin profile with no dominant node (value area spans
  most of the range). One-timeframe directional day.

`shape_features` returns the numeric features behind the call so they can feed an
ML model; `classify_shape` returns the human-readable label.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from volume_profile import Profile


@dataclass
class ShapeFeatures:
    """Numeric descriptors of a volume profile's shape (model features)."""

    poc_pos: float        # POC location within [low, high], 0=low .. 1=high
    va_pos: float         # value-area midpoint location within the range
    va_width: float       # (VAH-VAL)/(high-low): how tight the value area is
    skew: float           # volume-weighted price skew (+ = mass low, - = mass high)
    n_modes: int          # number of prominent peaks in the histogram
    valley_ratio: float   # deepest inter-peak valley / lower peak (B-shape cue)
    concentration: float  # POC-bin volume / total (how peaked the profile is)

    def as_dict(self) -> dict:
        return asdict(self)


def _normalize(vol: np.ndarray) -> np.ndarray:
    total = vol.sum()
    return vol / total if total > 0 else vol


def _smooth(vol: np.ndarray, w: int = 3) -> np.ndarray:
    """Light moving-average smoothing so peak detection ignores single-bin noise."""
    if w <= 1 or len(vol) < w:
        return vol
    kernel = np.ones(w) / w
    return np.convolve(vol, kernel, mode="same")


def _find_peaks(vol: np.ndarray, rel_prominence: float = 0.20) -> list[int]:
    """Indices of local maxima at least `rel_prominence` of the global max."""
    if len(vol) == 0 or vol.max() <= 0:
        return []
    thresh = vol.max() * rel_prominence
    peaks: list[int] = []
    for i in range(len(vol)):
        left = vol[i - 1] if i > 0 else -np.inf
        right = vol[i + 1] if i < len(vol) - 1 else -np.inf
        if vol[i] >= left and vol[i] >= right and vol[i] >= thresh:
            peaks.append(i)
    # merge adjacent plateau peaks (keep the first of a run)
    merged: list[int] = []
    for p in peaks:
        if merged and p - merged[-1] == 1:
            continue
        merged.append(p)
    return merged


def shape_features(
    centers: np.ndarray, vol: np.ndarray, profile: Profile
) -> ShapeFeatures:
    """Compute shape descriptors from a profile histogram and its levels."""
    rng = profile.high - profile.low
    rng = rng if rng > 0 else 1e-9

    poc_pos = (profile.poc - profile.low) / rng
    va_mid = (profile.vah + profile.val) / 2.0
    va_pos = (va_mid - profile.low) / rng
    va_width = (profile.vah - profile.val) / rng

    p = _normalize(vol)
    mean = float((centers * p).sum())
    var = float((p * (centers - mean) ** 2).sum())
    std = np.sqrt(var) if var > 0 else 0.0
    skew = float((p * (centers - mean) ** 3).sum() / std**3) if std > 0 else 0.0
    # Orient skew so + means the volume mass leans toward LOW prices (b-ish) and
    # - means it leans HIGH (P-ish), which is the intuitive reading.
    skew = -skew

    sm = _smooth(vol)
    peaks = _find_peaks(sm)
    n_modes = len(peaks)

    valley_ratio = 0.0
    if n_modes >= 2:
        # deepest valley between the two strongest peaks, relative to the
        # weaker peak — a low ratio means a real LVN split (double distribution)
        order = sorted(peaks, key=lambda i: sm[i], reverse=True)[:2]
        a, b = sorted(order)
        valley = sm[a:b + 1].min()
        weaker = min(sm[a], sm[b])
        valley_ratio = float(valley / weaker) if weaker > 0 else 1.0

    concentration = float(vol.max() / vol.sum()) if vol.sum() > 0 else 0.0

    return ShapeFeatures(
        poc_pos=float(poc_pos),
        va_pos=float(va_pos),
        va_width=float(va_width),
        skew=skew,
        n_modes=int(n_modes),
        valley_ratio=valley_ratio,
        concentration=concentration,
    )


def classify_shape(f: ShapeFeatures) -> str:
    """Map shape features to one of: 'D', 'P', 'b', 'B', 'trend'."""
    # Double distribution first: 2+ modes with a genuine LVN split between them.
    if f.n_modes >= 2 and f.valley_ratio <= 0.5:
        return "B"
    # Thin / trend: value area spans most of the range and nothing is peaked
    # (a uniform profile's value area is already ~70% of the range, so the
    # distinguishing cue is the lack of any concentration at a single price).
    if f.va_width >= 0.60 and f.concentration < 0.05:
        return "trend"
    # Single distribution: read where the fat node sits in the range.
    if f.poc_pos >= 0.60:
        return "P"      # node high, thin tail below -> bullish lean
    if f.poc_pos <= 0.40:
        return "b"      # node low, thin tail above -> bearish lean
    return "D"          # node mid-range -> balance


SHAPE_BIAS = {
    "D": "balance (mean-revert to POC)",
    "P": "bullish (short-covering / accumulation)",
    "b": "bearish (long liquidation / distribution)",
    "B": "double distribution (LVN is the decision point)",
    "trend": "directional / one-timeframe day",
}
