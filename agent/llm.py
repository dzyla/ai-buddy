"""OpenAI-compatible chat client: request payload (byte-compatible fields with ai.c), SSE parsing
(including tool-call argument fragments spread over many deltas), retries, context-window probe.

chat(messages, tools, stream=True, on_chunk=None, client=None) -> dict
    {"message": assistant message, "finish_reason": str|None, "usage": {...}, "status": http status,
     "error": str|None (transport/HTTP failure, message is then empty), "interrupted": bool}
"""
import http.client
import json
import os
import socket
import threading
import time
import urllib.parse

from .cli import Options


# ── payload ──────────────────────────────────────────────────────────────────
def _c2(x):
    """ai.c keeps sampling values as C floats and prints them with "%.2f"."""
    import struct
    return float("%.2f" % struct.unpack("f", struct.pack("f", x))[0])


def build_payload(model, messages, tools, opts, stream=True):
    """The request body ai.c builds (field set, order and "%.2f" float rounding)."""
    p = {"model": model, "stream": bool(stream)}
    if stream:
        p["stream_options"] = {"include_usage": True}
    if opts.temperature >= 0.0:
        p["temperature"] = _c2(opts.temperature)
    if opts.top_p >= 0.0:
        p["top_p"] = _c2(opts.top_p)
    if opts.top_k > 0:
        p["top_k"] = opts.top_k
    if opts.min_p >= 0.0:
        p["min_p"] = _c2(opts.min_p)
    if opts.max_tokens > 0:
        p["max_tokens"] = opts.max_tokens
    if opts.frequency_penalty > 0.0:
        p["frequency_penalty"] = _c2(opts.frequency_penalty)
    if opts.presence_penalty > 0.0:
        p["presence_penalty"] = _c2(opts.presence_penalty)
    if opts.reasoning_effort:
        p["reasoning_effort"] = opts.reasoning_effort
    if opts.reasoning_budget >= 0:
        p["reasoning_budget"] = opts.reasoning_budget
    if opts.preserve_thinking:
        p["preserve_thinking"] = True
    p["messages"] = messages
    if tools:
        p["tools"] = tools
        p["tool_choice"] = opts.tool_choice
    return p


# ── SSE ──────────────────────────────────────────────────────────────────────
class SSEAccumulator:
    """Incremental `data:` line parser. on_event(kind, text) fires for "content", "reasoning" and
    "toolcall" (first tool-call delta of a response, text "") as chunks arrive."""

    def __init__(self, on_event=None):
        self.on_event = on_event
        self._buf = b""
        self.raw = b""
        self.content = None
        self.reasoning = None
        self.calls = []          # [{"index", "id", "name", "arguments"}] in first-seen order
        self.finish_reason = None
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.meta = {}
        self.saw_data = False
        self._toolcall_seen = False

    def _emit(self, kind, text):
        if self.on_event:
            try:
                self.on_event(kind, text)
            except Exception:
                pass

    def feed(self, data):
        self.raw += data
        self._buf += data
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            self._line(line)

    def _line(self, line):
        line = line.strip()
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if not data or data.startswith(b"[DONE]"):
            return
        try:
            obj = json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return
        if isinstance(obj, dict):
            self.saw_data = True
            self._chunk(obj)

    def _call(self, index):
        for c in self.calls:
            if c["index"] == index:
                return c
        if len(self.calls) >= 64:
            return None
        c = {"index": index, "id": None, "name": None, "arguments": None}
        self.calls.append(c)
        return c

    def _chunk(self, obj):
        for k in ("id", "object", "model"):
            if isinstance(obj.get(k), str) and k not in self.meta:
                self.meta[k] = obj[k]
        if isinstance(obj.get("created"), int) and "created" not in self.meta:
            self.meta["created"] = obj["created"]
        u = obj.get("usage")
        if isinstance(u, dict):
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if isinstance(u.get(k), int):
                    self.usage[k] = u[k]
        ch = obj.get("choices")
        if not isinstance(ch, list) or not ch or not isinstance(ch[0], dict):
            return
        choice = ch[0]
        if isinstance(choice.get("finish_reason"), str) and self.finish_reason is None:
            self.finish_reason = choice["finish_reason"]
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return
        rc = delta.get("reasoning_content")
        if isinstance(rc, str):
            self.reasoning = (self.reasoning or "") + rc
            self._emit("reasoning", rc)
        cc = delta.get("content")
        if isinstance(cc, str):
            self.content = (self.content or "") + cc
            self._emit("content", cc)
        tcs = delta.get("tool_calls")
        if isinstance(tcs, list):
            if not self._toolcall_seen:
                self._toolcall_seen = True
                self._emit("toolcall", "")
            for n, tc in enumerate(tcs):
                if not isinstance(tc, dict):
                    break
                idx = tc.get("index") if isinstance(tc.get("index"), int) else n
                c = self._call(idx)
                if c is None:
                    continue
                if isinstance(tc.get("id"), str) and tc["id"] and not c["id"]:
                    c["id"] = tc["id"]
                fn = tc.get("function")
                if isinstance(fn, dict):
                    name = fn.get("name")
                    if isinstance(name, str) and name:
                        if not c["name"]:
                            c["name"] = name
                        elif c["name"] != name:
                            c["name"] += name
                    a = fn.get("arguments")
                    if isinstance(a, str) and a:
                        c["arguments"] = (c["arguments"] or "") + a

    def flush(self):
        if self._buf:
            self._line(self._buf)
            self._buf = b""

    def result(self):
        """reconstruct_final_json(): the assistant message and finish reason."""
        self.flush()
        valid = [c for c in self.calls if c["name"] and c["name"].strip(" \t\r\n")]
        msg = {"role": "assistant", "content": self.content}
        if self.reasoning is not None:
            msg["reasoning_content"] = self.reasoning
        if valid:
            msg["tool_calls"] = [{"id": c["id"] or "", "type": "function",
                                  "function": {"name": c["name"], "arguments": c["arguments"] or ""}}
                                 for c in valid]
        fin = self.finish_reason
        if not valid and fin == "tool_calls":
            fin = "stop"
        return {"message": msg, "finish_reason": fin, "usage": dict(self.usage),
                "model": self.meta.get("model")}


def parse_json_response(body):
    """A non-streamed chat.completion body -> the same shape as SSEAccumulator.result()."""
    obj = json.loads(body)
    choice = (obj.get("choices") or [{}])[0]
    m = dict(choice.get("message") or {})
    msg = {"role": "assistant", "content": m.get("content") if isinstance(m.get("content"), str) else None}
    if isinstance(m.get("reasoning_content"), str):
        msg["reasoning_content"] = m["reasoning_content"]
    calls = [c for c in (m.get("tool_calls") or []) if isinstance(c, dict)
             and (c.get("function") or {}).get("name", "").strip()]
    if calls:
        msg["tool_calls"] = [{"id": c.get("id") or "", "type": "function",
                              "function": {"name": c["function"]["name"],
                                           "arguments": c["function"].get("arguments") or ""}} for c in calls]
    fin = choice.get("finish_reason")
    if not calls and fin == "tool_calls":
        fin = "stop"
    usage = {k: int((obj.get("usage") or {}).get(k) or 0)
             for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    return {"message": msg, "finish_reason": fin, "usage": usage, "model": obj.get("model")}


# ── transport ────────────────────────────────────────────────────────────────
def _is_local(url):
    return "localhost" in url or "127.0.0.1" in url


class Client:
    """One endpoint (api_url ends in chat/completions). interrupt(): abort the request in flight."""

    def __init__(self, api_url, api_key, model, opts=None, debug=False, log=None):
        self.api_url, self.api_key, self.model = api_url, api_key, model
        self.opts = opts or Options()
        self.debug = debug
        self.log = log or (lambda s: None)
        self._conn = None
        self._lock = threading.Lock()
        self._interrupted = False

    def interrupt(self):
        self._interrupted = True
        with self._lock:
            conn = self._conn
        if conn is not None and conn.sock is not None:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _open(self, url, timeout=None):
        u = urllib.parse.urlsplit(url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = cls(u.hostname, u.port, timeout=timeout)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        return conn, path

    def get_json_text(self, url, timeout=10):
        """GET url -> body text, or None (with ai.c's local-server startup retries)."""
        for attempt in range(60 if _is_local(url) else 1):
            try:
                conn, path = self._open(url, timeout=timeout)
                try:
                    conn.request("GET", path, headers={"Authorization": "Bearer %s" % self.api_key})
                    r = conn.getresponse()
                    body = r.read().decode("utf-8", "replace")
                finally:
                    conn.close()
                if "Loading model" in body and _is_local(url):
                    time.sleep(0.5)
                    continue
                return body
            except (OSError, http.client.HTTPException):
                if not _is_local(url) or attempt == 59:
                    return None
                time.sleep(0.5)
        return None

    def chat(self, messages, tools, stream=True, on_chunk=None, debug_tag=None):
        payload = build_payload(self.model, messages, tools, self.opts, stream=stream)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if self.debug:
            self.log("[debug] Loop %s payload: %s\n" % (debug_tag, body.decode("utf-8", "replace")))
        self._interrupted = False
        local = _is_local(self.api_url)
        local_tries, server_tries = 0, 0
        warned = False
        while True:
            acc = SSEAccumulator(on_event=on_chunk)
            try:
                status, err = self._post(body, acc, stream)
            except (OSError, http.client.HTTPException) as e:
                if self._interrupted:
                    return self._done(acc, stream, interrupted=True)
                transient = isinstance(e, (ConnectionRefusedError, ConnectionResetError, socket.timeout,
                                           http.client.RemoteDisconnected, BrokenPipeError))
                if local and local_tries < 60 and transient:
                    local_tries += 1
                    if not warned:
                        self.log("\033[2m[ai] Local server is starting up, waiting...\033[0m\n")
                        warned = True
                    time.sleep(0.5)
                    continue
                if not local and isinstance(e, (ConnectionResetError, http.client.RemoteDisconnected)) \
                        and server_tries < 3:
                    server_tries += 1
                    time.sleep(2 ** server_tries / 2)
                    continue
                return {"message": {"role": "assistant", "content": None}, "finish_reason": None,
                        "usage": {}, "error": str(e) or e.__class__.__name__, "connect_failed":
                        isinstance(e, (ConnectionRefusedError, socket.gaierror, socket.timeout)),
                        "interrupted": False, "status": 0}
            if self._interrupted:
                return self._done(acc, stream, interrupted=True)
            if local and b"Loading model" in acc.raw and local_tries < 60:
                local_tries += 1
                if local_tries == 1:
                    self.log("\033[2m[ai] Local server model is loading, waiting...\033[0m\n")
                time.sleep(0.5)
                continue
            if status >= 500 and server_tries < 3:
                server_tries += 1
                time.sleep(2 ** server_tries / 2)
                continue
            res = self._done(acc, stream)
            res["status"] = status
            if status >= 400:
                res["error"] = err or ("HTTP %d" % status)
            if self.debug:
                self.log("[debug] Loop %s response: %s\n" % (debug_tag, json.dumps(res)))
            return res

    def _done(self, acc, stream, interrupted=False):
        if stream or not acc.raw:
            res = acc.result()
        else:
            try:
                res = parse_json_response(acc.raw.decode("utf-8", "replace"))
            except (ValueError, AttributeError, IndexError, TypeError):
                res = acc.result()
        res["interrupted"] = interrupted
        res.setdefault("error", None)
        return res

    def _post(self, body, acc, stream):
        conn, path = self._open(self.api_url, timeout=None)
        with self._lock:
            self._conn = conn
        try:
            conn.request("POST", path, body=body, headers={
                "Content-Type": "application/json", "Authorization": "Bearer %s" % self.api_key})
            r = conn.getresponse()
            err = None
            if stream and r.status < 400:
                while True:
                    chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
                    if not chunk:
                        break
                    acc.feed(chunk)
                    if self._interrupted:
                        break
            else:
                data = r.read()
                acc.raw += data
                if r.status >= 400:
                    err = _error_message(data)
                elif stream:
                    acc.feed(data)
            return r.status, err
        finally:
            with self._lock:
                self._conn = None
            conn.close()


def _error_message(data):
    try:
        obj = json.loads(data.decode("utf-8", "replace"))
        e = obj.get("error")
        if isinstance(e, dict):
            return str(e.get("message") or e)
        if e:
            return str(e)
    except (ValueError, AttributeError):
        pass
    return data.decode("utf-8", "replace")[:500] or None


def _int_field(text, key):
    """extract_json_int_field(): first `"key": <digits>` anywhere in the text."""
    import re
    m = re.search(r'"%s"\s*:\s*(\d+)' % re.escape(key), text or "")
    return int(m.group(1)) if m else 0


def detect_context_window(client):
    """llama.cpp /props n_ctx, else <base>models max_model_len|context_length|context_window|n_ctx."""
    url = client.api_url
    u = urllib.parse.urlsplit(url)
    win = 0
    if u.scheme and u.netloc:
        body = client.get_json_text("%s://%s/props" % (u.scheme, u.netloc))
        win = _int_field(body, "n_ctx")
    if win <= 0 and "chat/completions" in url:
        body = client.get_json_text(url[:url.index("chat/completions")] + "models")
        for k in ("max_model_len", "context_length", "context_window", "n_ctx"):
            win = _int_field(body, k)
            if win > 0:
                break
    return win


def chat(messages, tools, stream=True, on_chunk=None, client=None):
    """Module-level convenience: chat through `client`, or one configured from INFER_* env."""
    if client is None:
        base = os.environ.get("INFER_BASE_URL", "")
        client = Client(base + ("" if base.endswith("/") else "/") + "chat/completions",
                        os.environ.get("INFER_API_KEY", ""), os.environ.get("INFER_MODEL", ""))
    return client.chat(messages, tools, stream=stream, on_chunk=on_chunk)
