#!/usr/bin/env python3
"""Dump a previous session's messages for post-mortem."""
import json, sys

path = sys.argv[1] if len(sys.argv) > 1 else "/home/dzyla/.cache/ai/sessions/sess_1786907931_3202638.json"
d = json.load(open(path))
msgs = d.get("messages") if isinstance(d, dict) else d
print("N messages:", len(msgs))
for i, m in enumerate(msgs):
    role = m.get("role", "?")
    c = m.get("content")
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    c = (c or "").strip()
    print(f"\n===== [{i}] {role} ({len(c)} chars) =====")
    print(c[:2200])
