"""Client for the local embedding server (EmbeddingGemma-2 on llama.cpp, CPU only).

Server: `llama-server --embeddings --pooling mean -c 8192` on 127.0.0.1:8091, run by the
ai-embed user unit (`ai-backend embed serve`). $AI_EMBED_URL overrides the base URL and
$AI_EMBED_MODEL the GGUF path; both are read at call time.

embed(texts, kind) -> list of 768-d L2-normalised vectors, or None whenever the server is
unreachable/slow/broken (never raises). Queries get a 0.5 s budget in total (connect + answer), so
the ai startup path (auto-recall) degrades to the plain memory render instead of stalling.

Prompt prefixes follow the model card: query `task: search result | query: {q}`, document
`title: {title or "none"} | text: {content}`. Matryoshka truncation (truncate) re-normalises.
Vectors are stored as little-endian float16 blobs (struct 'e'; array('e') needs Python 3.13).
"""
import http.client
import json
import math
import os
import struct
import time
import urllib.parse

DEFAULT_URL = "http://127.0.0.1:8091"
MODEL_FILE = "embeddinggemma-2-UD-Q4_K_XL.gguf"
DIM = 768
COARSE_DIM = 128
QUERY_TIMEOUT = 0.5      # total budget for one query embedding (connect + response)
CONNECT_TIMEOUT = 0.5
DOC_TIMEOUT = 120.0      # documents: a batch of 2 KB chunks takes ~150 ms each on CPU
QUERY_PREFIX = "task: search result | query: "


def base_url():
    return (os.environ.get("AI_EMBED_URL", "").strip() or DEFAULT_URL).rstrip("/")


def model_path():
    p = os.environ.get("AI_EMBED_MODEL", "").strip()
    if p:
        return os.path.expanduser(p)
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".local", "share", "ai", "models", "embedding", MODEL_FILE)


def model_id():
    """Name stored in vectors.model (a model change re-embeds everything)."""
    name = os.path.basename(model_path())
    return name[:-5] if name.endswith(".gguf") else name


def format_query(q):
    return QUERY_PREFIX + str(q)


def format_document(text, title=None):
    return "title: %s | text: %s" % (str(title).strip() if title and str(title).strip() else "none", text)


def l2(v):
    n = math.sqrt(math.fsum(x * x for x in v))
    return [x / n for x in v] if n > 0 else list(v)


def truncate(v, dim):
    """Matryoshka truncation to the first `dim` components, re-normalised."""
    return l2(v[:dim])


def to_blob(v):
    return struct.pack("<%de" % len(v), *v)


def from_blob(b):
    return struct.unpack("<%de" % (len(b) // 2), b) if b else ()


def _request(method, path, body=None, timeout=QUERY_TIMEOUT):
    """One HTTP exchange with an overall deadline. Returns (status, bytes); raises on failure."""
    u = urllib.parse.urlsplit(base_url())
    deadline = time.monotonic() + timeout
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.hostname or "127.0.0.1", u.port, timeout=min(CONNECT_TIMEOUT, timeout))
    try:
        conn.connect()
        headers = {"Content-Type": "application/json"} if body is not None else {}
        # the socket timeout bounds every recv: set it to what is left of the budget (http.client
        # may close conn.sock once the response owns the stream, so it is set before reading)
        conn.sock.settimeout(max(0.01, deadline - time.monotonic()))
        conn.request(method, (u.path.rstrip("/") + path) or path, body=body, headers=headers)
        conn.sock.settimeout(max(0.01, deadline - time.monotonic()))
        resp = conn.getresponse()
        data = resp.read()
        if time.monotonic() > deadline + 0.05:
            raise TimeoutError("embedder deadline exceeded")
        return resp.status, data
    finally:
        conn.close()


def health(timeout=QUERY_TIMEOUT):
    """True when the embedding server answers /health with status ok."""
    try:
        status, body = _request("GET", "/health", timeout=timeout)
        return status == 200 and json.loads(body or b"{}").get("status") == "ok"
    except Exception:
        return False


def embed(texts, kind="query", titles=None, timeout=None):
    """Embed `texts` (kind 'query' or 'document'; optional per-text `titles` for documents).

    Returns one L2-normalised vector per text (the server's full dim, 768 for EmbeddingGemma-2),
    [] for no texts, or None when the embedder is unreachable, slow or returns garbage."""
    texts = [str(t) for t in (texts or [])]
    if not texts:
        return []
    if kind == "query":
        inputs = [format_query(t) for t in texts]
    else:
        titles = list(titles or [])
        inputs = [format_document(t, titles[i] if i < len(titles) else None) for i, t in enumerate(texts)]
    if timeout is None:
        timeout = QUERY_TIMEOUT if kind == "query" else DOC_TIMEOUT
    try:
        status, body = _request("POST", "/v1/embeddings",
                                json.dumps({"input": inputs, "model": model_id()}).encode(), timeout)
        if status != 200:
            return None
        data = sorted(json.loads(body).get("data") or [], key=lambda d: d.get("index", 0))
        vecs = [l2([float(x) for x in d["embedding"]]) for d in data]
        if len(vecs) != len(inputs) or any(len(v) < COARSE_DIM for v in vecs):
            return None
        return vecs
    except Exception:
        return None
