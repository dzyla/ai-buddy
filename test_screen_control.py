#!/usr/bin/env python3
"""Test all screen control capabilities."""

import subprocess
import sys
import os

print("=" * 60)
print("SCREEN CONTROL TEST SUITE")
print("=" * 60)

results = []

# Test 1: CLI tools availability
print("\n[1] CLI Tools Check")
tools = ["xdotool", "xclip", "scrot", "wmctrl", "gnome-screenshot"]
for t in tools:
    path = subprocess.run(["which", t], capture_output=True, text=True).stdout.strip()
    status = f"✅ {t} -> {path}" if path else f"❌ {t} -> NOT FOUND"
    results.append(status)
    print(f"   {status}")

# Test 2: Python libraries
print("\n[2] Python Libraries Check")
pylibs = [("mss", "mss"), ("PIL", "PIL")]
for short, lib in pylibs:
    try:
        __import__(lib)
        results.append(f"✅ {short} -> import OK")
        print(f"   ✅ {short} -> import OK")
    except ImportError as e:
        results.append(f"❌ {short} -> {e}")
        print(f"   ❌ {short} -> {e}")

# pyautogui is special — mouseinfo connects to display on import, so test separately

# Test 3: Screenshot with scrot
print("\n[3] Screenshot (scrot)")
try:
    out = subprocess.run(["scrot", "/tmp/test_screenshot.png", "--delay=1"],
                         capture_output=True, text=True, timeout=10)
    if out.returncode == 0 and os.path.exists("/tmp/test_screenshot.png"):
        size = os.path.getsize("/tmp/test_screenshot.png")
        results.append(f"✅ Screenshot -> /tmp/test_screenshot.png ({size} bytes)")
        print(f"   ✅ Screenshot saved ({size} bytes)")
    else:
        results.append(f"❌ Screenshot failed: {out.stderr}")
        print(f"   ❌ Screenshot failed: {out.stderr}")
except Exception as e:
    results.append(f"❌ Screenshot: {e}")
    print(f"   ❌ Screenshot: {e}")

# Test 4: Screenshot with mss (Python)
print("\n[4] Screenshot (mss Python)")
try:
    import mss
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        results.append(f"✅ MSS screenshot -> {shot.width}x{shot.height}")
        print(f"   ✅ MSS screenshot -> {shot.width}x{shot.height}")
except Exception as e:
    results.append(f"❌ MSS: {e}")
    print(f"   ❌ MSS: {e}")

# Test 5: xdotool mouse position
print("\n[5] xdotool (mouse position)")
try:
    out = subprocess.run(["xdotool", "getmouselocation"], capture_output=True, text=True)
    if out.returncode == 0:
        results.append(f"✅ xdotool getmouselocation -> {out.stdout.strip()}")
        print(f"   ✅ xdotool getmouselocation -> {out.stdout.strip()}")
    else:
        results.append(f"❌ xdotool: {out.stderr}")
        print(f"   ❌ xdotool: {out.stderr}")
except Exception as e:
    results.append(f"❌ xdotool: {e}")
    print(f"   ❌ xdotool: {e}")

# Test 6: xdotool key simulation (safe — just 'Tab' once)
print("\n[6] xdotool (key press - Tab)")
try:
    out = subprocess.run(["xdotool", "key", "Tab"], capture_output=True, text=True)
    if out.returncode == 0:
        results.append("✅ xdotool key press works")
        print(f"   ✅ xdotool key press works")
    else:
        results.append(f"❌ xdotool key: {out.stderr}")
        print(f"   ❌ xdotool key: {out.stderr}")
except Exception as e:
    results.append(f"❌ xdotool key: {e}")
    print(f"   ❌ xdotool key: {e}")

# Test 7: xdotool mouse move + click
print("\n[7] xdotool (mouse move + click)")
try:
    # Move to a safe corner (100, 100) and click
    out = subprocess.run(
        ["xdotool", "mousemove", "100", "100", "mouseclick", "left"],
        capture_output=True, text=True
    )
    if out.returncode == 0:
        results.append("✅ xdotool mouse move + click works")
        print(f"   ✅ xdotool mouse move + click works")
    else:
        results.append(f"❌ xdotool mouse: {out.stderr}")
        print(f"   ❌ xdotool mouse: {out.stderr}")
except Exception as e:
    results.append(f"❌ xdotool mouse: {e}")
    print(f"   ❌ xdotool mouse: {e}")

# Test 8: xclip clipboard
print("\n[8] xclip (clipboard write/read)")
try:
    # Write
    write = subprocess.run(
        ["bash", "-c", "echo 'hello from buddy' | xclip -selection clipboard"],
        capture_output=True, text=True
    )
    # Read
    read = subprocess.run(
        ["xclip", "-selection", "clipboard", "-o"],
        capture_output=True, text=True
    )
    if read.returncode == 0 and "hello from buddy" in read.stdout:
        results.append(f'✅ xclip clipboard -> read back: "{read.stdout.strip()}"')
        print(f'   ✅ xclip clipboard -> read back: "{read.stdout.strip()}"')
    else:
        results.append(f"❌ xclip: write={write.returncode}, read={read.stdout}")
        print(f"   ❌ xclip: write={write.returncode}, read={read.stdout}")
except Exception as e:
    results.append(f"❌ xclip: {e}")
    print(f"   ❌ xclip: {e}")

# Test 9: wmctrl window listing
print("\n[9] wmctrl (window list)")
try:
    out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True)
    if out.returncode == 0:
        windows = out.stdout.strip().split('\n') if out.stdout.strip() else ['(none)']
        results.append(f"✅ wmctrl -> {len(windows)} window(s) found")
        print(f"   ✅ wmctrl -> {len(windows)} window(s) found")
        for w in windows[:5]:
            print(f"      {w}")
    else:
        results.append(f"❌ wmctrl: {out.stderr}")
        print(f"   ❌ wmctrl: {out.stderr}")
except Exception as e:
    results.append(f"❌ wmctrl: {e}")
    print(f"   ❌ wmctrl: {e}")

# Test 10: pyautogui version
print("\n[10] pyautogui (version check)")
try:
    import pyautogui
    results.append(f"✅ pyautogui {pyautogui.__version__}")
    print(f"   ✅ pyautogui {pyautogui.__version__}")
    # Test pyautogui mouse position (non-destructive)
    pos = pyautogui.position()
    results.append(f"✅ pyautogui.position() -> {pos}")
    print(f"   ✅ pyautogui.position() -> {pos}")
except Exception as e:
    results.append(f"⚠️ pyautogui: {e} (likely Wayland/X11 auth issue — CLI tools still work)")
    print(f"   ⚠️ pyautogui: {e}")

# Summary
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
passed = sum(1 for r in results if r.startswith("✅"))
failed = sum(1 for r in results if r.startswith("❌"))
for r in results:
    print(f"  {r}")
print(f"\n  {passed} passed, {failed} failed out of {len(results)} tests")

# Cleanup
if os.path.exists("/tmp/test_screenshot.png"):
    os.remove("/tmp/test_screenshot.png")
    print("  🧹 Cleaned up test_screenshot.png")

sys.exit(0 if failed == 0 else 1)
