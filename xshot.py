#!/usr/bin/env python3
"""Capture X11/Xwayland root windows + enumerate top-level windows.

Usage: python3 xshot.py <cookie_file> [out_prefix]
For each of :0 and :1, grabs the root window to PNG and lists top-level
windows with names and geometry. Native Wayland windows are NOT visible
in Xwayland; only X11 clients appear.
"""
import os, sys
import PIL.Image
from Xlib.display import Display
from Xlib import X

cookie = sys.argv[1]
prefix = sys.argv[2] if len(sys.argv) > 2 else "/home/dzyla/Code/ai-buddy/xshot"
os.environ["XAUTHORITY"] = cookie

def wm_name(win):
    try:
        nm = win.get_wm_name()
    except Exception:
        nm = None
    if nm is None:
        try:
            prop = win.get_full_property(350, 0)  # _NET_WM_NAME atom id varies; use string fallback below
            nm = prop.value if prop else None
        except Exception:
            nm = None
    return nm

for dnum in (":0", ":1"):
    print(f"===== display {dnum} =====", flush=True)
    try:
        d = Display(dnum)
    except Exception as e:
        print("connect fail:", type(e).__name__, e, flush=True)
        continue
    scr = d.screen()
    root = scr.root
    w, h = scr.width_in_pixels, scr.height_in_pixels
    depth = scr.root_depth
    print(f"root {w}x{h} depth={depth}", flush=True)
    # enumerate top-level windows
    try:
        children = root.query_tree().children
        print(f"top-level windows: {len(children)}", flush=True)
        for c in children[:40]:
            try:
                g = c.get_geometry()
            except Exception:
                continue
            nm = wm_name(c) or "(no name)"
            cls = ""
            try:
                ic = c.get_wm_class()
                if ic:
                    cls = ic[1]
            except Exception:
                pass
            print(f"  win {c.id:#x} name={nm!r} class={cls!r} "
                  f"pos=({g.x},{g.y}) size={g.width}x{g.height} map_state={g.map_state}", flush=True)
    except Exception as e:
        print("enumerate fail:", e, flush=True)
    # grab root
    try:
        img_data = root.get_image(0, 0, w, h, X.ZPixmap, 0xFFFFFFFF)
        raw = bytes(img_data.data)
        if depth == 24:
            # Xlib pads to 32-bit for ZPixmap on 32-bit clients usually; handle both
            if len(raw) == w * h * 4:
                im = PIL.Image.frombytes("RGBA", (w, h), raw, "raw", "BGRX")
            else:
                im = PIL.Image.frombytes("RGB", (w, h), raw, "raw", "BGR")
        else:
            im = PIL.Image.frombytes("RGBA", (w, h), raw, "raw", "BGRX")
        out = f"{prefix}_{dnum.strip(':')}.png"
        im.save(out)
        print(f"saved {out} ({len(raw)} bytes raw)", flush=True)
    except Exception as e:
        print("grab fail:", type(e).__name__, e, flush=True)
    d.close()
