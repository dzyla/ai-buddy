#!/usr/bin/env python3
"""Tiny OpenAI-compatible embedding server for offline tests (and `run_evals.py --retrieval`).

POST /v1/embeddings {"input": str | [str]} -> {"data": [{"index": i, "embedding": [768 floats]}]}
GET  /health -> {"status": "ok"}

Vectors are deterministic hashed bags of words: the EmbeddingGemma prompt prefixes
("task: search result | query: ", "title: ... | text: ") are stripped, each lowercased word is cut
to its first 5 characters (a crude stemmer, so "deploying" ~ "deploys", which FTS5's unicode61
tokenizer does NOT match) and hashed to 3 signed dimensions (one always in the first 128, so the
Matryoshka 128-d prefix stays informative), then L2-normalised. Cosine similarity is therefore
word(stem) overlap.

In-process: `srv = start(); srv.url; srv.requests; srv.delay = 0.3; srv.stop()`.
Standalone: `python3 fake_embedder.py PORT`.
"""
import hashlib
import json
import math
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 768
_QUERY_RE = re.compile(r"^task: [^|]*\| query: ", re.S)
_DOC_RE = re.compile(r"^title: (.*?) \| text: ", re.S)


def _strip_prefix(text):
    text = str(text)
    m = _QUERY_RE.match(text)
    if m:
        return text[m.end():]
    m = _DOC_RE.match(text)
    if m:
        title = m.group(1)
        return ("" if title == "none" else title + " ") + text[m.end():]
    return text


def vector(text):
    v = [0.0] * DIM
    for w in re.findall(r"\w+", _strip_prefix(text).lower()):
        stem = w[:5]
        h = hashlib.md5(stem.encode("utf-8")).digest()
        idx = (h[0] % 128, 128 + (int.from_bytes(h[1:3], "little") % (DIM - 128)),
               int.from_bytes(h[3:5], "little") % DIM)
        for j, i in enumerate(idx):
            v[i] += 1.0 if (h[5 + j] & 1) else -1.0
    n = math.sqrt(sum(x * x for x in v))
    if n == 0:
        v[DIM - 1] = 1.0
        return v
    return [x / n for x in v]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/v1/health"):
            return self._send(200, {"status": "ok"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        srv = self.server
        if srv.delay:
            import time
            time.sleep(srv.delay)
        if self.path.rstrip("/") not in ("/v1/embeddings", "/embeddings"):
            return self._send(404, {"error": "not found"})
        inputs = req.get("input")
        if isinstance(inputs, str):
            inputs = [inputs]
        with srv.lock:
            srv.requests.append(list(inputs or []))
        data = [{"object": "embedding", "index": i, "embedding": vector(t)}
                for i, t in enumerate(inputs or [])]
        self._send(200, {"object": "list", "data": data, "model": req.get("model") or "fake"})


class FakeEmbedder:
    def __init__(self, port=0, delay=0.0):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.requests = []
        self.httpd.lock = threading.Lock()
        self.httpd.delay = delay
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.httpd.server_address[1]

    @property
    def requests(self):
        """One list of input strings per /v1/embeddings call."""
        return self.httpd.requests

    @property
    def delay(self):
        return self.httpd.delay

    @delay.setter
    def delay(self, value):
        self.httpd.delay = value

    def texts(self):
        return [t for r in self.requests for t in r]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def start(port=0, delay=0.0):
    return FakeEmbedder(port, delay)


if __name__ == "__main__":
    srv = FakeEmbedder(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(srv.url, flush=True)
    try:
        srv.thread.join()
    except KeyboardInterrupt:
        srv.stop()
