#!/usr/bin/env python3
"""Golden, CO water report: flow (USGS NWIS) + water quality (EPA WQP).

Robust against the flaky USGS waterservices backend (rotating between an
old Tomcat service and a new PostgREST one): every call is retried up to
MAX_TRIES times until a 200 response with the expected content pattern.

Outputs a markdown report to stdout and raw payloads to /tmp/golden_water/.
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

OUT = "/tmp/golden_water"
os.makedirs(OUT, exist_ok=True)
MAX_TRIES = 12
BACKOFF = 3.0
UA = {"User-Agent": "water-report/1.0 (dzyla personal use)"}
TODAY = date(2026, 8, 16)
Q_START = (TODAY - timedelta(days=90)).isoformat()

# --- Golden area stations (resolved in prior probes) ---
CLEAR_CREEK_AT_GOLDEN = "06719505"
CLEAR_CREEK_NEAR_GOLDEN = "06719500"
# WQP ids for water quality (USGS + CDPH/STORET swim sites)
WQ_SITES = [
    "USGS-06719505",          # Clear Creek at Golden (USGS)
    "USGS-06719500",          # Clear Creek near Golden (USGS)
    "21COL001_WQX-CC-2",      # Clear Creek footbridge at Golden Library
    "CORIVWCH_WQX-254",       # Clear Creek footbridge @ Library
    "21COL001-CLC010",        # Clear Creek at Youngfield St
    "CCWF-CC-SW-2",           # Mainstem Clear Creek (CDPH swim site)
]
BACTERIA = ["E. coli", "Enterococcus"]
CHEMISTRY = ["Dissolved oxygen", "Water temperature", "Turbidity",
             "Total suspended solids", "pH", "Specific conductance"]


def fetch(url, expect_prefix="", tries=MAX_TRIES, timeout=45, tag=""):
    """GET with retries until HTTP 200 (and optional content sniff)."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code = r.status
                body = r.read().decode("utf-8", "replace")
            if code == 200 and (not expect_prefix or body.lstrip().startswith(expect_prefix)):
                return body
            last = f"HTTP {code} / bad prefix"
        except Exception as e:
            last = str(e)
        time.sleep(BACKOFF)
    raise RuntimeError(f"fetch failed after {tries} tries ({tag}): {last} :: {url}")


def site_query(name):
    url = (f"https://waterservices.usgs.gov/nwis/site/?format=json"
           f"&siteName={name.replace(' ', '%20')}&stateCd=CO")
    body = fetch(url, expect_prefix="{", tag=f"siteName={name}")
    d = json.loads(body)
    out = []
    for s in d["value"]["timeSeries"]:
        code = s["sourceInfo"][0]["siteCode"][0]["value"]
        lat = float(s["geoGeometry"]["x"][0]["value"])
        lon = float(s["geoGeometry"]["y"][0]["value"])
        # Golden-area filter: within ~25 km of Confluence Park
        dlat = lat - 39.745
        dlon = lon + 105.27
        if dlat * dlat + dlon * dlon < 0.35:
            out.append((code, s["name"][0]["value"], lat, lon))
    return out


def iv_recent(site, params="00060,00010", minutes=2880):
    """Instantaneous values (last 2 days) for a USGS site."""
    url = (f"https://waterservices.usgs.gov/nwis/iv/?format=rdb&siteNo={site}"
           f"&parameterCd={params}&period={minutes}")
    body = fetch(url, expect_prefix="#", tag=f"iv {site}")
    rows = []
    for line in body.splitlines():
        if line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) < 9 or not f[1] or f[1] == "site_no":
            continue
        rows.append({"agency": f[0], "site": f[1], "param": f[2],
                     "date": f[3], "time": f[4], "val": f[7], "qual": f[8]})
    return rows


def wqp_results(site, chars):
    """WQP narrow-result CSV for a site (last 90 days)."""
    url = (f"https://www.waterqualitydata.us/data/Result/search?"
           f"siteid={site}&startDateLo={Q_START}&startDateHi={TODAY.isoformat()}"
           f"&characteristicName={urllib.parse.quote(','.join(chars))}"
           f"&mimeType=csv&dataProfile=narrowResult&zip=no")
    body = fetch(url, expect_prefix="OrganizationIdentifier", tag=f"wqp {site}")
    rows = list(csv.DictReader(io.StringIO(body)))
    return rows


def summarize(rows, chars):
    """Per-characteristic: last value, 7-day mean, 30-day mean, count."""
    stats = {}
    for r in rows:
        ch = r.get("CharacteristicName", "")
        if ch not in chars:
            continue
        try:
            v = float(r.get("ResultMeasureValue", ""))
            d = r.get("ActivityStartDate", "")[:10]
            dt = date.fromisoformat(d)
        except (ValueError, TypeError):
            continue
        age = (TODAY - dt).days
        s = stats.setdefault(ch, {"last": None, "n7": [], "n30": []})
        if s["last"] is None or dt >= s["last"][0]:
            s["last"] = (dt, v, r.get("ResultMeasure/MeasureUnitCode", ""))
        if age <= 7:
            s["n7"].append(v)
        if age <= 30:
            s["n30"].append(v)
    return stats


def fmt_flow(rows, label):
    q = [r for r in rows if r["param"] == "00060" and r["val"] not in ("", " ", "NA")]
    t = [r for r in rows if r["param"] == "00010" and r["val"] not in ("", " ", "NA")]
    lines = []
    if q:
        latest = q[-1]
        vals = [float(r["val"]) for r in q]
        day = (TODAY - timedelta(days=7))
        last7 = [float(r["val"]) for r in q if r["date"][:10] >= day.isoformat()]
        lines.append(f"- **{label}** — latest {latest['val']} cfs on "
                     f"{latest['date']} {latest['time']}; 2-day avg {sum(vals)/len(vals):.0f} cfs; "
                     f"7-day avg {sum(last7)/len(last7):.0f} cfs" if last7 else "")
    if t:
        lines.append(f"  - water temp latest {t[-1]['val']} °F")
    return "\n".join(x for x in lines if x)


def main():
    print("== resolving stations ==")
    resolved = {}
    for name in ("clear creek", "cherry creek", "south platte"):
        try:
            found = site_query(name)
            resolved[name] = found
            for c, n, la, lo in found:
                print(f"  {c} | {n[:45]} | {la:.4f},{lo:.4f}")
        except Exception as e:
            print(f"  ! {name}: {e}")

    platte = next((c for c, n, la, lo in resolved.get("south platte", [])
                   if "golden" in n.lower() or abs(la - 39.75) < 0.06), None)
    cherry = next((c for c, n, la, lo in resolved.get("cherry creek", [])
                   if abs(la - 39.74) < 0.10 and abs(lo + 105.25) < 0.12), None)

    print("\n== flow (USGS iv, last 2 days) ==")
    flow_lines = []
    for site, label in [(CLEAR_CREEK_AT_GOLDEN, "Clear Creek at Golden (USGS 06719505)"),
                        (CLEAR_CREEK_NEAR_GOLDEN, "Clear Creek near Golden (USGS 06719500)")]:
        try:
            rows = iv_recent(site)
            with open(f"{OUT}/iv_{site}.rdb", "w") as fh:
                fh.write(json.dumps(rows, indent=1))
            flow_lines.append(fmt_flow(rows, label))
        except Exception as e:
            flow_lines.append(f"- **{label}**: UNAVAILABLE ({e})")
    if platte:
        try:
            rows = iv_recent(platte)
            flow_lines.append(fmt_flow(rows, f"South Platte (USGS {platte})"))
        except Exception as e:
            flow_lines.append(f"- South Platte ({platte}): UNAVAILABLE ({e})")
    if cherry:
        try:
            rows = iv_recent(cherry)
            flow_lines.append(fmt_flow(rows, f"Cherry Creek (USGS {cherry})"))
        except Exception as e:
            flow_lines.append(f"- Cherry Creek ({cherry}): UNAVAILABLE ({e})")

    print("\n== water quality (WQP, last 90 days) ==")
    wq_lines = []
    for site in WQ_SITES:
        try:
            brows = wqp_results(site, BACTERIA)
            crows = wqp_results(site, CHEMISTRY)
            bstats = summarize(brows, BACTERIA)
            cstats = summarize(crows, CHEMISTRY)
            if not bstats and not cstats:
                continue
            wq_lines.append(f"### {site}")
            for ch, s in list(bstats.items()) + list(cstats.items()):
                last = s["last"]
                m7 = sum(s["n7"]) / len(s["n7"]) if s["n7"] else None
                m30 = sum(s["n30"]) / len(s["n30"]) if s["n30"] else None
                extra = ""
                if ch in BACTERIA and s["n7"]:
                    # geometric mean for bacteria
                    gm = 10 ** (sum(__import__("math").log10(max(v, 1)) for v in s["n7"]) / len(s["n7"]))
                    extra = f"; 7-day GM {gm:.0f}"
                line = (f"- {ch}: last {last[1]} {last[2]} on {last[0]}"
                        + (f" | 7d avg {m7:.1f}" if m7 is not None else "")
                        + (f" | 30d avg {m30:.1f}" if m30 is not None else "")
                        + extra)
                wq_lines.append(line)
        except Exception as e:
            wq_lines.append(f"### {site}\n- UNAVAILABLE: {e}")

    report = (f"# Golden / Confluence Park water report — {TODAY}\n\n"
              f"## Flow (surf/swim context)\n" + "\n".join(flow_lines)
              + "\n\n## Water quality (last 90 days, USGS WQP)\n"
              + ("\n".join(wq_lines) if wq_lines else "no recent data") + "\n")
    with open(f"{OUT}/report.md", "w") as fh:
        fh.write(report)
    print("\n== REPORT ==")
    print(report)
    print(f"\nDONE. Raw payloads in {OUT}/")


if __name__ == "__main__":
    import urllib.parse  # noqa: E402 (used by wqp_results via module lookup)
    main()
