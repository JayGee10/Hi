"""Build a self-contained, phone-friendly HTML to explore Session Volume
Profiles for one or more contracts: interactive candles + volume profile, with
POC / value area and LVN boxes. A dropdown switches contracts.

Run:  python build_html.py out.html file1.txt [file2.txt ...]
"""

from __future__ import annotations

import json
import sys

import pandas as pd

import data as data_mod
from volume_profile import low_volume_nodes, profile_arrays, session_profile

N_BINS = 80
# label -> pandas resample rule. The volume profile is independent of these;
# only the candlesticks change when you switch timeframe.
TIMEFRAMES = {"30m": "30min", "1h": "1h", "4h": "4h", "1D": "1D"}
DEFAULT_TF = "1h"


def candles(bars, rule: str) -> dict:
    c = bars.resample(rule).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
    ).dropna()
    return {
        "t": c.index.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
        "o": c["open"].round(2).tolist(),
        "h": c["high"].round(2).tolist(),
        "l": c["low"].round(2).tolist(),
        "c": c["close"].round(2).tolist(),
    }


def contract_payload(path: str) -> dict:
    bars = data_mod.load_csv(path)
    centers, vol = profile_arrays(bars, N_BINS)
    prof = session_profile(bars, N_BINS)
    lvns = low_volume_nodes(centers, vol)

    stem = path.split("/")[-1].replace(".Last.txt", "").replace(".txt", "")
    name = stem.split("MNQ_")[-1] if "MNQ_" in stem else stem.split("-")[-1]
    rng = f"{bars.index[0]:%Y-%m-%d} → {bars.index[-1]:%Y-%m-%d}"
    return {
        "name": name,
        "range": rng,
        "tf": {label: candles(bars, rule) for label, rule in TIMEFRAMES.items()},
        "vp_price": [round(float(x), 2) for x in centers],
        "vp_vol": [round(float(x), 1) for x in vol],
        "poc": round(prof.poc, 2),
        "vah": round(prof.vah, 2),
        "val": round(prof.val, 2),
        "lvns": [[round(lo, 2), round(hi, 2)] for lo, hi in lvns],
    }


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>MNQ Session Volume Profile</title>
__PLOTLY_JS__
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #ffffff; color: #1f2328;
         font-family: -apple-system, system-ui, sans-serif; }
  header { padding: 10px 12px; position: sticky; top: 0; background: #ffffff;
           border-bottom: 1px solid #d0d7de; z-index: 5; }
  h1 { font-size: 16px; margin: 0 0 8px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  select { background: #ffffff; color: #1f2328; border: 1px solid #d0d7de;
           border-radius: 8px; padding: 8px 10px; font-size: 15px; }
  #pick { flex: 1; }
  #tf { flex: 0 0 auto; }
  .meta { font-size: 12px; color: #57606a; }
  .legend { font-size: 11px; color: #57606a; padding: 6px 12px; }
  .sw { display:inline-block; width:10px; height:10px; border-radius:2px;
        margin: 0 4px 0 10px; vertical-align: middle; }
  #chart { width: 100%; }
</style>
</head>
<body>
<header>
  <h1>MNQ — Session Volume Profile</h1>
  <div class="row">
    <select id="pick"></select>
    <select id="tf"></select>
  </div>
  <div class="meta" id="meta"></div>
</header>
<div class="legend">
  <span class="sw" style="background:#1f6feb"></span>POC / value area
  <span class="sw" style="background:#d1242f"></span>LVN (low-volume node)
  <span class="sw" style="background:#2da44e"></span>up
  <span class="sw" style="background:#cf222e"></span>down
</div>
<div id="chart"></div>
<script>
const DATA = __DATA__;
const POC="#1f6feb", LVN="#d1242f";

function hline(y, color, width, dash){
  return {type:"line", xref:"paper", x0:0, x1:1, yref:"y", y0:y, y1:y,
          line:{color:color, width:width, dash:dash||"solid"}, layer:"above"};
}

function draw(key, tf){
  const d = DATA[key];
  const k = d.tf[tf] ? tf : Object.keys(d.tf)[0];
  const bars = d.tf[k];
  document.getElementById("meta").textContent = d.range + "  ·  " + k +
      "  ·  POC " + d.poc + "  ·  VA " + d.val + "–" + d.vah +
      "  ·  " + d.lvns.length + " LVN zones";

  const candle = {type:"candlestick", x:bars.t, open:bars.o, high:bars.h,
    low:bars.l, close:bars.c, xaxis:"x", yaxis:"y", name:"price",
    increasing:{line:{color:"#2da44e"}}, decreasing:{line:{color:"#cf222e"}}};

  const colors = d.vp_price.map(p => (p>=d.val && p<=d.vah) ? "#1f6feb" : "#b1b8c0");
  const vp = {type:"bar", orientation:"h", x:d.vp_vol, y:d.vp_price,
    xaxis:"x2", yaxis:"y", marker:{color:colors}, opacity:0.85,
    name:"volume", hoverinfo:"skip"};

  const shapes = [
    {type:"rect", xref:"paper", x0:0, x1:1, yref:"y", y0:d.val, y1:d.vah,
     fillcolor:"#1f6feb", opacity:0.06, line:{width:0}, layer:"below"},
    hline(d.poc, POC, 2), hline(d.vah, POC, 1, "dash"), hline(d.val, POC, 1, "dash"),
  ];
  d.lvns.forEach(b => shapes.push(
    {type:"rect", xref:"paper", x0:0, x1:1, yref:"y", y0:b[0], y1:b[1],
     fillcolor:LVN, opacity:0.13, line:{color:LVN, width:1, dash:"dot"}, layer:"below"}));

  const ann = [
    {xref:"paper", x:0.005, y:d.poc, yref:"y", text:"POC", showarrow:false,
     font:{color:POC, size:11}, bgcolor:"#ffffff", xanchor:"left"},
    {xref:"paper", x:0.005, y:d.vah, yref:"y", text:"VAH", showarrow:false,
     font:{color:POC, size:10}, bgcolor:"#ffffff", xanchor:"left"},
    {xref:"paper", x:0.005, y:d.val, yref:"y", text:"VAL", showarrow:false,
     font:{color:POC, size:10}, bgcolor:"#ffffff", xanchor:"left"},
  ];

  const layout = {
    height: Math.max(420, Math.round(window.innerHeight * 0.78)),
    margin: {l:48, r:8, t:8, b:28},
    paper_bgcolor:"#ffffff", plot_bgcolor:"#ffffff",
    font:{color:"#1f2328", size:11},
    showlegend:false, dragmode:"pan",
    xaxis:{domain:[0,0.80], type:"date", rangeslider:{visible:false},
           gridcolor:"#e1e4e8", showspikes:true, spikethickness:1},
    xaxis2:{domain:[0.82,1.0], anchor:"y", showgrid:false, zeroline:false,
            showticklabels:false},
    yaxis:{anchor:"x", side:"left", gridcolor:"#e1e4e8", showspikes:true,
           spikethickness:1, tickformat:","},
    shapes:shapes, annotations:ann,
  };
  Plotly.react("chart", [vp, candle], layout,
    {responsive:true, scrollZoom:true, displayModeBar:false});
}

const sel = document.getElementById("pick");
Object.keys(DATA).forEach(k => {
  const o = document.createElement("option");
  o.value = k; o.textContent = "MNQ " + k + "  (" + DATA[k].range + ")";
  sel.appendChild(o);
});

const tfSel = document.getElementById("tf");
const anyTf = DATA[Object.keys(DATA)[0]].tf;
Object.keys(anyTf).forEach(k => {
  const o = document.createElement("option");
  o.value = k; o.textContent = k;
  tfSel.appendChild(o);
});

function render(){ draw(sel.value, tfSel.value); }
sel.addEventListener("change", render);
tfSel.addEventListener("change", render);
window.addEventListener("resize", render);

sel.value = DATA["0626"] ? "0626" : Object.keys(DATA)[0];
tfSel.value = "__DEFAULT_TF__";
render();
</script>
</body>
</html>
"""


def plotly_script() -> str:
    """Inline the bundled Plotly JS for a fully offline file; fall back to CDN."""
    try:
        import plotly
        import pathlib
        js = next(pathlib.Path(plotly.__file__).parent.rglob("plotly*.min.js"))
        return "<script>" + js.read_text() + "</script>"
    except Exception:
        return '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'


def main(argv):
    out = argv[1]
    paths = argv[2:]
    payload = {}
    for p in paths:
        d = contract_payload(p)
        payload[d["name"]] = d
        print(f"  {d['name']}: {d['range']}  "
              f"{len(d['tf'][DEFAULT_TF]['t'])} {DEFAULT_TF}-candles, {len(d['lvns'])} LVN")
    html = (TEMPLATE
            .replace("__PLOTLY_JS__", plotly_script())
            .replace("__DEFAULT_TF__", DEFAULT_TF)
            .replace("__DATA__", json.dumps(payload)))
    with open(out, "w") as fh:
        fh.write(html)
    print(f"wrote {out}  ({len(html)/1024:.0f} KB, {len(payload)} contracts)")


if __name__ == "__main__":
    main(sys.argv)
