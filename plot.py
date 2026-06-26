"""Visualize a Session Volume Profile: price candles + volume-at-price, with
POC / value area drawn and Low-Volume Nodes (LVNs) highlighted as boxes.

Run:  python plot.py                       # synthetic data -> profile.png
      python plot.py data.txt              # your file
      python plot.py data.txt out.png 15   # file, output, last N sessions
"""

from __future__ import annotations

import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle

import data as data_mod
from volume_profile import low_volume_nodes, profile_arrays, session_profile

POC_C = "#1f6feb"
VA_C = "#1f6feb"
LVN_C = "#d1242f"
UP_C = "#2da44e"
DN_C = "#cf222e"


def plot_profile(bars, title, out_path, last_sessions=10, n_bins=60, candle="30min"):
    days = sorted(set(bars.index.normalize()))
    window_days = set(days[-last_sessions:])
    w = bars[bars.index.normalize().isin(window_days)]

    centers, vol = profile_arrays(w, n_bins)
    prof = session_profile(w, n_bins)
    lvns = low_volume_nodes(centers, vol)
    step = float(centers[1] - centers[0])

    # candles for the price panel
    c = w.resample(candle).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
    ).dropna()
    x = np.arange(len(c))

    fig, (axp, axv) = plt.subplots(
        1, 2, figsize=(14, 8), sharey=True,
        gridspec_kw={"width_ratios": [4, 1], "wspace": 0.02},
    )

    # --- price candles ---
    for xi, (_, row) in zip(x, c.iterrows()):
        up = row["close"] >= row["open"]
        col = UP_C if up else DN_C
        axp.vlines(xi, row["low"], row["high"], color=col, linewidth=0.7)
        lo = min(row["open"], row["close"])
        axp.add_patch(Rectangle((xi - 0.3, lo), 0.6, abs(row["close"] - row["open"]) or step * 0.05,
                                facecolor=col, edgecolor=col))

    # value area + POC
    axp.axhspan(prof.val, prof.vah, color=VA_C, alpha=0.07, zorder=0)
    axp.axhline(prof.poc, color=POC_C, lw=1.4, ls="-")
    for lvl, name in ((prof.vah, "VAH"), (prof.val, "VAL")):
        axp.axhline(lvl, color=VA_C, lw=0.8, ls="--", alpha=0.6)
        axp.text(x[0] + 0.5, lvl, name, va="center", ha="left", color=VA_C,
                 fontsize=8, clip_on=False,
                 bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.5))
    axp.text(x[0] + 0.5, prof.poc, "POC", va="center", ha="left", color=POC_C,
             fontsize=9, fontweight="bold", clip_on=False,
             bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.5))

    # --- volume profile ---
    in_va = (centers >= prof.val) & (centers <= prof.vah)
    axv.barh(centers, vol, height=step * 0.9,
             color=np.where(in_va, VA_C, "#9aa0a6"), alpha=0.8)
    axv.set_xlabel("volume")
    axv.set_xticks([])

    # --- LVN boxes across both panels ---
    for j, (lo, hi) in enumerate(lvns):
        for ax, xspan in ((axp, (x[0] - 0.5, len(c))), (axv, (0, vol.max()))):
            ax.add_patch(Rectangle(
                (xspan[0], lo), xspan[1], hi - lo,
                facecolor=LVN_C, alpha=0.13, edgecolor=LVN_C, lw=1.2,
                ls="--", zorder=3,
                label="LVN (low-volume node)" if j == 0 and ax is axp else None,
            ))

    # x axis: date labels at session starts
    starts, labels = [], []
    prev = None
    for xi, ts in zip(x, c.index):
        d = ts.normalize()
        if d != prev:
            starts.append(xi)
            labels.append(d.strftime("%m-%d"))
            prev = d
    axp.set_xticks(starts)
    axp.set_xticklabels(labels, rotation=45, fontsize=8)
    axp.set_xlim(x[0] - 0.5, x[-1] + 0.5)

    axp.set_title(title, fontsize=12, fontweight="bold", loc="left")
    axp.set_ylabel("price")
    axp.legend(loc="upper left", fontsize=8, framealpha=0.9)
    axp.grid(axis="y", alpha=0.15)
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    print(f"saved {out_path}  ({len(lvns)} LVN zones, POC={prof.poc:.1f}, "
          f"VA={prof.val:.1f}-{prof.vah:.1f})")


def main(argv):
    path = argv[1] if len(argv) > 1 else None
    out = argv[2] if len(argv) > 2 else "profile.png"
    last = int(argv[3]) if len(argv) > 3 else 10
    if path:
        bars = data_mod.load_csv(path)
        title = path.split("/")[-1]
    else:
        bars = data_mod.synthetic()
        title = "synthetic"
    plot_profile(bars, title, out, last_sessions=last)


if __name__ == "__main__":
    main(sys.argv)
