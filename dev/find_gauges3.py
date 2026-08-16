#!/usr/bin/env python3
"""Find USGS NWIS SW gauges by bounding box (format=rdb — the site service rejects format=json)."""
import urllib.request

def bbox(minlon, minlat, maxlon, maxlat):
    url = (f"https://waterservices.usgs.gov/nwis/site/?format=rdb"
           f"&siteType=SW&bBox={minlon},{minlat},{maxlon},{maxlat}")
    req = urllib.request.Request(url, headers={"User-Agent": "water-report/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        lines = [l.decode().rstrip() for l in r]
    if not lines or lines[0].startswith("#"):
        # rdb: first non-# line is header
        hdr = None
        for l in lines:
            if not l.startswith("#"):
                hdr = l.split("\t")
                break
        rows = [l.split("\t") for l in lines if not l.startswith("#") and l and len(l.split("\t")) == len(hdr)]
        return hdr, rows
    return None, []

hdr, rows = bbox(-105.35, 39.65, -104.80, 39.95)
if not hdr:
    print("no data"); raise SystemExit(1)
print(f"total SW gauges in box: {len(rows)}")
for i, r in enumerate(rows):
    d = dict(zip(hdr, r))
    name = d.get("Site_name", "")
    if any(k in name.lower() for k in ("south platte", "cherry creek", "clear creek", "golden")):
        print(f'{d["Site_no"]:>10}  {d.get("Latitude", "")} {d.get("Longitude", "")}  {name}')
