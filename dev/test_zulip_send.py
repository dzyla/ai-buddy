#!/usr/bin/env python3
"""Verify Zulip DM delivery path used by water_report.py."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import zulip_mcp_server as zms
dest = os.environ.get("AI_REMINDER_ZULIP_TO", "user1091223@zylalab.zulipchat.com")
res = zms.do_send_message({
    "message_type": "private",
    "to": dest,
    "content": "🌊 **River Watch** — Zulip delivery test (from water_report setup). "
               "You will get daily flow + water-quality reports here.",
})
print("RESULT:", res)
