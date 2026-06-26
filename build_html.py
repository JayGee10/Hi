"""Build a self-contained, phone-friendly HTML to explore Session Volume
Profiles for one or more MNQ contracts.

For each contract it carves out two trading-week windows — last completed week
(fixed range) and this developing week (anchored to the Sunday open) — and lets
you replay the candles one at a time while the volume profile (POC / value area
/ LVN boxes) rebuilds in step. Dropdowns switch contract, window and timeframe.

Run:  python build_html.py out.html file1.txt [file2.txt ...]
"""

from __future__ import annotations

import json
import sys

import data as data_mod
from volume_profile import session_profile

N_BINS = 80
# label -> pandas resample rule. The volume profile is rebuilt client-side from
# whichever timeframe's candles are showing, so it grows in step with replay.
TIMEFRAMES = {"30m": "30min", "1h": "1h", "4h": "4h", "1D": "1D"}
DEFAULT_TF = "1h"


def candles(bars, rule: str) -> dict:
    c = bars.resample(rule).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna()
    return {
        "t": c.index.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
        "o": c["open"].round(2).tolist(),
        "h": c["high"].round(2).tolist(),
        "l": c["low"].round(2).tolist(),
        "c": c["close"].round(2).tolist(),
        "v": c["volume"].round(1).tolist(),
    }


def window_payload(bars, label: str, rng: str) -> dict:
    """Build the per-timeframe candles + fixed price range for one week window.

    The volume profile is computed client-side over fixed bins spanning the
    window's full high/low, so it can develop as candles replay in. `poc/vah/val`
    here are a Python reference (full-resolution) shown only in the console.
    """
    prof = session_profile(bars, N_BINS)
    return {
        "label": label,
        "range": rng,
        "tf": {lbl: candles(bars, rule) for lbl, rule in TIMEFRAMES.items()},
        "lo": round(float(bars["low"].min()), 2),
        "hi": round(float(bars["high"].max()), 2),
        "nbins": N_BINS,
        "ref": None if prof is None else {
            "poc": round(prof.poc, 2), "vah": round(prof.vah, 2),
            "val": round(prof.val, 2),
        },
    }


def contract_payload(path: str) -> dict:
    bars = data_mod.load_csv(path)
    last_bars, this_bars, info = data_mod.trading_week_windows(bars)

    stem = path.split("/")[-1].replace(".Last.txt", "").replace(".txt", "")
    name = stem.split("MNQ_")[-1] if "MNQ_" in stem else stem.split("-")[-1]

    windows: dict[str, dict] = {}
    if not this_bars.empty:
        windows["this"] = window_payload(
            this_bars, "This week (developing)", info["this_range"])
    if not last_bars.empty:
        windows["last"] = window_payload(
            last_bars, "Last week (fixed)", info["last_range"])

    return {"name": name, "open_hour": info["open_hour"], "windows": windows}


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
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .row + .row { margin-top: 8px; }
  select, button { background: #ffffff; color: #1f2328; border: 1px solid #d0d7de;
           border-radius: 8px; padding: 8px 10px; font-size: 15px; }
  button { cursor: pointer; -webkit-tap-highlight-color: transparent; min-width: 42px; }
  button:active { background: #f0f3f6; }
  button.on { background: #1f6feb; color: #fff; border-color: #1f6feb; }
  #pick { flex: 1; min-width: 140px; }
  #win { flex: 0 0 auto; }
  #tf { flex: 0 0 auto; }
  #scrub { flex: 1; min-width: 120px; }
  #speed { width: 90px; }
  .meta { font-size: 12px; color: #57606a; }
  .legend { font-size: 11px; color: #57606a; padding: 6px 12px; }
  .sw { display:inline-block; width:10px; height:10px; border-radius:2px;
        margin: 0 4px 0 10px; vertical-align: middle; }
  #chart { width: 100%; }
  #wrap { position: relative; touch-action: none; }
  .cxline { position:absolute; pointer-events:none; display:none; z-index:4; }
  .cxline.v { border-left:1px dashed #6e7781; width:0; }
  .cxline.h { border-top:1px dashed #6e7781; height:0; }
  .cxtag { position:absolute; pointer-events:none; display:none; z-index:5;
           background:#1f2328; color:#fff; padding:1px 5px; border-radius:3px;
           font-size:11px; white-space:nowrap; }
  .cxinfo { position:absolute; top:6px; left:54px; pointer-events:none; display:none;
            z-index:5; background:rgba(255,255,255,.92); border:1px solid #d0d7de;
            border-radius:6px; padding:4px 7px; font-size:11px; line-height:1.5;
            color:#1f2328; }
</style>
</head>
<body>
<header>
  <h1>MNQ — Session Volume Profile</h1>
  <div class="row">
    <select id="pick"></select>
    <select id="win"></select>
    <select id="tf"></select>
  </div>
  <div class="row">
    <button id="play" title="Play / pause replay">▶</button>
    <button id="step" title="Reveal next candle">⏭</button>
    <button id="reset" title="Show full window">⟲</button>
    <input id="scrub" type="range" min="1" max="1" value="1">
    <label class="meta">speed <input id="speed" type="range" min="1" max="30" value="8"></label>
  </div>
  <div class="meta" id="meta"></div>
</header>
<div class="legend">
  <span class="sw" style="background:#1f6feb"></span>POC / value area
  <span class="sw" style="background:#d1242f"></span>LVN (low-volume node)
  <span class="sw" style="background:#2da44e"></span>up
  <span class="sw" style="background:#cf222e"></span>down
</div>
<div id="wrap">
  <div id="chart"></div>
  <div id="cxv" class="cxline v"></div>
  <div id="cxh" class="cxline h"></div>
  <div id="cxprice" class="cxtag"></div>
  <div id="cxtime" class="cxtag"></div>
  <div id="cxinfo" class="cxinfo"></div>
</div>
<script>
const DATA = __DATA__;
const POC="#1f6feb", LVN="#d1242f";
const VA_PCT=0.70, LVN_REL=0.30;

// --- volume-profile math (mirrors volume_profile.py) -------------------------
function profileVol(bars, lo, hi, nbins, upTo){
  if (hi <= lo) hi = lo + 1e-9;
  const step = (hi - lo) / nbins;
  const centers = new Array(nbins), vol = new Array(nbins).fill(0);
  for (let i = 0; i < nbins; i++) centers[i] = lo + step * (i + 0.5);
  const n = Math.min(upTo, bars.h.length);
  for (let k = 0; k < n; k++){
    const v = bars.v[k];
    if (!(v > 0)) continue;
    let loIdx = Math.floor((bars.l[k] - lo) / step);
    let hiIdx = Math.floor((bars.h[k] - lo) / step);
    loIdx = Math.max(0, Math.min(loIdx, nbins - 1));
    hiIdx = Math.max(0, Math.min(hiIdx, nbins - 1));
    const share = v / (hiIdx - loIdx + 1);
    for (let b = loIdx; b <= hiIdx; b++) vol[b] += share;
  }
  return {centers, vol, step};
}

function valueArea(vol){
  let total = 0; for (const x of vol) total += x;
  if (total <= 0) return null;
  let poc = 0; for (let i = 1; i < vol.length; i++) if (vol[i] > vol[poc]) poc = i;
  const target = total * VA_PCT;
  let captured = vol[poc], lo = poc, hi = poc, n = vol.length;
  while (captured < target && (lo > 0 || hi < n - 1)){
    const below = lo > 0 ? vol[lo - 1] : -1;
    const above = hi < n - 1 ? vol[hi + 1] : -1;
    if (above >= below) { hi++; captured += vol[hi]; }
    else { lo--; captured += vol[lo]; }
  }
  return {poc, lo, hi};
}

function lvnBands(centers, vol, step){
  let mx = 0; for (const x of vol) if (x > mx) mx = x;
  if (mx <= 0) return [];
  const thresh = mx * LVN_REL, bands = [];
  let start = null;
  for (let i = 1; i < vol.length - 1; i++){
    if (vol[i] < thresh){ if (start === null) start = i; }
    else if (start !== null){ bands.push([centers[start] - step/2, centers[i-1] + step/2]); start = null; }
  }
  if (start !== null) bands.push([centers[start] - step/2, centers[vol.length-2] + step/2]);
  return bands;
}

// --- view state --------------------------------------------------------------
let cur = {key:null, win:null, tf:"__DEFAULT_TF__"};
let reveal = null;   // null => full static window; else number of candles shown
let timer = null;
let LEVELS = null;   // current POC/VAH/VAL, for the crosshair readout

function activeWin(){ return DATA[cur.key].windows[cur.win]; }
function activeBars(){
  const w = activeWin();
  return w.tf[cur.tf] ? w.tf[cur.tf] : w.tf[Object.keys(w.tf)[0]];
}

function hline(y, color, width, dash){
  return {type:"line", xref:"paper", x0:0, x1:1, yref:"y", y0:y, y1:y,
          line:{color:color, width:width, dash:dash||"solid"}, layer:"above"};
}

function draw(){
  const w = activeWin();
  const bars = activeBars();
  const n = bars.t.length;
  const upTo = (reveal === null) ? n : Math.max(1, Math.min(reveal, n));

  const candle = {type:"candlestick",
    x: bars.t.slice(0, upTo), open: bars.o.slice(0, upTo), high: bars.h.slice(0, upTo),
    low: bars.l.slice(0, upTo), close: bars.c.slice(0, upTo),
    xaxis:"x", yaxis:"y", name:"price",
    increasing:{line:{color:"#2da44e"}}, decreasing:{line:{color:"#cf222e"}}};

  const pr = profileVol(bars, w.lo, w.hi, w.nbins, upTo);
  const va = valueArea(pr.vol);
  const bands = lvnBands(pr.centers, pr.vol, pr.step);

  let poc, vah, val, inVA = () => false;
  if (va){
    poc = pr.centers[va.poc]; vah = pr.centers[va.hi]; val = pr.centers[va.lo];
    inVA = (p) => p >= val && p <= vah;
  }
  LEVELS = va ? {poc:poc, vah:vah, val:val} : null;

  const colors = pr.centers.map(p => inVA(p) ? "#1f6feb" : "#b1b8c0");
  const vp = {type:"bar", orientation:"h", x:pr.vol, y:pr.centers,
    xaxis:"x2", yaxis:"y", marker:{color:colors}, opacity:0.85,
    name:"volume", hoverinfo:"skip"};

  const shapes = [], ann = [];
  if (va){
    shapes.push(
      {type:"rect", xref:"paper", x0:0, x1:1, yref:"y", y0:val, y1:vah,
       fillcolor:"#1f6feb", opacity:0.06, line:{width:0}, layer:"below"},
      hline(poc, POC, 2), hline(vah, POC, 1, "dash"), hline(val, POC, 1, "dash"));
    ann.push(
      {xref:"paper", x:0.005, y:poc, yref:"y", text:"POC", showarrow:false,
       font:{color:POC, size:11}, bgcolor:"#ffffff", xanchor:"left"},
      {xref:"paper", x:0.005, y:vah, yref:"y", text:"VAH", showarrow:false,
       font:{color:POC, size:10}, bgcolor:"#ffffff", xanchor:"left"},
      {xref:"paper", x:0.005, y:val, yref:"y", text:"VAL", showarrow:false,
       font:{color:POC, size:10}, bgcolor:"#ffffff", xanchor:"left"});
  }
  bands.forEach(b => shapes.push(
    {type:"rect", xref:"paper", x0:0, x1:1, yref:"y", y0:b[0], y1:b[1],
     fillcolor:LVN, opacity:0.13, line:{color:LVN, width:1, dash:"dot"}, layer:"below"}));

  const tag = (reveal === null) ? "full" : (upTo + "/" + n);
  document.getElementById("meta").textContent =
    w.label + "  ·  " + w.range + "  ·  " + cur.tf + "  ·  " + tag +
    (va ? ("  ·  POC " + poc.toFixed(1) + "  ·  VA " + val.toFixed(1) + "–" + vah.toFixed(1)
           + "  ·  " + bands.length + " LVN") : "");

  const layout = {
    height: Math.max(420, Math.round(window.innerHeight * 0.72)),
    margin: {l:48, r:8, t:8, b:28},
    paper_bgcolor:"#ffffff", plot_bgcolor:"#ffffff",
    font:{color:"#1f2328", size:11},
    showlegend:false, dragmode:"pan",
    xaxis:{domain:[0,0.80], type:"date", rangeslider:{visible:false},
           gridcolor:"#e1e4e8", showspikes:false, spikethickness:1},
    xaxis2:{domain:[0.82,1.0], anchor:"y", showgrid:false, zeroline:false,
            showticklabels:false},
    yaxis:{anchor:"x", side:"left", gridcolor:"#e1e4e8", showspikes:false,
           spikethickness:1, tickformat:","},
    shapes:shapes, annotations:ann,
  };
  Plotly.react("chart", [vp, candle], layout,
    {responsive:true, scrollZoom:true, displayModeBar:false});
}

// --- replay ------------------------------------------------------------------
const scrub = document.getElementById("scrub");
const playBtn = document.getElementById("play");

function syncScrub(){
  const n = activeBars().t.length;
  scrub.max = n;
  scrub.value = (reveal === null) ? n : Math.min(reveal, n);
}

function stop(){
  if (timer){ clearInterval(timer); timer = null; }
  playBtn.classList.remove("on"); playBtn.textContent = "▶";
}

function refresh(){ stop(); syncScrub(); draw(); }

function step(){
  const n = activeBars().t.length;
  reveal = (reveal === null ? 1 : Math.min(reveal + 1, n));
  scrub.value = reveal;
  draw();
  if (reveal >= n) stop();
}

function play(){
  if (timer){ stop(); return; }
  const n = activeBars().t.length;
  if (reveal === null || reveal >= n) reveal = 1;   // (re)start from the open
  playBtn.classList.add("on"); playBtn.textContent = "⏸";
  const fps = +document.getElementById("speed").value;
  timer = setInterval(step, Math.max(40, 1000 / fps));
}

playBtn.addEventListener("click", play);
document.getElementById("step").addEventListener("click", () => { stop(); step(); });
document.getElementById("reset").addEventListener("click", () => { stop(); reveal = null; syncScrub(); draw(); });
scrub.addEventListener("input", () => { stop(); reveal = +scrub.value; draw(); });
document.getElementById("speed").addEventListener("input", () => { if (timer){ const p = true; stop(); if (p) play(); } });

// --- selectors ---------------------------------------------------------------
const pick = document.getElementById("pick");
const winSel = document.getElementById("win");
const tfSel = document.getElementById("tf");

Object.keys(DATA).forEach(k => {
  const o = document.createElement("option");
  o.value = k; o.textContent = "MNQ " + k;
  pick.appendChild(o);
});

const anyTf = (() => {
  for (const k of Object.keys(DATA)){
    const ws = DATA[k].windows;
    const wk = Object.keys(ws)[0];
    if (wk) return ws[wk].tf;
  }
  return {};
})();
Object.keys(anyTf).forEach(k => {
  const o = document.createElement("option");
  o.value = k; o.textContent = k;
  tfSel.appendChild(o);
});

function fillWindows(){
  winSel.innerHTML = "";
  const ws = DATA[cur.key].windows;
  ["this", "last"].forEach(wk => {
    if (!ws[wk]) return;
    const o = document.createElement("option");
    o.value = wk; o.textContent = ws[wk].label;
    winSel.appendChild(o);
  });
  if (!ws[cur.win]) cur.win = winSel.options.length ? winSel.options[0].value : null;
  winSel.value = cur.win;
}

pick.addEventListener("change", () => {
  cur.key = pick.value; reveal = null; fillWindows(); refresh();
});
winSel.addEventListener("change", () => { cur.win = winSel.value; reveal = null; refresh(); });
tfSel.addEventListener("change", () => { cur.tf = tfSel.value; reveal = null; refresh(); });
window.addEventListener("resize", draw);

// --- crosshair cursor (free-floating, with price/time + level readout) -------
const cxv = document.getElementById("cxv"), cxh = document.getElementById("cxh");
const cxprice = document.getElementById("cxprice"), cxtime = document.getElementById("cxtime");
const cxinfo = document.getElementById("cxinfo");

function fmtP(v){ return v.toLocaleString(undefined, {maximumFractionDigits:0}); }
function fmtT(ms){
  const d = new Date(ms), p = n => String(n).padStart(2, "0");
  return p(d.getMonth()+1) + "/" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}
function hideCross(){ [cxv, cxh, cxprice, cxtime, cxinfo].forEach(e => e.style.display = "none"); }

function moveCross(evt){
  const gd = document.getElementById("chart");
  const fl = gd && gd._fullLayout;
  if (!fl || !fl.xaxis || !fl.yaxis){ hideCross(); return; }
  const xa = fl.xaxis, ya = fl.yaxis, xa2 = fl.xaxis2;
  const rect = gd.getBoundingClientRect();
  const gx = evt.clientX - rect.left, gy = evt.clientY - rect.top;
  const xL = xa._offset, xR = xa._offset + xa._length;
  const yT = ya._offset, yB = ya._offset + ya._length;
  if (gx < xL || gx > xR || gy < yT || gy > yB){ hideCross(); return; }

  const xl0 = xa.r2l(xa.range[0]), xl1 = xa.r2l(xa.range[1]);
  const tms = xl0 + (gx - xL) / (xR - xL) * (xl1 - xl0);
  const yl0 = ya.r2l(ya.range[0]), yl1 = ya.r2l(ya.range[1]);
  const price = yl0 + (gy - yB) / (yT - yB) * (yl1 - yl0);
  const fullR = xa2 ? (xa2._offset + xa2._length) : xR;   // span the VP panel too

  cxv.style.display = "block"; cxv.style.left = gx + "px"; cxv.style.top = yT + "px";
  cxv.style.height = (yB - yT) + "px";
  cxh.style.display = "block"; cxh.style.top = gy + "px"; cxh.style.left = xL + "px";
  cxh.style.width = (fullR - xL) + "px";

  cxprice.style.display = "block"; cxprice.textContent = fmtP(price);
  cxprice.style.left = (xL - 2) + "px"; cxprice.style.top = (gy - 9) + "px";
  cxprice.style.transform = "translateX(-100%)";

  cxtime.style.display = "block"; cxtime.textContent = fmtT(tms);
  cxtime.style.left = gx + "px"; cxtime.style.top = (yB + 3) + "px";
  cxtime.style.transform = "translateX(-50%)";

  const sgn = v => (v >= 0 ? "+" : "") + v.toFixed(1);
  cxinfo.style.display = "block";
  cxinfo.innerHTML = "<b>" + fmtP(price) + "</b> @ " + fmtT(tms) + (LEVELS ?
    ("<br>POC " + sgn(price - LEVELS.poc) + " · VAH " + sgn(price - LEVELS.vah) +
     " · VAL " + sgn(price - LEVELS.val)) : "");
}

const chartEl = document.getElementById("chart");
chartEl.addEventListener("pointermove", moveCross);
chartEl.addEventListener("pointerdown", moveCross);
chartEl.addEventListener("pointerleave", hideCross);

cur.key = Object.keys(DATA)[0];
fillWindows();
tfSel.value = DATA[cur.key].windows[cur.win].tf[cur.tf] ? cur.tf : Object.keys(anyTf)[0];
cur.tf = tfSel.value;
refresh();
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
        if not d["windows"]:
            print(f"  {d['name']}: no usable week window — skipped")
            continue
        payload[d["name"]] = d
        oh = d["open_hour"]
        print(f"  {d['name']}: session opens {oh:02d}:00  "
              f"({'~ET' if oh == 18 else '~CT' if oh == 17 else 'detected'})")
        for wk in ("this", "last"):
            w = d["windows"].get(wk)
            if w:
                print(f"      {w['label']:22s} {w['range']}  "
                      f"({len(w['tf'][DEFAULT_TF]['t'])} {DEFAULT_TF}-candles)")
    if not payload:
        print("no contracts with usable week windows — nothing written")
        return
    html = (TEMPLATE
            .replace("__PLOTLY_JS__", plotly_script())
            .replace("__DEFAULT_TF__", DEFAULT_TF)
            .replace("__DATA__", json.dumps(payload)))
    with open(out, "w") as fh:
        fh.write(html)
    print(f"wrote {out}  ({len(html)/1024:.0f} KB, {len(payload)} contracts)")


if __name__ == "__main__":
    main(sys.argv)
