#!/usr/bin/env python3
"""Resolve USGS SW station numbers via siteName (2 words), with retries (service is flaky)."""
import time, urllib.request

def site_query(name, tries=5):
    url = (f"https://waterservices.usgs.gov/nwis/site/?format=rdb"
           f"&siteName={name.replace(' ', '%20')}&stateCd=CO")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "water-report/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode()
            rows = [l.split("\t") for l in body.splitlines()
                    if not l.startswith("#") and l.strip()]
            if len(rows) >= 2 and rows[0][0] == "USGS":
                return rows
        except Exception as e:
            print(f"  retry {i+1} for {name!r}: {e}")
        time.sleep(3)
    return []

for name in ("clear creek", "cherry creek", "south platte"):
    rows = site_query(name)
    print(f"== {name}: {len(rows)-1 if rows else 0} sites")
    for r in rows:
        if r[0] != "USGS":
            print("  ", r[1], "|", r[2], "| lat", r[4], "lon", r[5])
    print()
