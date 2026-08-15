#!/usr/bin/env python3
"""Build a working xauthority file from a mutter Xwaylandauth file and
test X connections to :0 and :1. Writes /tmp/ck_local."""
import os, glob, struct

# find newest mutter cookie file
files = sorted(glob.glob("/run/user/1000/.mutter-Xwaylandauth.*"),
               key=lambda p: os.path.getmtime(p), reverse=True)
src = files[0]
print("source:", src, flush=True)

data = open(src, "rb").read()
entries = []  # (family, hostname, proto, number, cookie)
i = 0
while i < len(data):
    fam = data[i]
    i += 1
    if fam in (0x01, 0x02):
        nlen = data[i]; i += 1
        host = data[i:i+nlen].decode(); i += nlen
    elif fam == 0x03:
        addr = data[i:i+4]; i += 4
        host = "ipv4-" + str(struct.unpack(">I", addr)[0]); i += 0
    elif fam == 0x18:
        addr = data[i:i+16]; i += 16
        host = "ipv6"
    else:
        # family 0xFFFF: 2-byte length, name; then transport, number
        l = data[i]; i += 1
        host = data[i:i+l].decode(); i += l
        tlen = data[i]; i += 1
        transport = data[i:i+tlen].decode(); i += tlen
        nlen = data[i]; i += 1
        number = data[i:i+nlen].decode(); i += nlen
        plen = data[i]; i += 1
        proto = data[i:i+plen].decode(); i += plen
        clen = data[i]; i += 1
        cookie = data[i:i+clen]; i += clen
        entries.append((0xFFFF, host, transport, number, proto, cookie))
        continue
    plen = data[i]; i += 1
    proto = data[i:i+plen].decode(); i += plen
    nlen = data[i]; i += 1
    number = data[i:i+nlen].decode(); i += nlen
    clen = data[i]; i += 1
    cookie = data[i:i+clen]; i += clen
    entries.append((fam, host, proto, number, cookie))

print(f"parsed {len(entries)} entries", flush=True)

def build_local_entry(cookie, number):
    proto = b"MIT-MAGIC-COOKIE-1"
    out = bytearray()
    out.append(0x01)              # FAMILY_LOCAL
    out.append(0)                 # hostname length 0
    out.append(len(proto)); out += proto
    nb = str(number).encode()
    out.append(len(nb)); out += nb
    out.append(len(cookie)); out += cookie
    return bytes(out)

out = b""
for e in entries:
    if e[0] == 0xFFFF:
        fam, host, transport, number, proto, cookie = e
        for num in ("0", "1"):
            if number in (num, ""):
                out += build_local_entry(cookie, num)
    else:
        fam, host, proto, number, cookie = e
        for num in ("0", "1"):
            if number in (num, ""):
                out += build_local_entry(cookie, num)

open("/tmp/ck_local", "wb").write(out)
print("wrote /tmp/ck_local", len(out), "bytes;", out.count(b"MIT-MAGIC-COOKIE-1"), "entries", flush=True)

os.environ["XAUTHORITY"] = "/tmp/ck_local"
from Xlib.display import Display
for ds in (":0", ":1"):
    try:
        d = Display(ds)
        s = d.screen()
        print(ds, "OK", s.width_in_pixels, "x", s.height_in_pixels, "depth", s.root_depth, flush=True)
        d.close()
    except Exception as e:
        print(ds, "FAIL", str(e)[:120], flush=True)
