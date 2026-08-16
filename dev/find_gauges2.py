#!/usr/bin/env python3
"""Find USGS NWIS surface-water gauges near Confluence Park, River Run Park, and Golden."""
import json, urllib.request, urllib.parse

def sites(query, lat_lo, lat_hi, lon_lo, lon_hi):
    url = ("https://waterservices.usgs.gov/nwis/site/?format=json"
           "&siteName=" + urllib.parse.quote(query)
           + "&stateCd=CO&siteType=SW&dataProfile=flow,all")
    req = urllib.request.Request(url, headers={"User-Agent": "water-report/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    out = []
    for s in d["value"]["sites"]:
        lat, lon = s.get("lat"), s.get("lon")
        if lat is None:
            continue
        if lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi:
            out.append((s["siteNo"], s.get("name", ""), round(lat, 4), round(lon, 4)))
    return out

print("== SOUTH PLATTE (Denver + Golden box)")
for row in sites("south platte river", 39.60, 39.80, -105.35, -104.80):
    print(row)
print("\n== CHERRY CREEK (Denver + Golden box)")
for row in sites("cherry creek", 39.55, 40.00, -105.35, -104.80):
    print(row)
print("\n== CLEAR CREEK (Golden box)")
for row in sites("clear creek", 39.60, 40.00, -105.45, -105.10):
    print(row)
print("\n== GOLDEN (any river, exact names)")
url = ("https://waterservices.usgs.gov/nwis/site/?format=json&stateCd=CO&siteType=SW"
       "&bBox=-105.35,39.68,-105.15,39.80")
req = urllib.request.Request(url, headers={"User-Agent": "water-report/1.0"})
with urllib.request.urlopen(req, timeout=60) as r:
    d = json.load(r)
for s in d["value"]["sites"]:
    print(s["siteNo"], "|", s.get("name", ""))
