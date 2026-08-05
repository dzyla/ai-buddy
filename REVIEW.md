# Code Review — `dashboard.py`

**Reviewer:** AI Buddy  
**Date:** 2026-08-04  
**File:** `/home/dzyla/Code/ai-buddy/dashboard.py` (138 lines)

---

## 1. Correctness & Safety

### 1.1 — Shell injection via `subprocess.run` with list args (lines 77–80)

```python
subprocess.run(
    ["curl", "-s", "--max-time", "8", f"https://wttr.in/{city}?format=j1"],
    ...
)
```

The command is passed as a list (good — avoids shell injection), but the `city` value is interpolated directly into the URL string. If `city` contains characters that produce a valid but surprising URL fragment (e.g., `..%2f`, spaces, Unicode), curl will simply fetch the weather for that slug. This isn't a security vulnerability per se, but it **silently returns wrong data** — e.g. `city="London%20UK"` will fetch weather for `London%2520UK`, not "London, UK".

**Suggestion:** URL-encode the city before passing it:
```python
from urllib.parse import quote
url = f"https://wttr.in/{quote(city, safe='')}?format=j1"
```

### 1.2 — Race condition on `HTML_TEMPLATE` (lines 21–120)

`HTML_TEMPLATE` is defined as a module-level string that is encoded inline at every request:

```python
def _send_html(self):
    body = HTML_TEMPLATE.encode()   # line 100
```

This works correctly but allocates a fresh bytes object per request. Not a correctness bug, but wasteful.

### 1.3 — `json.loads` on untrusted API response (line 81)

```python
data = json.loads(result.stdout)
```

If `wttr.in` changes its response format, returns malformed JSON, or is spoofed (MITM on a network), this will raise `json.JSONDecodeError` or `KeyError` — both are caught by the broad `except Exception` on line 125. The 500 response leaks the raw error message to the client.

**Suggestion:** Catch specific exceptions and return a stable error message.

---

## 2. Error Handling

### 2.1 — Overly broad `except Exception` (lines 124–126)

```python
try:
    self._send_json(fetch_weather(city))
except Exception as exc:
    self._send_json({"error": str(exc)}, 500)
```

This swallows everything: `KeyError`, `IndexError`, `json.JSONDecodeError`, `RuntimeError`, `ConnectionError`, `TimeoutError`, etc. It also **leaks internal implementation details** (e.g. `"curl failed: curl: (28) …"`) to the end user.

**Suggestion:**
```python
try:
    self._send_json(fetch_weather(city))
except (json.JSONDecodeError, KeyError, IndexError) as exc:
    self._send_json({"error": "Invalid weather response"}, 502)
except Exception as exc:
    logger.error("Weather fetch failed: %s", exc)
    self._send_json({"error": "Unable to fetch weather data"}, 503)
```

### 2.2 — Silent failure on empty city (lines 118–120)

```python
city = params.get("city", [None])[0]
if not city:
    self._send_json({"error": "Missing 'city' query parameter"}, 400)
```

`not city` catches empty string `""` but also catches `None`. This is fine, but a request with `?city=` would pass `""` which is treated as missing — correct behavior, just worth noting.

### 2.3 — No timeout validation on city length

A request with `?city=` + 1000 characters will be sent to `wttr.in`, wasting bandwidth and time. The `--max-time 8` on curl helps, but a length limit would be cheaper.

**Suggestion:** Reject city names longer than, say, 100 characters early with a 400 response.

---

## 3. Security Concerns

### 3.1 — Information leakage in error responses (line 125)

```python
self._send_json({"error": str(exc)}, 500)
```

Error messages like `curl failed: curl: (6) Could not resolve host: ...` or `KeyError: 'current_condition'` reveal internals (DNS resolution, JSON structure) to any client. An attacker can use these to fingerprint the service.

**Fix:** Map exceptions to generic user-facing messages; log full details server-side.

### 3.2 — No input sanitization for XSS (lines 129–133)

The HTML template renders `d.city`, `d.temperature`, `d.conditions`, `d.humidity` directly into the DOM via `innerHTML`:

```javascript
res.innerHTML = `<strong>${d.city}</strong><br>` + …
```

`d.city` comes from the server's `city.title()` call, which is safe for ASCII. However, if `wttr.in` ever returned user-controlled content in `weatherDesc`, this would be a reflected XSS. Currently mitigated because wttr.in controls those fields, but the pattern is fragile.

**Suggestion:** Use `textContent` instead of `innerHTML`, or sanitize with a library like `bleach` on the server side before responding with JSON.

### 3.3 — No rate limiting or request throttling

Any client can hammer `/api/weather` at will, each request spawning a `curl` subprocess and making an external HTTP call. Under load this will:

- Exhaust file descriptors (subprocess + network sockets per request).
- Abuse wttr.in's rate limits (they explicitly ask for caching).
- Amplify any upstream outage.

**Suggestion:** Add a simple in-memory rate limiter (e.g. `collections.defaultdict(list)` with timestamps) or use `ThreadingHTTPServer` with a semaphore.

### 3.4 — `0.0.0.0` bind (line 141)

```python
server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
```

Binds to all network interfaces. If this machine has a public IP or is on a shared network, the dashboard is exposed. For development this is fine; for anything else, restrict to `127.0.0.1` or document clearly.

### 3.5 — No TLS

The entire dashboard (including the JS that calls `/api/weather`) is served over plaintext HTTP. On any non-localhost network, credentials or session data could be intercepted. Not a bug in a dev tool, but worth flagging.

---

## 4. Performance

### 4.1 — `subprocess.run` for every request (lines 77–80)

Spawning a new `curl` process per request is expensive: ~10–50ms just for process creation, plus the 8-second HTTP timeout. Under 100 concurrent requests, you'd have 100 curl processes competing for resources.

**Suggestion:** Use `urllib.request` or `httpx`/`requests` directly in Python — eliminates the subprocess overhead and gives you proper timeout handling:
```python
import urllib.request
req = urllib.request.Request(f"https://wttr.in/{quote(city)}?format=j1")
req.add_header("User-Agent", "WeatherDashboard/1.0")
with urllib.request.urlopen(req, timeout=8) as resp:
    data = json.loads(resp.read())
```

### 4.2 — No response caching (line 78)

wttr.in's docs explicitly recommend caching responses for at least 10 minutes. Every request hits their servers. Add an in-memory TTL cache:

```python
from functools import lru_cache
import time

_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 600  # seconds

def fetch_weather(city: str) -> dict:
    now = time.time()
    if city in _cache and now - _cache[city][0] < CACHE_TTL:
        return _cache[city][1]
    …
    _cache[city] = (now, result)
    return result
```

### 4.3 — `HTML_TEMPLATE` re-encoding per request (line 100)

As noted in §1.2, `.encode()` is called on every request. Pre-compute once:

```python
_HTML_BYTES = HTML_TEMPLATE.encode()

def _send_html(self):
    self.wfile.write(_HTML_BYTES)  # no Content-Length needed if using chunked
```

### 4.4 — No `ThreadingHTTPServer` or async I/O

`HTTPServer` handles one request at a time. A single slow `/api/weather` call (up to 8s) blocks all other requests. Use `ThreadingHTTPServer` (Python 3.7+) or `asyncio`-based `aiohttp` for concurrent handling.

---

## 5. Maintainability

### 5.1 — Single file, single responsibility violated (entire file)

The file contains: HTML template, CSS, JavaScript, HTTP handler, API client, subprocess invocation, and server bootstrapping — all in one 138-line file. While fine for a prototype, this violates separation of concerns and makes testing difficult.

**Suggestion:** Extract the HTML template to a separate `.html` file. Move the API client into a `weather.py` module. Keep `dashboard.py` as the server entry point only.

### 5.2 — Hard-coded city title-casing (line 86)

```python
"city": city.title(),
```

`.title()` mis-capitalizes many names (e.g., `"san francisco"` → `"San Francisco"` ✓, but `"düsseldorf"` → `"Düsseldorf"` ✓, `"mcdonald"` → `"Mcdonald"` ✗). More importantly, the user's input is silently altered — if they typed `"NEW YORK"`, the response says `"New York"` without telling them.

**Suggestion:** Use the raw input, or have wttr.in canonicalize it (it does in its response).

### 5.3 — `log_message` override is non-standard (lines 132–133)

```python
def log_message(self, fmt, *args):
    print(f"[dashboard] {args[0]}", flush=True)
```

This discards the format string and only prints the first argument. Python's `BaseHTTPRequestHandler.log_message` receives `(format, *args)` where `format` is the log template (e.g. `'"%s" %s %s'`). The override is fragile if the base class ever changes its format string.

**Suggestion:** Just use `logging` module:
```python
import logging
logger = logging.getLogger("dashboard")

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info(format, *args)
```

### 5.4 — No `requirements.txt` or dependency pinning

The script uses only stdlib (`http.server`, `subprocess`, `json`, `urllib.parse`). That's good — but if you switch to `httpx` for the performance fix in §4.1, you'll need to document that dependency.

### 5.5 — Magic numbers (lines 8, 78, 80)

```python
PORT = int(os.environ.get("PORT", 8080))
…
"--max-time", "8", …, timeout=12,
```

These should be named constants at module level (`CURL_TIMEOUT = 8`, `SUBPROCESS_TIMEOUT = 12`, `DEFAULT_PORT = 8080`) for clarity and ease of tuning.

---

## Overall Rating: **5 / 10**

This is a functional prototype that demonstrates the core flow (HTML → JS → API → subprocess → response). It works for personal use. However, it has several issues that would make it problematic in anything beyond a localhost dev environment: unbounded error leakage, no caching, subprocess-per-request overhead, no rate limiting, and a single file doing too much.

---

## Top 3 Priorities for Improvement

| Priority | Issue | Impact | Effort |
|----------|-------|--------|--------|
| **1** | **Cache weather responses** (§4.2) | Eliminates redundant external API calls, reduces latency from ~8s to ~0ms for repeated cities, respects wttr.in's caching policy | Low (~10 lines) |
| **2** | **Replace `subprocess.run` + `curl` with Python HTTP client** (§4.1) | Removes process-spawn overhead, enables proper connection pooling and timeouts, simplifies error handling | Low (~15 lines) |
| **3** | **Tighten error handling & stop leaking internals** (§2.1, §3.1) | Prevents information disclosure, gives stable error responses, makes debugging easier via server-side logging | Low (~10 lines) |

After these three, address rate limiting (§3.3) and split the file into modules (§5.1) for production readiness.
