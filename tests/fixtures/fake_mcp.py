#!/usr/bin/env python3
"""Tiny stdio JSON-RPC MCP server for offline tool-server tests.

Tools: `echo` (returns its `text` argument), `pid` (returns this process's PID), `sleep` (sleeps
`seconds`, then returns "slept"), `crash` (appends a line to `file`, then dies without answering).
On start it appends its PID to $FAKE_MCP_PIDFILE (one per line), so a test can count how many
times the pool started it, then sleeps $FAKE_MCP_START_DELAY seconds (a slow start).
"""
import json
import os
import sys
import time

TOOLS = [
    {"name": "echo", "description": "Echo text back.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "pid", "description": "Return the server PID.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "sleep", "description": "Sleep for `seconds`.",
     "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}}},
    {"name": "crash", "description": "Append a line to `file`, then exit without answering.",
     "inputSchema": {"type": "object", "properties": {"file": {"type": "string"}}}},
]


def _reply(rid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()


def _text(t):
    return {"content": [{"type": "text", "text": t}]}


def main():
    pidfile = os.environ.get("FAKE_MCP_PIDFILE")
    if pidfile:
        with open(pidfile, "a") as f:
            f.write("%d\n" % os.getpid())
    time.sleep(float(os.environ.get("FAKE_MCP_START_DELAY") or 0))
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method, rid = req.get("method"), req.get("id")
        if rid is None:
            continue  # notification
        if method == "initialize":
            _reply(rid, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                         "serverInfo": {"name": "fake", "version": "1"}})
        elif method == "tools/list":
            _reply(rid, {"tools": TOOLS})
        elif method == "tools/call":
            p = req.get("params") or {}
            name, args = p.get("name"), p.get("arguments") or {}
            if name == "echo":
                _reply(rid, _text(str(args.get("text", ""))))
            elif name == "pid":
                _reply(rid, _text(str(os.getpid())))
            elif name == "crash":
                with open(args["file"], "a") as f:
                    f.write("ran\n")
                os._exit(3)
            elif name == "sleep":
                time.sleep(float(args.get("seconds", 1)))
                _reply(rid, _text("slept"))
            else:
                _reply(rid, {"isError": True, "content": [{"type": "text", "text": "unknown tool"}]})
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                         "error": {"code": -32601, "message": "no such method"}}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
