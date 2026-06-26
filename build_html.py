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
from volume_profile import low_volume_nodes, profile_arrays, session_profile

N_BINS = 80
# label -> pandas resample rule. The volume profile is rebuilt client-side from
# whichever timeframe's candles are showing, so it grows in step with replay.
TIMEFRAMES = {"30m": "30min", "1h": "1h", "4h": "4h", "1D": "1D"}
DEFAULT_TF = "1h"
# intraday timeframes for the per-day view
DAILY_TF = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h"}
DAILY_DEFAULT_TF = "15m"
MAX_DAILY = 60   # cap per-day payload to the most recent N sessions (size guard)


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


def candles_by_tf(bars) -> dict:
    return {lbl: candles(bars, rule) for lbl, rule in TIMEFRAMES.items()}


def fixed_vp(bars) -> dict | None:
    """Precompute a complete (fixed) volume profile for a set of bars.

    Returns the volume-at-price histogram plus POC/VAH/VAL/LVN, computed at full
    bar resolution. Used for reference profiles whose levels are drawn over a
    *different* set of candles (e.g. last week's levels over this week's price).
    """
    prof = session_profile(bars, N_BINS)
    if prof is None:
        return None
    centers, vol = profile_arrays(bars, N_BINS)
    lvns = low_volume_nodes(centers, vol)
    return {
        "price": [round(float(x), 2) for x in centers],
        "vol": [round(float(x), 1) for x in vol],
        "poc": round(prof.poc, 2), "vah": round(prof.vah, 2), "val": round(prof.val, 2),
        "lvns": [[round(lo, 2), round(hi, 2)] for lo, hi in lvns],
    }


def developing_window(bars, label: str, rng: str) -> dict:
    """A window whose VP rebuilds client-side as candles replay in."""
    return {
        "label": label, "range": rng, "develop": True,
        "tf": candles_by_tf(bars),
        "lo": round(float(bars["low"].min()), 2),
        "hi": round(float(bars["high"].max()), 2),
        "nbins": N_BINS, "fixedVP": None,
    }


def fixed_window(profile_bars, candle_bars, label: str,
                 levels_rng: str, price_rng: str) -> dict | None:
    """A window that draws a FIXED profile (from `profile_bars`) as reference
    levels over `candle_bars` candles — the levels do not develop on replay."""
    fv = fixed_vp(profile_bars)
    if fv is None:
        return None
    return {
        "label": label, "range": levels_rng, "priceRange": price_rng,
        "develop": False, "tf": candles_by_tf(candle_bars), "fixedVP": fv,
    }


def day_payload(rec) -> dict:
    """One trading day: full-day candles + overnight and RTH (9:30–3pm) profiles,
    the 9:30 cash open, and where that open landed vs overnight value."""
    g, ov, rth = rec["session"], rec["overnight"], rec["rth"]
    ovp = fixed_vp(ov) if len(ov) else None
    rvp = fixed_vp(rth) if len(rth) else None
    open_price = float(rth["open"].iloc[0]) if len(rth) else None
    rth_close = float(rth["close"].iloc[-1]) if len(rth) else None

    loc = None
    if open_price is not None and ovp is not None:
        if open_price > ovp["vah"]:
            loc = "above VAH"
        elif open_price < ovp["val"]:
            loc = "below VAL"
        else:
            loc = "inside value"

    return {
        "date": rec["date"].strftime("%Y-%m-%d (%a)"),
        "tf": {lbl: candles(g, rule) for lbl, rule in DAILY_TF.items()},
        "cashOpen": rec["co"].strftime("%Y-%m-%dT%H:%M:%S"),
        "cut3": rec["cut"].strftime("%Y-%m-%dT%H:%M:%S"),
        "overnightVP": ovp,
        "rthVP": rvp,
        "openPrice": None if open_price is None else round(open_price, 2),
        "rthClose": None if rth_close is None else round(rth_close, 2),
        "openLoc": loc,
    }


def daily_stats(days: list[dict]) -> dict:
    """Aggregate how the 9:30 open related to overnight value across all days,
    and whether the cash session (9:30→3pm) closed up from its open."""
    cats = {"inside value": [], "above VAH": [], "below VAL": []}
    for d in days:
        loc = d["openLoc"]
        if loc is None or d["openPrice"] is None or d["rthClose"] is None:
            continue
        cats[loc].append(1 if d["rthClose"] > d["openPrice"] else 0)
    n = sum(len(v) for v in cats.values())
    out = {"n": n, "cats": {}}
    for loc, arr in cats.items():
        if arr:
            out["cats"][loc] = {
                "pct": round(100 * len(arr) / n) if n else 0,
                "count": len(arr),
                "upRate": round(100 * sum(arr) / len(arr)),
            }
    return out


def daily_payload(bars, info) -> dict:
    sessions, _ = data_mod.daily_sessions(bars, info["open_hour"])
    if MAX_DAILY:
        sessions = sessions[-MAX_DAILY:]
    days, order, raw = {}, [], []
    for rec in sessions:
        dp = day_payload(rec)
        key = rec["date"].strftime("%Y-%m-%d")
        days[key] = dp
        order.append(key)
        raw.append(dp)
    return {"dates": order, "days": days, "stats": daily_stats(raw)}


def contract_payload(path: str) -> dict:
    bars = data_mod.load_csv(path)
    last_bars, this_bars, info = data_mod.trading_week_windows(bars)

    stem = path.split("/")[-1].replace(".Last.txt", "").replace(".txt", "")
    name = stem.split("MNQ_")[-1] if "MNQ_" in stem else stem.split("-")[-1]

    windows: dict[str, dict] = {}
    if not this_bars.empty:
        windows["this"] = developing_window(
            this_bars, "This week (developing)", info["this_range"])
    if not last_bars.empty:
        # Last week's levels (fixed) drawn over this week's candles for reference;
        # fall back to last week's own candles if there is no current week yet.
        disp = this_bars if not this_bars.empty else last_bars
        price_rng = info["this_range"] if not this_bars.empty else info["last_range"]
        w = fixed_window(last_bars, disp, "Last week (fixed)",
                         info["last_range"], price_rng)
        if w is not None:
            windows["last"] = w

    return {"name": name, "open_hour": info["open_hour"], "windows": windows,
            "daily": daily_payload(bars, info)}


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
    <select id="day" style="display:none"></select>
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
  <div class="meta" id="stats" style="display:none"></div>
</header>
<div class="legend">
  <span class="sw" style="background:#1f6feb"></span>overnight POC / value
  <span class="sw" style="background:#bf8700"></span>RTH (9:30–3p) POC / value
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

function isDaily(){ return cur.win === "daily"; }
function activeWin(){ return DATA[cur.key].windows[cur.win]; }
function activeDay(){ return DATA[cur.key].daily.days[cur.day]; }
function activeBars(){
  const src = isDaily() ? activeDay() : activeWin();
  return src.tf[cur.tf] ? src.tf[cur.tf] : src.tf[Object.keys(src.tf)[0]];
}
function draw(){ isDaily() ? drawDay() : drawWeek(); }

function hline(y, color, width, dash){
  return {type:"line", xref:"paper", x0:0, x1:1, yref:"y", y0:y, y1:y,
          line:{color:color, width:width, dash:dash||"solid"}, layer:"above"};
}

function drawWeek(){
  const w = activeWin();
  const bars = activeBars();
  const n = bars.t.length;
  const upTo = (reveal === null) ? n : Math.max(1, Math.min(reveal, n));

  const candle = {type:"candlestick",
    x: bars.t.slice(0, upTo), open: bars.o.slice(0, upTo), high: bars.h.slice(0, upTo),
    low: bars.l.slice(0, upTo), close: bars.c.slice(0, upTo),
    xaxis:"x", yaxis:"y", name:"price",
    increasing:{line:{color:"#2da44e"}}, decreasing:{line:{color:"#cf222e"}}};

  // Volume profile: develop it client-side from the revealed candles, OR draw a
  // fixed precomputed profile (its levels stay put as candles replay in).
  let centers, vols, poc, vah, val, bands, inVA = () => false;
  if (w.develop){
    const pr = profileVol(bars, w.lo, w.hi, w.nbins, upTo);
    centers = pr.centers; vols = pr.vol;
    const va = valueArea(pr.vol);
    bands = lvnBands(pr.centers, pr.vol, pr.step);
    if (va){ poc = centers[va.poc]; vah = centers[va.hi]; val = centers[va.lo]; }
  } else {
    const fv = w.fixedVP;
    centers = fv.price; vols = fv.vol;
    poc = fv.poc; vah = fv.vah; val = fv.val; bands = fv.lvns;
  }
  const hasVA = (poc !== undefined);
  if (hasVA) inVA = (p) => p >= val && p <= vah;
  LEVELS = hasVA ? {poc:poc, vah:vah, val:val} : null;

  const colors = centers.map(p => inVA(p) ? "#1f6feb" : "#b1b8c0");
  const vp = {type:"bar", orientation:"h", x:vols, y:centers,
    xaxis:"x2", yaxis:"y", marker:{color:colors}, opacity:0.85,
    name:"volume", hoverinfo:"skip"};

  const shapes = [], ann = [];
  if (hasVA){
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
  (bands || []).forEach(b => shapes.push(
    {type:"rect", xref:"paper", x0:0, x1:1, yref:"y", y0:b[0], y1:b[1],
     fillcolor:LVN, opacity:0.13, line:{color:LVN, width:1, dash:"dot"}, layer:"below"}));

  const tag = (reveal === null) ? "full" : (upTo + "/" + n);
  const src = w.develop
    ? (w.range + "  ·  " + cur.tf + "  ·  " + tag)
    : ("levels " + w.range + "  ·  price " + (w.priceRange || "") + "  ·  " + cur.tf + "  ·  " + tag + "  ·  FIXED");
  document.getElementById("meta").textContent =
    w.label + "  ·  " + src +
    (hasVA ? ("  ·  POC " + poc.toFixed(1) + "  ·  VA " + val.toFixed(1) + "–" + vah.toFixed(1)
           + "  ·  " + (bands ? bands.length : 0) + " LVN") : "");
  document.getElementById("stats").style.display = "none";

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

// --- per-day view: overnight VP + RTH VP side by side, 9:30 open vs value -----
function lblc(y, text, color, size){
  return {xref:"paper", x:0.005, y:y, yref:"y", text:text, showarrow:false,
          font:{color:color, size:size||10}, bgcolor:"#ffffff", xanchor:"left"};
}

function vpTrace(vp, axis, inColor, outColor, name){
  const inVA = p => p >= vp.val && p <= vp.vah;
  return {type:"bar", orientation:"h", x:vp.vol, y:vp.price, xaxis:axis, yaxis:"y",
    marker:{color: vp.price.map(p => inVA(p) ? inColor : outColor)}, opacity:0.85,
    name:name, hoverinfo:"skip"};
}

function drawDay(){
  const day = activeDay();
  const bars = activeBars();
  const n = bars.t.length;
  const upTo = (reveal === null) ? n : Math.max(1, Math.min(reveal, n));
  const t = bars.t.slice(0, upTo);

  const candle = {type:"candlestick", x:t,
    open:bars.o.slice(0, upTo), high:bars.h.slice(0, upTo),
    low:bars.l.slice(0, upTo), close:bars.c.slice(0, upTo),
    xaxis:"x", yaxis:"y", name:"price",
    increasing:{line:{color:"#2da44e"}}, decreasing:{line:{color:"#cf222e"}}};
  const traces = [candle];
  const shapes = [], ann = [];

  // time-conditional: the RTH (9:30–3pm) profile shows once 3pm is reached
  const curMs = t.length ? new Date(t[t.length-1]).getTime() : null;
  const cut3 = new Date(day.cut3).getTime();
  const showRth = day.rthVP && (reveal === null || (curMs !== null && curMs >= cut3));

  // overnight VP + levels + emphasized LVN zones (the 9:30-open focus)
  const ov = day.overnightVP;
  if (ov){
    traces.push(vpTrace(ov, "x2", "#1f6feb", "#b1b8c0", "overnight"));
    shapes.push(
      {type:"rect", xref:"paper", x0:0, x1:1, yref:"y", y0:ov.val, y1:ov.vah,
       fillcolor:"#1f6feb", opacity:0.05, line:{width:0}, layer:"below"},
      hline(ov.poc, POC, 2), hline(ov.vah, POC, 1, "dash"), hline(ov.val, POC, 1, "dash"));
    ann.push(lblc(ov.poc, "ON POC", POC, 11), lblc(ov.vah, "ON VAH", POC),
             lblc(ov.val, "ON VAL", POC));
    (ov.lvns || []).forEach(b => shapes.push(
      {type:"rect", xref:"paper", x0:0, x1:1, yref:"y", y0:b[0], y1:b[1],
       fillcolor:LVN, opacity:0.18, line:{color:LVN, width:1.4, dash:"dot"}, layer:"below"}));
    LEVELS = {poc:ov.poc, vah:ov.vah, val:ov.val};
  }

  // RTH (9:30–2:59pm) VP + levels in gold, side by side on x3
  if (showRth){
    const r = day.rthVP;
    traces.push(vpTrace(r, "x3", "#bf8700", "#d9c89a", "rth"));
    shapes.push(hline(r.poc, "#bf8700", 2), hline(r.vah, "#bf8700", 1, "dash"),
                hline(r.val, "#bf8700", 1, "dash"));
    ann.push(lblc(r.poc, "RTH POC", "#bf8700", 11), lblc(r.vah, "RTH VAH", "#bf8700"),
             lblc(r.val, "RTH VAL", "#bf8700"));
  }

  // 9:30 cash-open divider + open price + where it landed vs overnight value
  shapes.push({type:"line", xref:"x", x0:day.cashOpen, x1:day.cashOpen, yref:"paper",
    y0:0, y1:1, line:{color:"#6e7781", width:1.2, dash:"dot"}});
  if (day.openPrice != null){
    shapes.push({type:"line", xref:"paper", x0:0, x1:1, yref:"y",
      y0:day.openPrice, y1:day.openPrice, line:{color:"#cf222e", width:1, dash:"dashdot"}});
    ann.push({xref:"x", x:day.cashOpen, yref:"paper", y:1, yanchor:"bottom", xanchor:"left",
      text:" 9:30 " + day.openPrice + (day.openLoc ? (" · " + day.openLoc) : ""),
      showarrow:false, font:{color:"#cf222e", size:11}, bgcolor:"#ffffff"});
  }

  const tag = (reveal === null) ? "full" : (upTo + "/" + n);
  document.getElementById("meta").textContent =
    "Daily · " + day.date + " · " + cur.tf + " · " + tag +
    (ov ? (" · ON POC " + ov.poc.toFixed(1) + " VA " + ov.val.toFixed(1) + "–" + ov.vah.toFixed(1)
           + " · " + (ov.lvns ? ov.lvns.length : 0) + " LVN") : "") +
    (showRth ? (" · RTH POC " + day.rthVP.poc.toFixed(1)) : "");
  renderStats(DATA[cur.key].daily.stats);

  const layout = {
    height: Math.max(420, Math.round(window.innerHeight * 0.72)),
    margin: {l:48, r:8, t:18, b:28},
    paper_bgcolor:"#ffffff", plot_bgcolor:"#ffffff", font:{color:"#1f2328", size:11},
    showlegend:false, dragmode:"pan",
    xaxis:{domain:[0,0.66], type:"date", rangeslider:{visible:false},
           gridcolor:"#e1e4e8", showspikes:false},
    xaxis2:{domain:[0.68,0.83], anchor:"y", showgrid:false, zeroline:false,
            showticklabels:false, title:{text:"O/N", font:{size:9, color:"#57606a"}}},
    xaxis3:{domain:[0.85,1.0], anchor:"y", showgrid:false, zeroline:false,
            showticklabels:false, title:{text:"RTH", font:{size:9, color:"#57606a"}}},
    yaxis:{anchor:"x", side:"left", gridcolor:"#e1e4e8", showspikes:false, tickformat:","},
    shapes:shapes, annotations:ann,
  };
  Plotly.react("chart", traces, layout,
    {responsive:true, scrollZoom:true, displayModeBar:false});
}

function renderStats(s){
  const el = document.getElementById("stats");
  if (!s || !s.n){ el.style.display = "none"; return; }
  const order = ["inside value", "above VAH", "below VAL"];
  const parts = order.filter(k => s.cats[k]).map(k => {
    const c = s.cats[k];
    return k + " " + c.pct + "% (" + c.count + "), closed↑ " + c.upRate + "%";
  });
  el.innerHTML = "<b>9:30 open vs overnight value</b> — " + s.n + " days · " + parts.join("  ·  ");
  el.style.display = "block";
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
const daySel = document.getElementById("day");
const tfSel = document.getElementById("tf");

function opt(sel, val, txt){
  const o = document.createElement("option");
  o.value = val; o.textContent = txt; sel.appendChild(o);
}

Object.keys(DATA).forEach(k => opt(pick, k, "MNQ " + k));

function winValid(){
  if (cur.win === "daily"){
    const dd = DATA[cur.key].daily;
    return dd && dd.dates.length;
  }
  return DATA[cur.key].windows[cur.win];
}

function fillWindows(){
  winSel.innerHTML = "";
  const ws = DATA[cur.key].windows;
  ["this", "last"].forEach(wk => { if (ws[wk]) opt(winSel, wk, ws[wk].label); });
  const dd = DATA[cur.key].daily;
  if (dd && dd.dates.length) opt(winSel, "daily", "Daily — overnight + 9:30");
  if (!winValid()) cur.win = winSel.options.length ? winSel.options[0].value : null;
  winSel.value = cur.win;
}

function fillDays(){
  daySel.innerHTML = "";
  const dd = DATA[cur.key].daily;
  dd.dates.forEach(dt => opt(daySel, dt, dd.days[dt].date));
  if (!dd.days[cur.day]) cur.day = dd.dates[dd.dates.length - 1];   // default latest
  daySel.value = cur.day;
}

function fillTf(){
  tfSel.innerHTML = "";
  const tfs = isDaily() ? activeDay().tf : activeWin().tf;
  Object.keys(tfs).forEach(k => opt(tfSel, k, k));
  const def = isDaily() ? "__DAILY_DEFAULT_TF__" : "__DEFAULT_TF__";
  cur.tf = tfs[cur.tf] ? cur.tf : (tfs[def] ? def : Object.keys(tfs)[0]);
  tfSel.value = cur.tf;
}

function applyMode(){
  daySel.style.display = isDaily() ? "" : "none";
  if (isDaily()) fillDays();
  fillTf();
}

pick.addEventListener("change", () => {
  cur.key = pick.value; reveal = null; fillWindows(); applyMode(); refresh();
});
winSel.addEventListener("change", () => {
  cur.win = winSel.value; reveal = null; applyMode(); refresh();
});
daySel.addEventListener("change", () => { cur.day = daySel.value; reveal = null; refresh(); });
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
  const xa = fl.xaxis, ya = fl.yaxis, xa2 = fl.xaxis2, xa3 = fl.xaxis3;
  const rect = gd.getBoundingClientRect();
  const gx = evt.clientX - rect.left, gy = evt.clientY - rect.top;
  const xL = xa._offset, xR = xa._offset + xa._length;
  const yT = ya._offset, yB = ya._offset + ya._length;
  if (gx < xL || gx > xR || gy < yT || gy > yB){ hideCross(); return; }

  const xl0 = xa.r2l(xa.range[0]), xl1 = xa.r2l(xa.range[1]);
  const tms = xl0 + (gx - xL) / (xR - xL) * (xl1 - xl0);
  const yl0 = ya.r2l(ya.range[0]), yl1 = ya.r2l(ya.range[1]);
  const price = yl0 + (gy - yB) / (yT - yB) * (yl1 - yl0);
  let fullR = xR;                                          // span the VP panel(s) too
  if (xa2) fullR = Math.max(fullR, xa2._offset + xa2._length);
  if (xa3) fullR = Math.max(fullR, xa3._offset + xa3._length);

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
applyMode();
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
        if not d["windows"] and not d["daily"]["dates"]:
            print(f"  {d['name']}: no usable sessions — skipped")
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
        dd = d["daily"]
        st = dd["stats"]
        print(f"      Daily sessions: {len(dd['dates'])}  "
              f"(9:30 open vs O/N value over {st['n']} days)")
    if not payload:
        print("no contracts with usable sessions — nothing written")
        return
    html = (TEMPLATE
            .replace("__PLOTLY_JS__", plotly_script())
            .replace("__DEFAULT_TF__", DEFAULT_TF)
            .replace("__DAILY_DEFAULT_TF__", DAILY_DEFAULT_TF)
            .replace("__DATA__", json.dumps(payload)))
    with open(out, "w") as fh:
        fh.write(html)
    print(f"wrote {out}  ({len(html)/1024:.0f} KB, {len(payload)} contracts)")


if __name__ == "__main__":
    main(sys.argv)
