#!/usr/bin/env python3
"""RiverWatch: NWPS gauge status for Golden / Confluence Park area (api.water.noaa.gov)."""
import json, sys, urllib.request
from datetime import datetime, timezone

BASE = "https://api.water.noaa.gov/nwps/v1/gauges"

GAUGES = [
    ("GLDC2", "Clear Creek at Golden (Confluence Park)"),
    ("HIWC2", "Lena Gulch at Golden"),
    ("VBCC2", "Van Bibber Creek near Golden (Van Bibber Park)"),
    ("DRBC2", "Clear Creek at Derby (downstream)"),
    ("DNVC2", "South Platte River at Denver (surfing)"),
    ("ENWC2", "South Platte River at Englewood (downstream)"),
]

def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "riverwatch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def age_hours(valid_iso):
    t = datetime.fromisoformat(valid_iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600

def summarize(lid, name):
    try:
        d = get(f"{BASE}/{lid}/stageflow/observed")
    except Exception as e:
        print(f"### {name} ({lid}): ERROR {e}")
        return
    data = d.get("data", [])
    if not data:
        print(f"### {name} ({lid}): no observed data (issued {d.get('issuedTime')})")
        return
    latest = data[-1]
    age = age_hours(latest["validTime"])
    stage, flow = latest["primary"], latest["secondary"]
    # trends: 1h, 6h, 24h ago
    def back(hours):
        for x in reversed(data):
            if age_hours(x["validTime"]) >= hours:
                return x
        return data[0]
    out = [f"### {name} ({lid})"]
    out.append(f"  issued: {d['issuedTime']}  newest obs: {latest['validTime']} (age {age:.1f} h)")
    out.append(f"  stage: {stage:.2f} ft   flow: {flow*1000:.1f} cfs ({flow:.4f} kcfs)")
    for label, hrs in (("1h", 1.0), ("6h", 6.0), ("24h", 24.0)):
        x = back(hrs)
        ds = stage - x["primary"]
        df = (flow - x["secondary"]) * 1000
        out.append(f"  trend {label:>3}: stage {ds:+.2f} ft, flow {df:+.0f} cfs")
    # min/max over last 24h
    day = [x for x in data if age_hours(x["validTime"]) <= 24]
    if len(day) > 1:
        lo, hi = min(day, key=lambda x: x["primary"]), max(day, key=lambda x: x["primary"])
        out.append(f"  24h range: {lo['primary']:.2f}-{hi['primary']:.2f} ft, flow {min(x['secondary'] for x in day)*1000:.0f}-{max(x['secondary'] for x in day)*1000:.0f} cfs")
    print("\n".join(out))

def forecast(lid, hours=24):
    try:
        d = get(f"{BASE}/{lid}/stageflow/forecast")
    except Exception as e:
        print(f"  forecast ERROR {e}")
        return
    data = d.get("data", []) or []
    fut = []
    now = datetime.now(timezone.utc)
    for x in data:
        t = datetime.fromisoformat(x["validTime"].replace("Z", "+00:00"))
        if t >= now and (t - now).total_seconds() / 3600 <= hours:
            fut.append((t, x["primary"], x["secondary"]))
    if fut:
        fut.sort(key=lambda a: a[0])
        line = ", ".join(f"{t:%H:%M}Z {s:.1f}ft/{f*1000:.0f}cfs" for t, s, f in fut[::max(1, len(fut)//6)][:6])
        print(f"  forecast (issued {d.get('issuedTime')}): {line}")
    else:
        print("  forecast: none within window")

print("=" * 72)
print(f"RiverWatch NWPS report  {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
print("=" * 72)
for lid, name in GAUGES:
    summarize(lid, name)
print()
print("--- 24h flow forecast (South Platte + Clear Creek) ---")
for lid in ("GLDC2", "DNVC2"):
    print(f"## {lid}")
    forecast(lid)
