#!/usr/bin/env python3
"""Minimal mock OpenAI-compatible server for offline testing of the `ai` binary.

It records every incoming /v1/chat/completions request body to a capture file
(path from MOCK_CAPTURE env, default ./last_request.json) and returns a canned,
valid completion. This lets tests assert what the C binary *sent* (system prompt,
message history, tools) without needing a real LLM.

The reply is controlled by env vars:
  MOCK_REPLY_CONTENT  — assistant text to return (default: "MOCK_OK")
  MOCK_CAPTURE        — file to append each request body to (JSON per line)
  MOCK_TOOL_SEQ       — JSON list of tool calls, one per request (an item may
                        itself be a list = parallel tool calls in one turn; an
                        object without "function", e.g. {"content": "...",
                        "finish_reason": "length"}, is a plain text reply)
  MOCK_N_CTX          — if set, GET /props reports this n_ctx
  MOCK_TIMES          — file to append each request's arrival time (epoch secs) to

Usage: MOCK_CAPTURE=/tmp/cap.jsonl python3 mock_llm_server.py <port>
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    _seq_idx = 0  # index into MOCK_TOOL_SEQ for the current server process

    def log_message(self, *args):
        pass  # silence

    def _capture(self, body):
        cap = os.environ.get("MOCK_CAPTURE")
        if cap:
            # Re-dump compact so each captured request is exactly one JSON line,
            # even if the raw body contained newlines.
            try:
                line = json.dumps(json.loads(body))
            except Exception:
                line = json.dumps({"_unparsed": body})
            with open(cap, "a") as f:
                f.write(line + "\n")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        if os.environ.get("MOCK_TIMES"):
            with open(os.environ["MOCK_TIMES"], "a") as f:
                f.write("%.6f\n" % time.time())
        self._capture(raw)
        try:
            body = json.loads(raw)
        except Exception:
            body = {}
        stream = bool(body.get("stream"))

        content = os.environ.get("MOCK_REPLY_CONTENT", "MOCK_OK")
        summary = os.environ.get("MOCK_TASK_COMPLETE")
        tool_call_env = os.environ.get("MOCK_TOOL_CALL")
        seq_env = os.environ.get("MOCK_TOOL_SEQ")
        seq = json.loads(seq_env) if seq_env else None

        msgs = body.get("messages", [])
        last_is_tool = msgs and msgs[-1].get("role") == "tool"

        tc = None
        if seq is not None:
            # MOCK_TOOL_SEQ: drive a deterministic tool sequence. Consume one
            # item per LLM request regardless of message history so the loop can
            # span multiple tool calls; when the sequence is exhausted, fall
            # through to the normal content/task_complete reply.
            if Handler._seq_idx < len(seq):
                tc = seq[Handler._seq_idx]
                Handler._seq_idx += 1
        elif tool_call_env and not last_is_tool:
            tc = json.loads(tool_call_env)

        if isinstance(tc, dict) and "function" not in tc:
            # A scripted plain reply (e.g. a truncated answer with finish_reason "length").
            text, finish = tc.get("content", ""), tc.get("finish_reason", "stop")
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self._sse({"role": "assistant", "content": ""})
                self._sse({"content": text})
                self._sse({}, finish=finish)
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                resp = {"id": "chatcmpl-mock", "object": "chat.completion", "created": 0, "model": "mock",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                     "finish_reason": finish}]}
                payload = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            return

        if tc is not None:
            # A MOCK_TOOL_SEQ item may be a list -> parallel tool calls in one turn.
            tcs = tc if isinstance(tc, list) else [tc]
            self.send_response(200)
            if stream:
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                for idx, one in enumerate(tcs):
                    fn = one.get("function", {})
                    self._sse({"role": "assistant", "content": None,
                               "tool_calls": [{"index": idx, "id": one.get("id", "call_mock_%d" % (idx + 1)),
                                               "type": "function",
                                               "function": {"name": fn.get("name", ""), "arguments": ""}}]})
                    self._sse({"tool_calls": [{"index": idx,
                               "function": {"arguments": fn.get("arguments", "")}}]})
                self._sse({}, finish="tool_calls")
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self.send_header("Content-Type", "application/json")
                resp = {
                    "id": "chatcmpl-mock", "object": "chat.completion", "created": 0, "model": "mock",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": tcs}, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
                payload = json.dumps(resp).encode()
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            return

        if summary is None and last_is_tool:
            summary = "Done"

        if stream:
            self._respond_stream(summary, content)
        else:
            self._respond_json(summary, content)

    def _respond_json(self, summary, content):
        if summary is not None:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_mock_1", "type": "function",
                    "function": {"name": "task_complete",
                                 "arguments": json.dumps({"summary": summary})},
                }],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": content}
            finish = "stop"
        resp = {
            "id": "chatcmpl-mock", "object": "chat.completion", "created": 0,
            "model": "mock",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        payload = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _sse(self, delta, finish=None):
        chunk = {"id": "chatcmpl-mock", "object": "chat.completion.chunk",
                 "created": 0, "model": "mock",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")

    def _respond_stream(self, summary, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if summary is not None:
            # Emit a task_complete tool call split across delta chunks like a real API.
            self._sse({"role": "assistant", "content": None,
                       "tool_calls": [{"index": 0, "id": "call_mock_1",
                                       "type": "function",
                                       "function": {"name": "task_complete", "arguments": ""}}]})
            self._sse({"tool_calls": [{"index": 0,
                       "function": {"arguments": json.dumps({"summary": summary})}}]})
            self._sse({}, finish="tool_calls")
        else:
            self._sse({"role": "assistant", "content": ""})
            self._sse({"content": content})
            self._sse({}, finish="stop")
        self.wfile.write(b"data: [DONE]\n\n")

    def do_GET(self):
        # /v1/models or health probe. MOCK_N_CTX makes /props report a
        # llama.cpp-style runtime context window (n_ctx).
        n_ctx = os.environ.get("MOCK_N_CTX")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if n_ctx and self.path.startswith("/props"):
            self.wfile.write(json.dumps({"default_generation_settings": {"n_ctx": int(n_ctx)}}).encode())
            return
        self.wfile.write(b'{"data":[{"id":"mock"}]}')


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8919
    # Threading so the binary's streaming SSE + a follow-up request (next agent
    # loop iteration) never deadlock on keep-alive under load.
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
