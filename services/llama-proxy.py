#!/usr/bin/env python3
"""
llama-proxy.py — on-demand trigger proxy for llama-server.

Listens on 127.0.0.1:8080 (LLAMA_PROXY_HOST / LLAMA_PROXY_PORT).
- Non-API requests (/, /favicon.ico, robots.txt, port scans) are answered without waking the GPU.
- /health reports the proxy and the backend state without starting the backend:
    {"status": "ok", "proxy": "ok", "backend": "down|starting|ready|failed", "reason": ...}
- On /v1/*, /completion, /infill, /props, /slots, ...:
    if llama-server is not answering on the backend port (127.0.0.1:8081), runs
    `systemctl --user start llama-server.service` and waits for its /health, then forwards the
    request and streams the response back as it arrives (SSE included).
- Fail fast: when the unit fails (e.g. the ai-backend VRAM preflight aborts) or the backend is not
  healthy within LLAMA_PROXY_START_TIMEOUT seconds (default 180), the request gets HTTP 503 with a
  JSON error {"error": {"type": "backend_unavailable", "message": <reason>, "log": [...]}} built
  from `journalctl --user -u llama-server` of this start attempt — never an endless wait. A
  failure is remembered for LLAMA_PROXY_FAIL_CACHE seconds (default 10): requests in that window
  (a client's /props + /v1/models + chat sequence) get the same 503 without another start attempt.
- A client that disconnects mid-response closes the upstream connection, so llama-server cancels
  the generation instead of running it to the end.

Idle shutdown is not done here: `ai-backend serve` (the unit's ExecStart) stops the server after
LLAMA_IDLE_TIMEOUT seconds without activity.

Environment (all optional): LLAMA_PROXY_HOST, LLAMA_PROXY_PORT, LLAMA_PROXY_BACKEND_HOST,
LLAMA_PROXY_BACKEND_PORT, LLAMA_PROXY_SERVICE (unit name), LLAMA_PROXY_START_TIMEOUT,
LLAMA_PROXY_UPSTREAM_TIMEOUT (seconds without upstream data before giving up, default 300),
LLAMA_PROXY_FAIL_CACHE.
"""

import json
import logging
import os
import socket
import subprocess
import threading
import time
import http.client
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit


def _env_num(name, default, cast=float):
    try:
        return cast(os.environ.get(name, "") or default)
    except ValueError:
        return cast(default)


LISTEN_HOST = os.environ.get("LLAMA_PROXY_HOST") or "127.0.0.1"
LISTEN_PORT = _env_num("LLAMA_PROXY_PORT", 8080, int)
BACKEND_HOST = os.environ.get("LLAMA_PROXY_BACKEND_HOST") or "127.0.0.1"
BACKEND_PORT = _env_num("LLAMA_PROXY_BACKEND_PORT", 8081, int)
SYSTEMD_SERVICE = os.environ.get("LLAMA_PROXY_SERVICE") or "llama-server.service"
STARTUP_TIMEOUT = _env_num("LLAMA_PROXY_START_TIMEOUT", 180)   # model load into VRAM: 15-60 s typical
UPSTREAM_TIMEOUT = _env_num("LLAMA_PROXY_UPSTREAM_TIMEOUT", 300)
FAIL_CACHE_SECONDS = _env_num("LLAMA_PROXY_FAIL_CACHE", 10)
JOURNAL_LINES = 30
FIX_HINT = "Check `ai-backend status` and `journalctl --user -u %s -n 50`." % SYSTEMD_SERVICE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [llama-proxy] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("llama-proxy")

start_lock = threading.Lock()
# What the proxy knows about the backend: "starting" while a start attempt waits for it, "ready"
# after one succeeded, "failed" (+ reason, journal lines, time) after one failed.
_state_lock = threading.Lock()
_state = {"phase": None, "reason": "", "log": [], "at": 0.0}

# Endpoints that represent actual inference / AI tool calls
VALID_API_PREFIXES = (
    "/v1/",
    "/v1",
    "/completion",
    "/completions",
    "/infill",
    "/embedding",
    "/embeddings",
    "/tokenize",
    "/detokenize",
    "/props",
    "/slots",
    "/lora-adapters",
)


def _set_state(phase, reason="", log=None):
    with _state_lock:
        _state.update(phase=phase, reason=reason, log=list(log or []), at=time.time())


def _get_state():
    with _state_lock:
        return dict(_state)


def is_backend_ready() -> bool:
    try:
        conn = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=1.5)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        if resp.status == 200:
            return True
        return b"ok" in data.lower() or b"slots_idle" in data.lower()
    except Exception:
        return False


def _systemctl(*args, timeout=10):
    try:
        return subprocess.run(["systemctl", "--user"] + list(args), capture_output=True, text=True,
                              timeout=timeout)
    except Exception as e:  # systemctl missing / hung
        return subprocess.CompletedProcess(args, 1, "", str(e))


def unit_state() -> str:
    """systemd ActiveState of the backend unit: active, activating, failed, inactive, ..."""
    out = (_systemctl("is-active", SYSTEMD_SERVICE, timeout=5).stdout or "").strip()
    return out.splitlines()[-1] if out else "unknown"


def journal_lines(since=None, n=JOURNAL_LINES):
    cmd = ["journalctl", "--user", "-u", SYSTEMD_SERVICE, "-n", str(n), "--no-pager", "-o", "cat"]
    if since:
        cmd[1:1] = ["--since", "@%d" % int(since)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    return [line.rstrip() for line in out.splitlines() if line.strip()]


def failure_reason(lines, fallback):
    """One-line cause from the unit's journal: the ai-backend error block (VRAM preflight etc.)
    when there is one, else the last few lines, else `fallback`."""
    def clean(s):
        return s.replace("[llama-wrapper]", "").strip()
    start = None
    for i, line in enumerate(lines):
        low = line.lower()
        if "[llama-wrapper]" in line and "error" in low or "error:" in low:
            start = i
    if start is not None:
        block = [clean(x) for x in lines[start:start + 12]
                 if "Main process exited" not in x and "Failed with result" not in x]
        return " | ".join(x for x in block if x)
    tail = [clean(x) for x in lines[-5:]]
    return " | ".join(x for x in tail if x) or fallback


def _fail(reason, lines):
    msg = "llama-server is unavailable: %s. %s" % (reason.rstrip(". "), FIX_HINT)
    logger.error(msg)
    _set_state("failed", msg, lines)
    return msg


def ensure_backend_running():
    """-> None when the backend answers, else the failure message (the request gets a 503)."""
    if is_backend_ready():
        return None

    with start_lock:
        if is_backend_ready():
            return None
        st = _get_state()
        if st["phase"] == "failed" and time.time() - st["at"] < FAIL_CACHE_SECONDS:
            return st["reason"]

        logger.info(f"API inference request received. Starting {SYSTEMD_SERVICE}...")
        _set_state("starting")
        t_start = time.time()
        r = _systemctl("start", SYSTEMD_SERVICE, timeout=60)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "exit status %d" % r.returncode).strip()
            return _fail("`systemctl --user start %s` failed: %s" % (SYSTEMD_SERVICE, err),
                         journal_lines(since=t_start - 1))

        # Wait for the backend to be ready; give up early when the unit stops (preflight abort,
        # crash during load) instead of waiting out the whole timeout.
        t0 = time.time()
        stopped_checks = 0
        next_unit_check = t0 + 1.0
        while time.time() - t0 < STARTUP_TIMEOUT:
            if is_backend_ready():
                logger.info(f"llama-server is online and ready after {time.time() - t0:.1f}s.")
                _set_state("ready")
                return None
            if time.time() >= next_unit_check:
                next_unit_check = time.time() + 1.0
                state = unit_state()
                # "inactive" right after a start can be transient; "failed" is final
                stopped_checks = stopped_checks + 1 if state in ("failed", "inactive") else 0
                if state == "failed" or stopped_checks >= 3:
                    lines = journal_lines(since=t_start - 1)
                    return _fail(failure_reason(lines, "the unit is %s" % state), lines)
            time.sleep(0.5)

        lines = journal_lines(since=t_start - 1)
        reason = "not healthy within %gs of starting %s (LLAMA_PROXY_START_TIMEOUT)" % (
            STARTUP_TIMEOUT, SYSTEMD_SERVICE)
        if lines:
            reason += "; last log: " + " | ".join(x.replace("[llama-wrapper]", "").strip() for x in lines[-3:])
        return _fail(reason, lines)


def backend_health():
    """Backend state for /health; never starts anything."""
    if is_backend_ready():
        return {"backend": "ready"}
    st = _get_state()
    if st["phase"] == "starting":
        return {"backend": "starting"}
    unit = unit_state()
    if unit in ("activating", "active", "reloading"):
        return {"backend": "starting", "unit": unit}
    if st["phase"] == "failed":
        return {"backend": "failed", "reason": st["reason"]}
    if unit == "failed":
        return {"backend": "failed", "reason": failure_reason(journal_lines(), "the unit is failed")}
    return {"backend": "down"}


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def _send_json(self, status, obj, send_body=True, cors=False):
        body = (json.dumps(obj) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.handle_proxy(send_body=False)

    def do_GET(self):
        self.handle_proxy(send_body=True)

    def do_POST(self):
        self.handle_proxy(send_body=True)

    def do_PUT(self):
        self.handle_proxy(send_body=True)

    def do_DELETE(self):
        self.handle_proxy(send_body=True)

    def handle_proxy(self, send_body=True):
        self.close_connection = True
        path = urlsplit(self.path).path

        # 1. Non-API / root / probe requests -> answered without waking the GPU
        #    (`ai-backend` recognises the proxy by "llama-proxy-ready" here)
        if path in ("/", "/index.html", "/favicon.ico", "/robots.txt"):
            backend_up = is_backend_ready()
            self._send_json(200, {"status": "llama-proxy-ready", "backend": "running" if backend_up else "standby",
                                  "info": "On-demand inference active. Requests to /v1 will boot model into GPU."},
                            send_body, cors=True)
            return

        # 2. Health: the proxy's own state plus the backend's, never starting it
        if path == "/health":
            self._send_json(200, dict({"status": "ok", "proxy": "ok"}, **backend_health()), send_body)
            return

        is_api_request = any(path == p or path.startswith(p if p.endswith("/") else p + "/") for p in VALID_API_PREFIXES)
        if not is_api_request:
            self._send_json(404, {"error": "Not Found - llama proxy only forwards /v1 inference endpoints"},
                            send_body)
            return

        # 3. Valid API request -> ensure the backend is up, or fail fast with the reason
        failure = ensure_backend_running()
        if failure:
            log = _get_state().get("log", [])[-JOURNAL_LINES:]
            self._send_json(503, {"error": {"message": failure, "type": "backend_unavailable", "code": 503,
                                            "log": log}}, send_body)
            return

        # 4. Forward request to backend
        self.forward(send_body)

    def forward(self, send_body):
        conn = None
        started = False   # response headers already sent to the client
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            req_body = self.rfile.read(content_length) if content_length > 0 else None

            conn = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=UPSTREAM_TIMEOUT)
            headers = {}
            for k, v in self.headers.items():
                if k.lower() not in ("host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"):
                    headers[k] = v
            headers["Host"] = f"{BACKEND_HOST}:{BACKEND_PORT}"

            conn.request(self.command, self.path, body=req_body, headers=headers)
            resp = conn.getresponse()

            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            started = True

            if send_body:
                # read1: whatever has arrived (one SSE event at a time), never waiting for a full buffer
                read = resp.read1 if hasattr(resp, "read1") else resp.read
                while True:
                    chunk = read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as e:
            if started:
                # the client went away mid-response: closing upstream (finally) makes llama-server
                # cancel the generation
                logger.info(f"Client disconnected mid-response ({e.__class__.__name__}); closing upstream.")
            else:
                self._error_502(e)
        except Exception as e:
            logger.error(f"Error forwarding request to backend: {e}")
            if not started:
                self._error_502(e)
        finally:
            if conn is not None:
                try:
                    if conn.sock is not None:
                        conn.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()

    def _error_502(self, e):
        try:
            self._send_json(502, {"error": {"message": str(e) or e.__class__.__name__, "type": "proxy_error"}})
        except Exception:
            pass


class ThreadedHTTPServer(HTTPServer):
    daemon_threads = True

    def process_request(self, request, client_address):
        t = threading.Thread(target=self.process_request_thread, args=(request, client_address))
        t.daemon = True
        t.start()

    def process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def main():
    server_address = (LISTEN_HOST, LISTEN_PORT)
    httpd = ThreadedHTTPServer(server_address, ProxyHandler)
    logger.info(f"llama-proxy listening on {LISTEN_HOST}:{LISTEN_PORT} -> backend {BACKEND_HOST}:{BACKEND_PORT} "
                f"(start timeout {STARTUP_TIMEOUT:g}s)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("llama-proxy stopping...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
