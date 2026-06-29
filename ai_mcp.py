#!/usr/bin/env python3
import sys
import os
import json
import subprocess
import urllib.request
import urllib.parse
import re

try:
    import trafilatura
    _HAS_TRAFILATURA = True
except ImportError:
    _HAS_TRAFILATURA = False

CONFIG_PATHS = [
    os.path.join(os.getcwd(), "mcp.json"),
    os.path.join(os.getcwd(), "mcp_config.json"),
    os.path.expanduser("~/.config/ai/mcp.json"),
    os.path.expanduser("~/.config/ai/mcp_config.json"),
    os.path.expanduser("~/.gemini/config/mcp_config.json"),
    os.path.expanduser("~/.lmstudio/mcp.json"),
]

def load_config():
    for path in CONFIG_PATHS:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    if "mcpServers" in data:
                        return data["mcpServers"]
                    return data
            except Exception as e:
                print(f"Warning: failed to load config from {path}: {e}", file=sys.stderr)
    return {}

def run_jsonrpc(proc, method, params, req_id):
    req = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params
    }
    req_str = json.dumps(req) + "\n"
    proc.stdin.write(req_str)
    proc.stdin.flush()

    while True:
        line = proc.stdout.readline()
        if not line:
            raise Exception("Connection closed by server")
        try:
            resp = json.loads(line)
            if resp.get("id") == req_id:
                return resp
        except json.JSONDecodeError:
            pass

def send_notification(proc, method, params=None):
    req = {
        "jsonrpc": "2.0",
        "method": method
    }
    if params is not None:
        req["params"] = params
    req_str = json.dumps(req) + "\n"
    proc.stdin.write(req_str)
    proc.stdin.flush()

def start_server(cfg):
    cmd = []
    if "command" in cfg:
        cmd.append(cfg["command"])
    if "args" in cfg:
        cmd.extend(os.path.expandvars(os.path.expanduser(a)) for a in cfg["args"])
    
    if not cmd:
        return None

    env = os.environ.copy()
    if "env" in cfg:
        for k, v in cfg["env"].items():
            env[k] = str(v)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        bufsize=1
    )
    return proc

def init_server(proc):
    init_params = {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "ai", "version": "1.0"}
    }
    resp = run_jsonrpc(proc, "initialize", init_params, req_id=1)
    send_notification(proc, "notifications/initialized")
    return resp

def list_tools(server_name, cfg):
    proc = start_server(cfg)
    if not proc:
        return []
    try:
        init_server(proc)
        resp = run_jsonrpc(proc, "tools/list", {}, req_id=2)
        tools = resp.get("result", {}).get("tools", [])
        namespaced_tools = []
        for t in tools:
            clean_server = "".join(c if c.isalnum() or c == "_" else "_" for c in server_name)
            t["name"] = f"{clean_server}__{t['name']}"
            namespaced_tools.append(t)
        return namespaced_tools
    except Exception as e:
        print(f"Error listing tools from {server_name}: {e}", file=sys.stderr)
        return []
    finally:
        try:
            proc.terminate()
        except:
            pass

def call_tool(server_name, cfg, tool_name, arguments):
    proc = start_server(cfg)
    if not proc:
        return {"error": "Failed to start server"}
    try:
        init_server(proc)
        resp = run_jsonrpc(proc, "tools/call", {"name": tool_name, "arguments": arguments}, req_id=3)
        return resp.get("result", {})
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            proc.terminate()
        except:
            pass

def ddg_lite_search(query):
    try:
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')

        trs = re.findall(r'<tr.*?>(.*?)</tr>', html, re.DOTALL)
        results = []
        top_url = None

        i = 0
        while i < len(trs):
            tr = trs[i]
            link_match = re.search(r"(<a[^>]+class='result-link'[^>]*>)(.*?)</a>", tr, re.DOTALL)
            if link_match:
                tag = link_match.group(1)
                href_match = re.search(r'href="([^"]+)"', tag)
                link = href_match.group(1) if href_match else ""
                title = re.sub(r'<[^>]+>', '', link_match.group(2)).strip()

                snippet = ""
                if i + 1 < len(trs):
                    sm = re.search(r"<td[^>]+class='result-snippet'[^>]*>(.*?)</td>",
                                   trs[i + 1], re.DOTALL)
                    if sm:
                        snippet = re.sub(r'<[^>]+>', '', sm.group(1)).strip()
                        snippet = (snippet.replace('&amp;', '&').replace('&quot;', '"')
                                         .replace('&lt;', '<').replace('&gt;', '>')
                                         .replace('&nbsp;', ' '))

                if not top_url and link.startswith('http'):
                    top_url = link
                results.append(f"Title: {title}\nURL: {link}\nSnippet: {snippet}\n")
                if len(results) >= 5:
                    break
                i += 2
                continue
            i += 1

        if not results:
            return "No results found."

        output = "\n".join(results)

        # Auto-fetch the top result to provide full content (snippets are always truncated)
        if top_url:
            try:
                full = fetch_webpage(top_url)
                # Keep first 4000 chars of article body to stay within context budget
                body = full.split('\n\n', 1)[-1] if '\n\n' in full else full
                if len(body.split()) > 40:
                    body_trimmed = body[:4000] + ("\n... [more at URL]" if len(body) > 4000 else "")
                    output += f"\n---\n[Top result full content — {top_url}]\n{body_trimmed}"
            except Exception:
                pass

        return output
    except Exception as e:
        return f"Error during web search: {e}"

def _html_to_text_fallback(html, url):
    """Regex-based HTML→text extraction used when trafilatura is unavailable."""
    orig_html = html
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<p[^>]*>', '\n\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<br[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<h[1-6][^>]*>', '\n\n## ', html, flags=re.IGNORECASE)
    html = re.sub(r'</h[1-6]>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', html)
    text = (text.replace('&nbsp;', ' ').replace('&quot;', '"').replace('&amp;', '&')
                .replace('&lt;', '<').replace('&gt;', '>').replace('&#x27;', "'")
                .replace('&#39;', "'").replace('&ndash;', '-').replace('&mdash;', '-'))
    lines = [l.strip() for l in text.splitlines()]
    text = "\n".join(l for l in lines if l)
    word_count = len(text.split())
    js_indicators = ['enable javascript', 'javascript is required', 'javascript is disabled',
                     'you need to enable javascript', 'requires javascript']
    is_js_only = (any(ind in text.lower() for ind in js_indicators)
                  or (word_count < 40 and '<noscript>' in orig_html.lower()))
    if is_js_only:
        return (f"[WARNING: This page requires JavaScript and returned no useful content "
                f"({word_count} words). Use execute_command with curl to a plain-text API. "
                f"For weather: curl -s 'wttr.in/CITY?format=3']\n\n{text[:2000]}")
    max_tool = 65536
    max_tool_output_env = os.environ.get("INFER_MAX_TOOL_OUTPUT")
    if max_tool_output_env:
        try:
            max_tool = int(max_tool_output_env)
        except ValueError:
            pass
    web_limit = max(10000, int(max_tool * 0.8))
    if len(text) > web_limit:
        text = text[:web_limit] + f"\n... [truncated. Page content size was {len(text)} characters. Limit is {web_limit}.]"
    return text


def fetch_webpage(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
        if _HAS_TRAFILATURA:
            # trafilatura fetches + extracts main article body, strips nav/ads/footers
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                text = trafilatura.extract(
                    downloaded,
                    include_comments=False,
                    include_tables=True,
                    deduplicate=True,
                    no_fallback=False,
                )
                if text and len(text.split()) > 30:
                    max_tool = 65536
                    max_tool_output_env = os.environ.get("INFER_MAX_TOOL_OUTPUT")
                    if max_tool_output_env:
                        try:
                            max_tool = int(max_tool_output_env)
                        except ValueError:
                            pass
                    web_limit = max(12000, int(max_tool * 0.8))
                    if len(text) > web_limit:
                        text = text[:web_limit] + f"\n... [truncated. Page content size was {len(text)} characters. Limit is {web_limit}.]"
                    return f"[Source: {url}]\n\n{text}"
            # trafilatura returned nothing → fall through to regex

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')

        text = _html_to_text_fallback(html, url)
        return f"[Source: {url}]\n\n{text}"
    except Exception as e:
        return f"Error fetching webpage: {e}"

def fetch_webpage_js(url, wait_for="networkidle", timeout_ms=30000):
    """Fetch a JS-rendered page via Playwright and return its content as markdown."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "[Error: playwright not installed. Run: pip install playwright && playwright install chromium]"

    try:
        from markdownify import markdownify as md
        _HAS_MARKDOWNIFY = True
    except ImportError:
        _HAS_MARKDOWNIFY = False

    try:
        try:
            from playwright_stealth import Stealth as _Stealth
            _HAS_STEALTH = True
        except ImportError:
            _HAS_STEALTH = False

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                    timezone_id="America/New_York",
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                )
                page = context.new_page()
                if _HAS_STEALTH:
                    _Stealth().apply_stealth_sync(page)
                page.goto(url, timeout=timeout_ms)
                try:
                    page.wait_for_load_state(wait_for, timeout=timeout_ms)
                except Exception:
                    pass  # timeout on networkidle is fine — grab what we have
                # Scroll to trigger lazy-loaded content
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(800)
                except Exception:
                    pass
                html = page.content()
            finally:
                browser.close()

        # Strip script/style/noscript blocks before conversion
        clean_html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        clean_html = re.sub(r'<style[^>]*>.*?</style>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
        clean_html = re.sub(r'<noscript[^>]*>.*?</noscript>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)

        # Try trafilatura first — best main-content extraction from raw HTML
        if _HAS_TRAFILATURA:
            text = trafilatura.extract(
                clean_html,
                include_comments=False,
                include_tables=True,
                deduplicate=True,
                no_fallback=False,
            )
            if text and len(text.split()) > 30:
                text = text.strip()
            else:
                text = None
        else:
            text = None

        if not text:
            if _HAS_MARKDOWNIFY:
                text = md(clean_html, heading_style="ATX",
                          strip=["head", "nav", "footer", "aside"])
            else:
                text = _html_to_text_fallback(clean_html, url)

        # Collapse excessive blank lines
        text = re.sub(r'\n{3,}', '\n\n', text).strip()

        max_tool = 65536
        max_tool_output_env = os.environ.get("INFER_MAX_TOOL_OUTPUT")
        if max_tool_output_env:
            try:
                max_tool = int(max_tool_output_env)
            except ValueError:
                pass
        web_limit = max(12000, int(max_tool * 0.8))
        if len(text) > web_limit:
            text = text[:web_limit] + f"\n... [truncated. Content was {len(text)} chars, limit {web_limit}.]"

        return f"[Source (JS-rendered): {url}]\n\n{text}"
    except Exception as e:
        return f"Error fetching JS page: {e}"


def _is_blocked(text, status_code=200):
    """Return True if a fetched response looks like a bot-block or empty page."""
    if status_code in (403, 429, 503):
        return True
    if not text or len(text.split()) < 10:
        return True
    lower = text.lower()
    cf_markers = [
        'cf-browser-verification', 'cf_chl_opt', 'checking your browser',
        'please wait while we verify', 'ddos-guard', 'enable javascript',
        'javascript is required',
    ]
    return any(m in lower for m in cf_markers)


def fetch_smart(url):
    """Speed-first cascade: curl_cffi TLS impersonation → Playwright+stealth → urllib."""
    # ── Step 1: curl_cffi (browser TLS fingerprint) ──────────────────────────
    try:
        from curl_cffi import requests as cffi_req
        resp = cffi_req.get(url, impersonate="chrome124", timeout=15,
                            allow_redirects=True)
        html = resp.text
        if _HAS_TRAFILATURA:
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                deduplicate=True,
                no_fallback=False,
            )
        else:
            text = None
        if not text:
            text = _html_to_text_fallback(html, url)
        if not _is_blocked(text, resp.status_code):
            max_tool = int(os.environ.get("INFER_MAX_TOOL_OUTPUT", 65536))
            web_limit = max(12000, int(max_tool * 0.8))
            if len(text) > web_limit:
                text = text[:web_limit] + f"\n... [truncated at {web_limit} chars]"
            return f"[Source (smart/curl): {url}]\n\n{text}"
        # blocked — fall through to Playwright
    except ImportError:
        # curl_cffi not installed — fall back to urllib path
        return fetch_webpage(url)
    except Exception:
        pass  # network error or parse failure — try Playwright

    # ── Step 2: Playwright + stealth ─────────────────────────────────────────
    try:
        js_result = fetch_webpage_js(url)
        body_part = js_result.split('\n\n', 1)[-1] if '\n\n' in js_result else js_result
        if not _is_blocked(body_part):
            return js_result.replace('[Source (JS-rendered):', '[Source (smart/stealth):', 1)
    except Exception:
        pass

    # ── Step 3: Final urllib fallback ────────────────────────────────────────
    result = fetch_webpage(url)
    body_part = result.split('\n\n', 1)[-1] if '\n\n' in result else result
    if _is_blocked(body_part):
        result = f"[FETCH_WARN: site resisted all fetch methods — content may be incomplete]\n\n{result}"
    return result


MEMORY_PATH = os.path.expanduser("~/.config/ai/memory.txt")

def save_memory(content):
    try:
        os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)
        if len(content) > 4000:
            content = content[-4000:]
        with open(MEMORY_PATH, "w") as f:
            f.write(content)
        return "Memory updated successfully."
    except Exception as e:
        return f"Error saving memory: {e}"

def _clean_pdf_text(raw):
    # Repair soft-hyphenation at line breaks (word-\nrest -> wordrest)
    raw = re.sub(r'-\n(?=[a-z])', '', raw)
    # Remove control characters except newline/tab
    raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)
    # Collapse runs of blank lines to at most two
    raw = re.sub(r'\n{3,}', '\n\n', raw)
    # Collapse runs of spaces/tabs on a single line
    raw = re.sub(r'[ \t]{2,}', ' ', raw)
    return raw.strip()

def extract_text_from_pdf(path):
    source_header = f"[Source: {path}]\n\n"
    pages_text = []

    # 1. pdfplumber — best layout-awareness; also extracts tables
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            meta = pdf.metadata or {}
            meta_parts = []
            if meta.get("Title"):
                meta_parts.append(f"Title: {meta['Title']}")
            if meta.get("Author"):
                meta_parts.append(f"Author: {meta['Author']}")
            if meta_parts:
                pages_text.append("[PDF Metadata] " + " | ".join(meta_parts))

            for i, page in enumerate(pdf.pages, 1):
                parts = []
                body = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                if body.strip():
                    parts.append(body)
                # Append any tables found on the page as simple CSV-ish blocks
                for table in page.extract_tables() or []:
                    rows = []
                    for row in table:
                        rows.append(" | ".join(cell.strip() if cell else "" for cell in row))
                    if rows:
                        parts.append("[Table]\n" + "\n".join(rows))
                if parts:
                    pages_text.append(f"--- Page {i} ---\n" + "\n\n".join(parts))

        if pages_text:
            return source_header + _clean_pdf_text("\n\n".join(pages_text))
    except Exception:
        pass

    # 2. pypdf
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        meta = reader.metadata or {}
        meta_parts = []
        if getattr(meta, "title", None):
            meta_parts.append(f"Title: {meta.title}")
        if getattr(meta, "author", None):
            meta_parts.append(f"Author: {meta.author}")
        if meta_parts:
            pages_text.append("[PDF Metadata] " + " | ".join(meta_parts))

        for i, page in enumerate(reader.pages, 1):
            body = page.extract_text() or ""
            if body.strip():
                pages_text.append(f"--- Page {i} ---\n{body}")

        if pages_text:
            return source_header + _clean_pdf_text("\n\n".join(pages_text))
    except Exception:
        pass

    # 3. pdftotext (poppler-utils) — reliable fallback for scanned/complex PDFs
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return source_header + _clean_pdf_text(proc.stdout)
    except Exception:
        pass

    return "Error: Could not parse PDF. Install 'pdfplumber' (pip install pdfplumber), 'pypdf' (pip install pypdf), or 'pdftotext' (apt install poppler-utils)."

def computer_control(arguments):
    action = arguments.get("action")
    x = arguments.get("x")
    y = arguments.get("y")
    text = arguments.get("text")
    window_id = arguments.get("window_id")

    # 1. screenshot
    if action == "screenshot":
        import tempfile
        tmp_dir = tempfile.gettempdir()
        scrot_path = os.path.join(tmp_dir, "ai_screenshot.png")
        cmd = f"scrot -z '{scrot_path}' 2>/dev/null || scrot '{scrot_path}' 2>/dev/null || gnome-screenshot -f '{scrot_path}' 2>/dev/null"
        ret = os.system(cmd)
        if ret == 0 and os.path.exists(scrot_path):
            return f"[IMAGE_DATA_SUCCESS:{scrot_path}] Screenshot captured successfully."
        else:
            return "Error: failed to take screenshot. Ensure 'scrot' or 'gnome-screenshot' is installed."

    # 2. click
    elif action == "click":
        if x is not None and y is not None:
            cmd = f"xdotool mousemove {x} {y} click 1"
        else:
            cmd = "xdotool click 1"
        ret = os.system(cmd)
        if ret == 0:
            return f"Clicked successfully at current mouse position or ({x}, {y})."
        return "Error: failed to perform click action. Check if 'xdotool' is installed."

    # 3. double_click
    elif action == "double_click":
        if x is not None and y is not None:
            cmd = f"xdotool mousemove {x} {y} click --repeat 2 --delay 100 1"
        else:
            cmd = "xdotool click --repeat 2 --delay 100 1"
        ret = os.system(cmd)
        if ret == 0:
            return "Double-clicked successfully."
        return "Error: failed to double-click."

    # 4. right_click
    elif action == "right_click":
        if x is not None and y is not None:
            cmd = f"xdotool mousemove {x} {y} click 3"
        else:
            cmd = "xdotool click 3"
        ret = os.system(cmd)
        if ret == 0:
            return "Right-clicked successfully."
        return "Error: failed to right-click."

    # 5. mouse_move
    elif action == "mouse_move":
        if x is None or y is None:
            return "Error: coordinates x and y are required for mouse_move."
        cmd = f"xdotool mousemove {x} {y}"
        ret = os.system(cmd)
        if ret == 0:
            return f"Moved mouse to ({x}, {y})."
        return "Error: failed to move mouse."

    # 6. mouse_drag
    elif action == "mouse_drag":
        if x is None or y is None:
            return "Error: coordinates x and y are required for mouse_drag."
        cmd = f"xdotool mousedown 1 mousemove {x} {y} mouseup 1"
        ret = os.system(cmd)
        if ret == 0:
            return f"Dragged mouse to ({x}, {y})."
        return "Error: failed to drag mouse."

    # 7. type_text
    elif action == "type_text":
        if not text:
            return "Error: text argument is required for type_text."
        escaped_text = text.replace("'", "'\\''")
        cmd = f"xdotool type '{escaped_text}'"
        ret = os.system(cmd)
        if ret == 0:
            return f"Typed text: '{text}'"
        return "Error: failed to type text."

    # 8. key_combo
    elif action == "key_combo":
        if not text:
            return "Error: text argument (e.g. 'ctrl+c', 'Escape', 'Alt+Tab') is required for key_combo."
        cmd = f"xdotool key '{text}'"
        ret = os.system(cmd)
        if ret == 0:
            return f"Pressed key combination: {text}"
        return "Error: failed to press key combo."

    # 9. minimize_all
    elif action == "minimize_all":
        import subprocess
        proc = subprocess.run(["wmctrl", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            os.system("xdotool key ctrl+super+d")
            return "Attempted minimizing all windows via keyboard shortcut."
        
        lines = proc.stdout.strip().split("\n")
        minimized_count = 0
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            win_id = parts[0]
            ret = os.system(f"wmctrl -i -b add,hidden -r {win_id} 2>/dev/null")
            if ret == 0:
                minimized_count += 1
        return f"Successfully minimized {minimized_count} windows."

    # 10. minimize_window
    elif action == "minimize_window":
        if not window_id:
            cmd = "xdotool getactivewindow windowminimize"
        else:
            cmd = f"wmctrl -i -b add,hidden -r {window_id} 2>/dev/null || wmctrl -b add,hidden -r {window_id}"
        ret = os.system(cmd)
        if ret == 0:
            return f"Minimized window '{window_id or 'active'}'."
        return f"Error: failed to minimize window '{window_id}'."

    # 11. maximize_window
    elif action == "maximize_window":
        if not window_id:
            return "Error: window_id is required to maximize a window."
        cmd = f"wmctrl -i -b add,maximized_vert,maximized_horz -r {window_id} 2>/dev/null || wmctrl -b add,maximized_vert,maximized_horz -r {window_id}"
        ret = os.system(cmd)
        if ret == 0:
            return f"Maximized window '{window_id}'."
        return f"Error: failed to maximize window '{window_id}'."

    # 12. close_window
    elif action == "close_window":
        if not window_id:
            cmd = "xdotool getactivewindow windowkill"
        else:
            cmd = f"wmctrl -i -c {window_id} 2>/dev/null || wmctrl -c {window_id}"
        ret = os.system(cmd)
        if ret == 0:
            return f"Closed window '{window_id or 'active'}'."
        return f"Error: failed to close window '{window_id}'."

    # 13. list_windows
    elif action == "list_windows":
        import subprocess
        proc = subprocess.run(["wmctrl", "-lG"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode == 0:
            return proc.stdout
        proc = subprocess.run(["wmctrl", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode == 0:
            return proc.stdout
        return "Error: wmctrl is not installed or failed to execute."

    else:
        return f"Error: Unknown action '{action}'."

def list_directory(path="."):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return f"Error: directory {path} does not exist."
        if not os.path.isdir(abs_path):
            return f"Error: path {path} is not a directory."
        
        items = os.listdir(abs_path)
        if not items:
            return f"Directory {path} is empty."
            
        lines = []
        for item in sorted(items):
            item_path = os.path.join(abs_path, item)
            is_dir = os.path.isdir(item_path)
            prefix = "[DIR] " if is_dir else "      "
            size_str = ""
            if not is_dir:
                try:
                    size = os.path.getsize(item_path)
                    if size < 1024:
                        size_str = f" ({size} B)"
                    elif size < 1024 * 1024:
                        size_str = f" ({size / 1024:.1f} KB)"
                    else:
                        size_str = f" ({size / (1024*1024):.1f} MB)"
                except:
                    pass
            lines.append(f"{prefix}{item}{size_str}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing directory: {e}"

def highlight_line(line, lang):
    if not lang:
        return f"  \033[36m{line}\033[0m"
        
    stripped = line.strip()
    if stripped.startswith('#') or stripped.startswith('//') or stripped.startswith('/*') or stripped.endswith('*/'):
        return f"  \033[90m{line}\033[0m"
        
    string_placeholder = "___STR_PLACEHOLDER_{}___"
    strings = []
    
    def repl_str(match):
        strings.append(match.group(0))
        return string_placeholder.format(len(strings) - 1)
        
    temp_line = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'', repl_str, line)

    # Numbers must run FIRST — before keyword/constant substitutions inject digits
    # inside ANSI escape codes (e.g. \033[1;33m), which would otherwise be
    # re-matched by \b(\d+)\b and corrupt the sequence.
    temp_line = re.sub(r'\b(\d+)\b', r'\033[35m\1\033[0m', temp_line)

    keywords = [
        "def", "class", "return", "if", "elif", "else", "for", "while", "break", "continue",
        "import", "from", "as", "try", "except", "finally", "raise", "assert", "with", "in",
        "is", "not", "and", "or", "lambda", "global", "nonlocal", "pass", "yield", "del",
        "int", "char", "float", "double", "void", "struct", "union", "enum", "typedef",
        "const", "static", "extern", "volatile", "inline", "switch", "case", "default",
        "do", "goto", "sizeof", "alignof", "then", "fi", "done", "esac", "local", "export",
        "function", "let", "var", "fn", "impl", "pub", "use", "mod"
    ]
    keyword_re = r'\b(' + '|'.join(keywords) + r')\b'
    temp_line = re.sub(keyword_re, r'\033[1;33m\1\033[0m', temp_line)

    constants = ["True", "False", "None", "true", "false", "null", "NULL", "self"]
    const_re = r'\b(' + '|'.join(constants) + r')\b'
    temp_line = re.sub(const_re, r'\033[35m\1\033[0m', temp_line)
    
    for idx, s in enumerate(strings):
        temp_line = temp_line.replace(string_placeholder.format(idx), f"\033[32m{s}\033[0m")
        
    return f"  {temp_line}"

def is_binary_file(filepath):
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(1024)
            if b'\x00' in chunk:
                return True
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7f})
            non_text = sum(1 for c in chunk if c not in text_chars)
            if len(chunk) > 0 and (non_text / len(chunk)) > 0.30:
                return True
        return False
    except:
        return False

def extract_code_outline(content):
    """Return a list of top-level definitions with line numbers."""
    outline = []
    patterns = re.compile(
        r'^(?:'
        r'(?:async\s+)?def\s+\w+'          # Python functions
        r'|class\s+\w+'                     # Python/JS/TS/Rust classes
        r'|(?:pub\s+)?(?:fn|impl|struct|enum|trait)\s+\w+'  # Rust
        r'|func\s+\w+'                      # Go
        r'|(?:async\s+)?function\s+\w+'    # JS/TS
        r'|(?:export\s+)?(?:const|let)\s+\w+\s*=\s*(?:async\s+)?\('  # arrow fns
        r'|(?:interface|type)\s+\w+'        # TS
        r')'
    )
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.lstrip()
        if patterns.match(stripped):
            outline.append(f"  L{i:>5}: {stripped[:90]}")
        if len(outline) >= 40:
            outline.append("  ... (outline truncated at 40 entries)")
            break
    return "\n".join(outline)

def read_file(path, start_line=None, end_line=None):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return f"Error: file {path} does not exist."

        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in ['.png', '.jpg', '.jpeg', '.webp', '.pdf'] and is_binary_file(abs_path):
            return f"Error: Cannot read binary file '{path}'. This file appears to be a compiled binary or non-text file."

        if ext in ['.png', '.jpg', '.jpeg', '.webp']:
            return f"[IMAGE_DATA_SUCCESS:{abs_path}]"

        if ext == '.pdf':
            return extract_text_from_pdf(abs_path)

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return f"Error reading text file: {e}"

        lines = content.splitlines()
        total_lines = len(lines)
        total_chars = len(content)

        # Line-range read (model requested a specific slice)
        if start_line is not None or end_line is not None:
            s = max(0, (start_line or 1) - 1)
            e = min(total_lines, end_line or total_lines)
            snippet = "\n".join(lines[s:e])
            return (f"[{abs_path} | lines {s+1}-{e} of {total_lines}]\n"
                    f"[Use read_file with start_line/end_line to read other sections]\n\n"
                    f"{snippet}")

        # Small file — return as-is
        max_tool = 65536
        max_tool_output_env = os.environ.get("INFER_MAX_TOOL_OUTPUT")
        if max_tool_output_env:
            try:
                max_tool = int(max_tool_output_env)
            except ValueError:
                pass

        small_file_limit = max(12000, int(max_tool * 0.8))
        large_file_limit = max(80000, int(max_tool * 5.0))

        if total_chars <= small_file_limit:
            return content

        # Large file (small_file_limit – large_file_limit): return smart outline + head + tail
        if total_chars <= large_file_limit:
            outline = extract_code_outline(content)
            head = "\n".join(lines[:80])
            tail = "\n".join(lines[-30:])
            omitted = total_lines - 110
            parts = [
                f"[Large file: {total_lines} lines, {total_chars} chars | {abs_path}]",
                f"[To read a specific section: read_file(path, start_line=N, end_line=M)]",
            ]
            if outline:
                parts.append(f"\n### Code outline\n{outline}")
            parts.append(f"\n### First 80 lines\n{head}")
            if omitted > 0:
                parts.append(f"\n... ({omitted} lines omitted) ...")
            parts.append(f"\n### Last 30 lines\n{tail}")
            return "\n".join(parts)

        # Very large file (> large_file_limit): delegate digest to a sub-agent
        if os.environ.get("AI_DIGESTING") == "1":
            # Fall back to the outline + head/tail approach to avoid infinite recursion
            outline = extract_code_outline(content)
            head = "\n".join(lines[:80])
            tail = "\n".join(lines[-30:])
            omitted = total_lines - 110
            parts = [
                f"[Large file: {total_lines} lines, {total_chars} chars | {abs_path}]",
                f"[To read a specific section: read_file(path, start_line=N, end_line=M)]",
            ]
            if outline:
                parts.append(f"\n### Code outline\n{outline}")
            parts.append(f"\n### First 80 lines\n{head}")
            if omitted > 0:
                parts.append(f"\n... ({omitted} lines omitted) ...")
            parts.append(f"\n### Last 30 lines\n{tail}")
            return "\n".join(parts)

        ai_bin = os.environ.get("INFER_BIN_PATH")
        if not ai_bin or not os.path.exists(ai_bin):
            ai_bin = "/usr/local/bin/ai"
        if not os.path.exists(ai_bin):
            ai_bin = "./ai"
        if not os.path.exists(ai_bin):
            # Fall back to the outline + head approach
            outline = extract_code_outline(content)
            head = "\n".join(lines[:60])
            return (f"[Very large file: {total_lines} lines | {abs_path}]\n"
                    f"[Sub-agent digest unavailable; showing outline + first 60 lines]\n\n"
                    f"### Code outline\n{outline}\n\n### First 60 lines\n{head}")

        digest_prompt = (
            f"Read and digest the file '{abs_path}'. "
            f"Summarise its purpose, structure, key functions/classes/variables, "
            f"and any important patterns or TODOs. "
            f"Keep the summary under 800 words. Output in markdown."
        )
        try:
            task_timeout = 180
            env_timeout = os.environ.get("INFER_TASK_TIMEOUT")
            if env_timeout:
                try:
                    task_timeout = max(90, int(env_timeout) // 2)
                except ValueError:
                    pass
            env = os.environ.copy()
            env["AI_DIGESTING"] = "1"
            proc = subprocess.run(
                [ai_bin, digest_prompt],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=task_timeout,
                env=env
            )
            summary = proc.stdout.strip()
            if not summary:
                summary = "(sub-agent returned no output)"
        except Exception as ex:
            summary = f"(sub-agent error: {ex})"

        return (f"[Very large file: {total_lines} lines, {total_chars} chars | {abs_path}]\n"
                f"[Digest produced by sub-agent]\n\n{summary}")

    except Exception as e:
        return f"Error opening file: {e}"

def write_file(path, content):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as f:
            f.write(content)
        return f"File successfully written to {path}"
    except Exception as e:
        return f"Error writing file: {e}"

def edit_file(path, search_content, replace_content):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return f"Error: file {path} does not exist."
        with open(abs_path, "r") as f:
            content = f.read()
        if search_content in content:
            new_content = content.replace(search_content, replace_content)
            with open(abs_path, "w") as f:
                f.write(new_content)
            return f"File successfully edited at {path}"
        # Fuzzy retry: strip trailing whitespace per line and compare
        search_lines = search_content.splitlines()
        content_lines = content.splitlines()
        n = len(search_lines)
        matched_start = -1
        for i in range(len(content_lines) - n + 1):
            if all(content_lines[i + j].rstrip() == search_lines[j].rstrip()
                   for j in range(n)):
                matched_start = i
                break
        if matched_start >= 0:
            content_lines_with_ends = content.splitlines(keepends=True)
            original_span = "".join(content_lines_with_ends[matched_start:matched_start + n])
            new_content = content.replace(original_span, replace_content, 1)
            with open(abs_path, "w") as f:
                f.write(new_content)
            return f"File successfully edited at {path} (fuzzy whitespace match used)"
        return (f"Error: search content not found in {path}. "
                f"Make sure the search block matches exactly including whitespace.")
    except Exception as e:
        return f"Error editing file: {e}"

def render_math(text):
    latex_symbols = {
        r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\delta': 'δ', 
        r'\epsilon': 'ε', r'\zeta': 'ζ', r'\eta': 'η', r'\theta': 'θ',
        r'\iota': 'ι', r'\kappa': 'κ', r'\lambda': 'λ', r'\mu': 'μ', 
        r'\nu': 'ν', r'\xi': 'ξ', r'\pi': 'π', r'\rho': 'ρ', 
        r'\sigma': 'σ', r'\tau': 'τ', r'\upsilon': 'υ', r'\phi': 'φ', 
        r'\chi': 'χ', r'\psi': 'ψ', r'\omega': 'omega',
        r'\Delta': 'Δ', r'\Theta': 'Θ', r'\Lambda': 'Λ', r'\Pi': 'Π', 
        r'\Sigma': 'Σ', r'\Phi': 'Φ', r'\Psi': 'Ψ', r'\Omega': 'Ω',
        r'\infty': '∞', r'\times': '×', r'\div': '÷', r'\pm': '±',
        r'\cdot': '·', r'\neq': '≠', r'\ne': '≠', r'\leq': '≤', 
        r'\le': '≤', r'\geq': '≥', r'\ge': '≥', r'\approx': '≈', 
        r'\propto': '∝', r'\partial': '∂', r'\nabla': '∇', 
        r'\sum': '∑', r'\prod': '∏', r'\int': '∫', r'\oint': '∮',
        r'\sqrt': '√', r'\sim': '~', r'\forall': '∀', r'\exists': '∃', 
        r'\in': '∈', r'\notin': '∉', r'\ni': '∋', r'\emptyset': '∅', 
        r'\cap': '∩', r'\cup': '∪', r'\subset': '⊂', r'\supset': '⊃',
        r'\subseteq': '⊆', r'\supseteq': '⊇', r'\rightarrow': '→', 
        r'\leftarrow': '←', r'\uparrow': '↑', r'\downarrow': '↓', 
        r'\leftrightarrow': '↔', r'\Rightarrow': '⇒', r'\Leftarrow': '⇐',
        r'\hbar': 'ħ', r'\degree': '°'
    }
    
    text = text.replace('$$', '').replace('$', '')
    for latex, unicode_char in latex_symbols.items():
        text = text.replace(latex, unicode_char)
        
    superscripts = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾', 'n': 'ⁿ', 'i': 'ⁱ'}
    subscripts = {'0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉', '+': '₊', '-': '₋', '=': '₌', '(': '₍', ')': '₎', 'i': 'ᵢ', 'j': 'ⱼ', 'k': 'ₖ', 'x': 'ₓ'}
    
    def repl_super(match):
        val = match.group(1) or match.group(2)
        return "".join(superscripts.get(c, c) for c in val)
        
    text = re.sub(r'\^\{([^}]+)\}|\^([0-9+\-nix]+)', repl_super, text)
    
    def repl_sub(match):
        val = match.group(1) or match.group(2)
        return "".join(subscripts.get(c, c) for c in val)
        
    text = re.sub(r'\_\{([^}]+)\}|\_([0-9+\-ijkx]+)', repl_sub, text)
    text = re.sub(r'√\{([^}]+)\}', r'√\1', text)
    return text

def render_math_safely(line):
    code_placeholder = "___CODE_PLACEHOLDER_{}___"
    codes = []
    
    def repl_code(match):
        codes.append(match.group(0))
        return code_placeholder.format(len(codes) - 1)
        
    # Protect backtick inline code
    temp_line = re.sub(r'`[^`\n]+`', repl_code, line)
    
    # Render block math $$ ... $$
    def repl_block_math(match):
        math_content = match.group(1)
        return render_math(math_content)
        
    temp_line = re.sub(r'\$\$(.*?)\$\$', repl_block_math, temp_line)
    
    # Render inline math $ ... $
    def repl_inline_math(match):
        math_content = match.group(1)
        return render_math(math_content)
        
    temp_line = re.sub(r'\$([^$]+)\$', repl_inline_math, temp_line)
    
    # Render scientific notation outside math blocks (e.g. 10^-3 or 2^10)
    superscripts = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾', 'n': 'ⁿ', 'i': 'ⁱ'}
    def repl_super(match):
        val = match.group(1) or match.group(2)
        return "".join(superscripts.get(c, c) for c in val)
    
    temp_line = re.sub(r'(?<=\d)\^\{([^}]+)\}|(?<=\d)\^([0-9+\-nix]+)', repl_super, temp_line)
    
    # Restore code blocks
    for idx, c in enumerate(codes):
        temp_line = temp_line.replace(code_placeholder.format(idx), c)
    return temp_line

def render_markdown(text):
    lines = text.splitlines()
    rendered = []
    in_code_block = False
    
    # Helper to check if a line is a table row
    def is_table_row(line):
        return '|' in line

    # Helper to check if a line is a table separator
    def is_table_separator(line):
        if '|' not in line:
            return False
        cleaned = line.replace('|', '').replace(':', '').replace('-', '').strip()
        return len(cleaned) == 0

    # Helper to parse a markdown table row into cells
    def parse_row(row):
        parts = row.split('|')
        if parts[0].strip() == '': parts = parts[1:]
        if len(parts) > 0 and parts[-1].strip() == '': parts = parts[:-1]
        return [cell.strip() for cell in parts]
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    def visible_len(t):
        return len(ansi_escape.sub('', t))
    def format_cell(cell, is_header=False):
        cell = re.sub(r'\*\*(.*?)\*\*', r'\033[1m\1\033[22m', cell)
        cell = re.sub(r'(?<!\w)__(.*?)__(?!\w)', r'\033[1m\1\033[22m', cell)
        cell = re.sub(r'\*(.*?)\*', r'\033[3m\1\033[23m', cell)
        cell = re.sub(r'(?<!\w)_(.*?)_(?!\w)', r'\033[3m\1\033[23m', cell)
        cell = re.sub(r'`(.*?)`', r'\033[33m\1\033[39m', cell)
        cell = render_math_safely(cell)
        if is_header:
            return f"\033[1;36m{cell}\033[0m"
        return cell
    def pad_cell(cell, width, align):
        vis_len = visible_len(cell)
        padding = width - vis_len
        if padding <= 0: return cell
        if align == 'center':
            left = padding // 2
            right = padding - left
            return ' ' * left + cell + ' ' * right
        elif align == 'right':
            return ' ' * padding + cell
        else: return cell + ' ' * padding
    def render_table(table_rows):
        if len(table_rows) < 2: return table_rows
        header_cells = parse_row(table_rows[0])
        alignments = []
        sep_cells = parse_row(table_rows[1])
        for cell in sep_cells:
            if cell.startswith(':') and cell.endswith(':'): alignments.append('center')
            elif cell.endswith(':'): alignments.append('right')
            else: alignments.append('left')
        body_rows = [parse_row(r) for r in table_rows[2:]]
        num_cols = len(header_cells)
        aligned_body = []
        for r in body_rows:
            if len(r) < num_cols: r = r + [''] * (num_cols - len(r))
            elif len(r) > num_cols: r = r[:num_cols]
            aligned_body.append(r)
        if len(alignments) < num_cols: alignments = alignments + ['left'] * (num_cols - len(alignments))
        alignments = alignments[:num_cols]

        raw_widths = [0] * num_cols
        for col_idx in range(num_cols):
            w = len(header_cells[col_idx])
            for r in aligned_body:
                w = max(w, len(r[col_idx]))
            raw_widths[col_idx] = w

        import shutil
        term_width = shutil.get_terminal_size((80, 24)).columns
        border_overhead = 3 * num_cols + 1
        available_width = term_width - border_overhead

        col_widths = list(raw_widths)
        total_raw = sum(raw_widths)
        if total_raw > available_width and available_width > 0:
            allocated = [min(w, 8) for w in raw_widths]
            remaining = available_width - sum(allocated)
            if remaining > 0:
                needs_more = [i for i, w in enumerate(raw_widths) if w > allocated[i]]
                if needs_more:
                    total_needed = sum(raw_widths[i] - allocated[i] for i in needs_more)
                    for i in needs_more:
                        extra = int(remaining * (raw_widths[i] - allocated[i]) / total_needed)
                        allocated[i] += extra
                    diff = available_width - sum(allocated)
                    idx = 0
                    while diff > 0 and needs_more:
                        allocated[needs_more[idx % len(needs_more)]] += 1
                        diff -= 1
                        idx += 1
            col_widths = [max(1, w) for w in allocated]

        def wrap_text(text, width):
            if not text:
                return [""]
            segments = text.split('\n')
            all_lines = []
            for segment in segments:
                words = segment.split(' ')
                current_line = []
                current_len = 0
                for word in words:
                    word_len = len(word)
                    if current_len + word_len + (1 if current_line else 0) <= width:
                        current_line.append(word)
                        current_len += word_len + (1 if len(current_line) > 1 else 0)
                    else:
                        if word_len > width:
                            if current_line:
                                all_lines.append(" ".join(current_line))
                            for j in range(0, len(word), width):
                                all_lines.append(word[j:j+width])
                            current_line = []
                            current_len = 0
                        else:
                            if current_line:
                                all_lines.append(" ".join(current_line))
                            current_line = [word]
                            current_len = word_len
                if current_line:
                    all_lines.append(" ".join(current_line))
            return all_lines

        def render_wrapped_row(row_cells, is_header=False):
            wrapped_cells = []
            for col_idx in range(num_cols):
                wrapped_cells.append(wrap_text(row_cells[col_idx], col_widths[col_idx]))
            max_lines = max(len(c) for c in wrapped_cells)
            for col_idx in range(num_cols):
                while len(wrapped_cells[col_idx]) < max_lines:
                    wrapped_cells[col_idx].append("")
            row_lines = []
            for line_idx in range(max_lines):
                line_parts = []
                for col_idx in range(num_cols):
                    cell_line = wrapped_cells[col_idx][line_idx]
                    formatted = format_cell(cell_line, is_header)
                    padded = pad_cell(formatted, col_widths[col_idx], alignments[col_idx])
                    line_parts.append(f" {padded} ")
                row_lines.append('\033[90m│\033[0m' + '\033[90m│\033[0m'.join(line_parts) + '\033[90m│\033[0m')
            return row_lines

        top_parts = ['─' * (w + 2) for w in col_widths]
        top_line = '\033[90m┌' + '┬'.join(top_parts) + '┐\033[0m'
        
        header_lines = render_wrapped_row(header_cells, is_header=True)
        
        sep_parts = ['─' * (w + 2) for w in col_widths]
        sep_line = '\033[90m├' + '┼'.join(sep_parts) + '┤\033[0m'
        
        body_lines = []
        for row in aligned_body:
            body_lines.extend(render_wrapped_row(row, is_header=False))
            
        bottom_parts = ['─' * (w + 2) for w in col_widths]
        bottom_line = '\033[90m└' + '┴'.join(bottom_parts) + '┘\033[0m'
        
        return [top_line] + header_lines + [sep_line] + body_lines + [bottom_line]

    i = 0
    in_code_block = False
    lang = ""
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            if not in_code_block:
                in_code_block = True
                lang = line[3:].strip().lower()
            else:
                in_code_block = False
                lang = ""
            rendered.append("\033[90m" + "─" * 45 + "\033[0m")
            i += 1
            continue
            
        if in_code_block:
            rendered.append(highlight_line(line, lang))
            i += 1
            continue
            
        # Handle markdown tables
        if not in_code_block and is_table_row(line) and i + 1 < len(lines) and is_table_separator(lines[i + 1]):
            table_rows = []
            while i < len(lines) and is_table_row(lines[i]):
                table_rows.append(lines[i])
                i += 1
            rendered.extend(render_table(table_rows))
            continue

        h_match = re.match(r'^(#{1,6})\s+(.*)', line)
        if h_match:
            level = len(h_match.group(1))
            content = h_match.group(2)
            color = "35" if level == 1 else ("34" if level == 2 else "36")
            rendered.append(f"\n\033[1;{color}m{content}\033[0m")
            i += 1
            continue
            
        list_match = re.match(r'^(\s*[-*+])\s+(.*)', line)
        if list_match:
            indent = list_match.group(1)[:-1]
            content = list_match.group(2)
            line = f"{indent}• {content}"
            
        num_match = re.match(r'^(\s*\d+\.)\s+(.*)', line)
        if num_match:
            prefix = num_match.group(1)
            content = num_match.group(2)
            line = f"{prefix} {content}"

        line = re.sub(r'\*\*(.*?)\*\*', r'\033[1m\1\033[22m', line)
        line = re.sub(r'(?<!\w)__(.*?)__(?!\w)', r'\033[1m\1\033[22m', line)
        line = re.sub(r'\*(.*?)\*', r'\033[3m\1\033[23m', line)
        line = re.sub(r'(?<!\w)_(.*?)_(?!\w)', r'\033[3m\1\033[23m', line)
        line = re.sub(r'`(.*?)`', r'\033[33m\1\033[39m', line)
        
        line = render_math_safely(line)
        
        rendered.append(line)
        i += 1
        
    return "\n".join(rendered)

TOOL_REQUIRED_ARGS = {
    "execute_command": ["command"],
    "web_search":      ["query"],
    "fetch_webpage":   ["url"],
    "fetch_smart":     ["url"],
    "read_file":       ["path"],
    "write_file":      ["path", "content"],
    "edit_file":       ["path", "search_content", "replace_content"],
    "save_memory":     ["content"],
    "delegate_task":   ["tasks"],
    "parallel_fetch":  ["urls"],
    "think":           ["reasoning"],
    "task_complete":   ["summary"],
    "computer_control": ["action"],
    "pubmed_search":          ["query"],
    "pubmed_research_round":  ["query"],
}

# Per-agent and per-URL output caps for parallel tools
_AGENT_OUTPUT_CAP = 10 * 1024       # 10 KB per sub-agent result
_PARALLEL_FETCH_CAP = 10 * 1024     # 10 KB per fetched URL

def _resolve_ai_bin():
    """Return path to the ai binary, checking local install first."""
    candidates = [
        os.path.expanduser("~/.local/bin/ai"),
        os.environ.get("INFER_BIN_PATH", ""),
        "/usr/local/bin/ai",
        "./ai",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return candidates[-1]  # fall back to ./ai even if missing

def _pubmed_fetch_raw(query, top_k=10, start_date=None, end_date=None, high_quality_only=True):
    """Return parsed JSON results list from the search API, or a string error."""
    import urllib.request as _req
    import json as _json

    base_url = os.environ.get("PUBMED_SEARCH_URL", "http://152.53.80.217:8080").rstrip("/")
    api_key  = os.environ.get("PUBMED_API_KEY") or os.environ.get("MSS_API_KEY", "")
    if not api_key:
        return "Error: no API key found. Set PUBMED_API_KEY (or MSS_API_KEY) environment variable."

    top_k = max(5, min(10, int(top_k)))
    payload = {"query": query, "top_k": top_k, "high_quality_only": bool(high_quality_only)}
    if start_date:
        payload["start_date"] = start_date
    if end_date:
        payload["end_date"] = end_date

    data = _json.dumps(payload).encode("utf-8")
    req = _req.Request(
        f"{base_url}/search",
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    try:
        with _req.urlopen(req, timeout=60) as resp:
            body = _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return f"Error calling PubMed search API: {e}"

    return body


def pubmed_search(query, top_k=10, start_date=None, end_date=None, high_quality_only=True):
    body = _pubmed_fetch_raw(query, top_k, start_date, end_date, high_quality_only)
    if isinstance(body, str):
        return body  # error string

    results = body.get("results", [])
    if not results:
        return f"No results found for query: {query}"

    lines = [
        f"Search: \"{body.get('query', query)}\"  |  "
        f"{body.get('total_results', len(results))} result(s)  |  "
        f"{body.get('search_time_seconds', '?')}s\n"
    ]
    for i, p in enumerate(results, 1):
        doi = p.get("doi", "N/A")
        doi_url = f"https://doi.org/{doi}" if doi and doi != "N/A" else "N/A"
        lines.append(
            f"[{i}] {p.get('title', 'N/A')}\n"
            f"    Authors : {p.get('authors', 'N/A')}\n"
            f"    Journal : {p.get('journal', 'N/A')}  ({p.get('year', '?')})  [{p.get('source', '')}]\n"
            f"    DOI     : {doi}  →  {doi_url}\n"
            f"    Score   : {p.get('score', '?')}\n"
            f"    Abstract: {p.get('abstract', 'N/A')}\n"
        )
    return "\n".join(lines)

def pubmed_research_round(query, known_dois=None, start_date=None, end_date=None):
    """
    Fetch full abstracts from the search API, read them in Python, and return a
    compact structured digest to the main agent. No LLM sub-process — fast (~1.5s).

    The full abstract text is read here; only extracted key sentences go back to
    the main agent, keeping its context clean across multiple rounds.
    """
    body = _pubmed_fetch_raw(query, top_k=10, start_date=start_date, end_date=end_date)
    if isinstance(body, str):
        return body  # error

    results = body.get("results", [])
    elapsed = body.get("search_time_seconds", "?")
    if not results:
        return f"No results for: {query}"

    known = set(known_dois or [])
    new_papers = []
    repeat_dois = []

    for p in results:
        doi = p.get("doi", "N/A") or "N/A"
        abstract = (p.get("abstract", "") or "").strip()

        # Extract leading sentences (usually background + main finding) and trailing
        # sentence (usually conclusion). Full abstract is read; we surface key parts.
        sentences = re.split(r'(?<=[.!?])\s+', abstract)
        if len(sentences) >= 3:
            excerpt = " ".join(sentences[:2]) + " … " + sentences[-1]
        elif sentences:
            excerpt = " ".join(sentences[:3])
        else:
            excerpt = abstract[:300]

        entry = dict(
            doi=doi,
            title=p.get("title", "N/A"),
            authors=p.get("authors", "N/A"),
            journal=p.get("journal", "N/A"),
            year=p.get("year", "?"),
            source=p.get("source", ""),
            score=round(float(p.get("score", 0)), 4),
            excerpt=excerpt,
        )

        if doi != "N/A" and doi in known:
            repeat_dois.append(doi)
        else:
            new_papers.append(entry)

    # Sort new papers chronologically so temporal narrative is immediately readable
    new_papers.sort(key=lambda x: (x["year"] if isinstance(x["year"], int) else 0))

    lines = [
        f'ROUND: "{query}"',
        f'API: {len(results)} results in {elapsed}s | {len(new_papers)} new, {len(repeat_dois)} already seen\n',
    ]

    if new_papers:
        lines.append("PAPERS (new, oldest → newest):")
        for p in new_papers:
            doi_url = f"https://doi.org/{p['doi']}" if p['doi'] != "N/A" else "N/A"
            lines.append(
                f"  {p['year']} [{p['score']}] {p['title']}\n"
                f"    {p['authors']}\n"
                f"    {p['journal']} [{p['source']}]\n"
                f"    DOI: {p['doi']}  →  {doi_url}\n"
                f"    Abstract: {p['excerpt']}\n"
            )

    if repeat_dois:
        lines.append(f"ALREADY SEEN DOIs: {', '.join(repeat_dois)}")

    return "\n".join(lines)

def repair_json(s):
    """Best-effort repair of common small-model JSON mistakes."""
    s = s.strip()
    # Remove trailing commas before } or ]
    s = re.sub(r',\s*([}\]])', r'\1', s)
    # Close unclosed braces (truncated output)
    if s and s[0] == '{':
        depth = sum(1 if c == '{' else -1 if c == '}' else 0 for c in s)
        s += '}' * max(0, depth)
    return s

def main():
    if len(sys.argv) < 2:
        print("Usage: ai_mcp.py [list-tools | call-tool | render-markdown | trim-messages]", file=sys.stderr)
        sys.exit(1)

    action = sys.argv[1]
    mcp_servers = load_config()

    if action == "render-markdown":
        if len(sys.argv) < 3:
            sys.exit(0)
        text = sys.argv[2]
        print(render_markdown(text))
        sys.exit(0)

    if action == "trim-messages":
        if len(sys.argv) < 3:
            sys.exit(1)
        try:
            with open(sys.argv[2]) as f:
                messages = json.load(f)
            # Compress think reasoning in assistant messages to reclaim context tokens
            MAX_THINK = 120
            compressed = []
            for msg in messages:
                if msg.get('role') == 'assistant' and 'tool_calls' in msg:
                    new_calls = []
                    for call in msg['tool_calls']:
                        if call.get('function', {}).get('name') == 'think':
                            try:
                                args = json.loads(call['function']['arguments'])
                                r = args.get('reasoning', '')
                                if len(r) > MAX_THINK:
                                    args['reasoning'] = r[:MAX_THINK] + '…'
                                    call = dict(call)
                                    call['function'] = dict(call['function'])
                                    call['function']['arguments'] = json.dumps(args)
                            except Exception:
                                pass
                        new_calls.append(call)
                    msg = dict(msg)
                    msg['tool_calls'] = new_calls
                compressed.append(msg)
            # Keep: system prompt (0), first user turn (1), last 20 messages
            if len(compressed) > 22:
                compressed = compressed[:2] + compressed[-20:]
            print(json.dumps(compressed))
        except Exception:
            print("[]")
        sys.exit(0)

    if action == "list-tools":
        openai_tools = []

        # 1. think — first so small models see it first
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "think",
                "description": "Plan before a multi-step task. Call ONCE before your first action — never again after any non-think tool has been called. Keep reasoning under 50 words.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reasoning": {
                            "type": "string",
                            "description": "Brief plan (≤50 words): what steps you will take and in what order."
                        }
                    },
                    "required": ["reasoning"]
                }
            }
        })

        # 2. execute_command
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Run a shell command on the host system and return its stdout and stderr. Use for any system task, file inspection, running scripts, installing packages, or verification. Prefer this over describing commands to the user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact shell command to execute."
                        }
                    },
                    "required": ["command"]
                }
            }
        })

        # 3. web_search
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web using DuckDuckGo to find current information, prices, news, documentation, or facts you don't know. Always follow with fetch_webpage on at least one result URL before calling task_complete.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query."
                        }
                    },
                    "required": ["query"]
                }
            }
        })

        # 4. fetch_webpage
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "fetch_webpage",
                "description": "Download and read a static, unprotected URL quickly. For any URL that might be JS-rendered or bot-protected, use fetch_smart instead. Required before task_complete if search returned URLs — never present links without reading them.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL of the webpage to fetch."
                        }
                    },
                    "required": ["url"]
                }
            }
        })

        # 4b. fetch_smart
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "fetch_smart",
                "description": (
                    "Preferred fetch tool. Downloads a URL using browser TLS fingerprint impersonation "
                    "(curl_cffi) to bypass Cloudflare and similar bot protections. Automatically escalates "
                    "to Playwright+stealth for JS-rendered pages if the fast path is blocked. "
                    "Use this for any URL that might be protected, JS-heavy, or from a news/media site. "
                    "Falls back to fetch_webpage if curl_cffi is not installed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL to fetch."
                        }
                    },
                    "required": ["url"]
                }
            }
        })

        # 5. read_file
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read the contents of a file. Supports text files, PDFs (extracts text), "
                    "and image files (PNG, JPG, JPEG, WEBP) which are shown in context. "
                    "For large text files an outline + head/tail is returned automatically. "
                    "Use start_line and end_line to read a specific section of a large file."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file to read."
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "First line to return (1-based, inclusive). Omit to start from the beginning."
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Last line to return (1-based, inclusive). Omit to read to the end."
                        }
                    },
                    "required": ["path"]
                }
            }
        })

        # 6. write_file
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file, creating it and any parent directories if needed. After writing a script, always run it with execute_command to verify it works.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file to write to."
                        },
                        "content": {
                            "type": "string",
                            "description": "The exact content to write to the file."
                        }
                    },
                    "required": ["path", "content"]
                }
            }
        })

        # 7. edit_file
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Apply a search-and-replace edit to an existing file. The search_content must match exactly including whitespace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file to edit."
                        },
                        "search_content": {
                            "type": "string",
                            "description": "The exact text block to search for and replace."
                        },
                        "replace_content": {
                            "type": "string",
                            "description": "The replacement text block."
                        }
                    },
                    "required": ["path", "search_content", "replace_content"]
                }
            }
        })

        # 8. list_directory
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List the contents of a directory on the host system. Use to explore project structure before reading specific files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the directory to list. Defaults to '.' if not specified."
                        }
                    }
                }
            }
        })

        # 9. save_memory
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "save_memory",
                "description": "Save key facts, user preferences, or context to persistent memory. This memory is automatically loaded in subsequent runs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The exact content to store in memory. Keep it concise."
                        }
                    },
                    "required": ["content"]
                }
            }
        })

        # 10. delegate_task
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "delegate_task",
                "description": (
                    "Spawn N helper agents that run IN PARALLEL and return their combined results. "
                    "Use ONLY for independent sub-tasks (each agent must not depend on another's output). "
                    "Always pass 'tasks' as an array — even for a single task. "
                    "Example: fetch and summarise 3 papers → tasks:[\"Fetch https://... and summarise\", \"Fetch https://... and summarise\", ...]. "
                    "Each task string must be fully self-contained with all context (URLs, file paths, goals). "
                    "For fetching multiple URLs, prefer parallel_fetch instead — it is faster and needs no per-URL instructions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of self-contained task instructions. Each runs in its own agent concurrently."
                        }
                    },
                    "required": ["tasks"]
                }
            }
        })

        # 11. parallel_fetch
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "parallel_fetch",
                "description": (
                    "Fetch multiple URLs concurrently and return all page contents in one call. "
                    "Use instead of multiple sequential fetch_webpage calls whenever you need 2+ pages. "
                    "Example use cases: reading several search results, fetching multiple papers/docs, "
                    "multi-site comparison, publication digest. "
                    "Each result is capped at 10 KB and labelled with its URL."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of URLs to fetch concurrently."
                        }
                    },
                    "required": ["urls"]
                }
            }
        })

        # 12. load_skill
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": "Explore and load domain skills. Call with no argument (empty name) to list all available skills with descriptions. Call with a skill name to read its full guidance. Always call with no argument first if you are unsure what skills exist, then load the relevant one before starting domain work.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Skill directory name to load (e.g. 'bio_structure_analysis'). Omit or leave empty to list all available skills."
                        }
                    }
                }
            }
        })

        # 13. computer_control
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "computer_control",
                "description": "Control the user's screen/windows, capture screenshots, move mouse, click, type, and manipulate windows.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "screenshot",
                                "click",
                                "double_click",
                                "right_click",
                                "mouse_move",
                                "mouse_drag",
                                "type_text",
                                "key_combo",
                                "minimize_all",
                                "minimize_window",
                                "maximize_window",
                                "close_window",
                                "list_windows"
                            ],
                            "description": "The specific control action to perform."
                        },
                        "x": {
                            "type": "integer",
                            "description": "X coordinate for mouse actions (optional)."
                        },
                        "y": {
                            "type": "integer",
                            "description": "Y coordinate for mouse actions (optional)."
                        },
                        "text": {
                            "type": "string",
                            "description": "The text to type or the key combination to press (optional)."
                        },
                        "window_id": {
                            "type": "string",
                            "description": "The target window ID or name (optional)."
                        }
                    },
                    "required": ["action"]
                }
            }
        })

        # 14. fetch_webpage_js — Playwright-based for JS-protected sites
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "fetch_webpage_js",
                "description": (
                    "Explicit Playwright override: renders the page with a real headless browser + stealth patches "
                    "(playwright-stealth, realistic 1920×1080 viewport, lazy-load scroll). "
                    "Use only when you need direct control over JS rendering or wait_for behaviour. "
                    "For most cases, prefer fetch_smart which cascades automatically."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL to fetch with a headless browser."
                        },
                        "wait_for": {
                            "type": "string",
                            "enum": ["networkidle", "load", "domcontentloaded"],
                            "description": "When to consider the page ready. 'networkidle' (default) waits for no network activity — best for SPAs. 'load' waits for the load event. 'domcontentloaded' is fastest but may miss late-rendered content."
                        }
                    },
                    "required": ["url"]
                }
            }
        })

        # pubmed_research_round — delegates to a sub-agent; main agent never sees raw abstracts
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "pubmed_research_round",
                "description": (
                    "Search biomedical literature and return a compact digest. "
                    "Fetches full abstracts from the API, reads them in Python, and surfaces the key opening and closing sentences per paper. "
                    "Use this for ALL literature research — prefer it over pubmed_search. "
                    "Call with different descriptive-sentence queries across multiple rounds: "
                    "read digest → note new DOIs and gaps → call again with refined query → repeat until saturation → synthesise. "
                    "Pass known_dois from previous rounds to track overlap."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Full descriptive sentence (60–300 chars) describing what the ideal abstract would say. "
                                "Semantic search — longer sentences outperform short keywords. "
                                "Example: 'Uromodulin protects the kidney against ascending urinary tract infections by forming a gel barrier that traps uropathogenic bacteria in the tubular lumen'"
                            )
                        },
                        "known_dois": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "DOIs already collected in previous rounds — sub-agent will flag overlaps."
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Restrict to papers published on or after YYYY-MM-DD. Optional."
                        },
                        "end_date": {
                            "type": "string",
                            "description": "Restrict to papers published on or before YYYY-MM-DD. Optional."
                        }
                    },
                    "required": ["query"]
                }
            }
        })

        # pubmed_search
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "pubmed_search",
                "description": (
                    "Semantic search across 50M+ biomedical abstracts (PubMed, BioRxiv, MedRxiv, arXiv). "
                    "Returns titles, authors, journals, DOIs, abstracts, and relevance scores. "
                    "Use iteratively: search → digest abstracts → refine query → search again → synthesise. "
                    "Always report DOIs so manuscripts can be retrieved. "
                    "Requires PUBMED_API_KEY (or MSS_API_KEY) environment variable."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural-language search query (3–500 chars), e.g. 'CRISPR base editing off-target effects'."
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results to return (5–10, default 10).",
                            "default": 10
                        },
                        "start_date": {
                            "type": "string",
                            "description": "Restrict to papers published on or after this date (YYYY-MM-DD). Optional."
                        },
                        "end_date": {
                            "type": "string",
                            "description": "Restrict to papers published on or before this date (YYYY-MM-DD). Optional."
                        },
                        "high_quality_only": {
                            "type": "boolean",
                            "description": "Exclude papers with missing or very short abstracts (default true).",
                            "default": True
                        }
                    },
                    "required": ["query"]
                }
            }
        })

        # 12. task_complete — last so model only sees it as exit
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "task_complete",
                "description": "Call this ONLY when you have the verified answer from tools. Write the full result in summary — this is the only output the user sees. Do not call this if you still have URLs to fetch, commands to run, or scripts to verify.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "The complete answer or result for the user, in markdown. Include all relevant data you gathered from tools."
                        }
                    },
                    "required": ["summary"]
                }
            }
        })

        for server_name, cfg in mcp_servers.items():
            tools = list_tools(server_name, cfg)
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("inputSchema", {
                            "type": "object",
                            "properties": {}
                        })
                    }
                })

        print(json.dumps(openai_tools))

    elif action == "call-tool":
        if len(sys.argv) < 5:
            print("Usage: ai_mcp.py call-tool <server_name> <tool_name> <arguments_json>", file=sys.stderr)
            sys.exit(1)
        
        server_name = sys.argv[2]
        tool_name = sys.argv[3]
        args_json = sys.argv[4]

        try:
            arguments = json.loads(args_json)
        except Exception:
            try:
                arguments = json.loads(repair_json(args_json))
            except Exception as e:
                print(json.dumps({"error": f"Failed to parse arguments JSON even after repair: {e}"}))
                sys.exit(1)

        # Validate required arguments before dispatch
        required = TOOL_REQUIRED_ARGS.get(tool_name, [])
        if tool_name == "delegate_task":
            if "task" in arguments or "tasks" in arguments:
                required = []
        missing = [k for k in required if k not in arguments]
        if missing:
            print(json.dumps({"error": f"Missing required argument(s): {', '.join(missing)}"}))
            sys.exit(0)

        # Route custom tools
        if tool_name == "think" or server_name == "think":
            # Handled natively in C; this is a safety fallback
            print('{"ok": true}')
        elif tool_name == "task_complete" or server_name == "task_complete":
            # Handled natively in C; this is a safety fallback
            print('{"ok": true}')
        elif tool_name == "list_directory" or server_name == "list_directory":
            path = arguments.get("path", ".")
            result = list_directory(path)
            print(result)
        elif tool_name == "web_search" or server_name == "web_search":
            query = arguments.get("query", "")
            result = ddg_lite_search(query)
            print(result)
        elif tool_name == "fetch_webpage" or server_name == "fetch_webpage":
            url = arguments.get("url", "")
            result = fetch_webpage(url)
            print(result)
        elif tool_name == "fetch_smart" or server_name == "fetch_smart":
            url = arguments.get("url", "")
            result = fetch_smart(url)
            print(result)
        elif tool_name == "fetch_webpage_js" or server_name == "fetch_webpage_js":
            url = arguments.get("url", "")
            wait_for = arguments.get("wait_for", "networkidle")
            result = fetch_webpage_js(url, wait_for=wait_for)
            print(result)
        elif tool_name == "save_memory" or server_name == "save_memory":
            content = arguments.get("content", "")
            result = save_memory(content)
            print(result)
        elif tool_name == "read_file" or server_name == "read_file":
            path = arguments.get("path", "")
            start_line = arguments.get("start_line", None)
            end_line = arguments.get("end_line", None)
            result = read_file(path, start_line=start_line, end_line=end_line)
            print(result)
        elif tool_name == "write_file" or server_name == "write_file":
            path = arguments.get("path", "")
            content = arguments.get("content", "")
            result = write_file(path, content)
            print(result)
        elif tool_name == "edit_file" or server_name == "edit_file":
            path = arguments.get("path", "")
            search_content = arguments.get("search_content", "")
            replace_content = arguments.get("replace_content", "")
            result = edit_file(path, search_content, replace_content)
            print(result)
        elif tool_name == "delegate_task" or server_name == "delegate_task":
            tasks = arguments.get("tasks")
            if not isinstance(tasks, list):
                # Accept legacy single-task call gracefully
                single_task = arguments.get("task", "")
                tasks = [single_task] if single_task else []

            if not tasks:
                print("Error: delegate_task requires 'tasks' array with at least one item.")
            else:
                try:
                    import concurrent.futures
                    ai_bin = _resolve_ai_bin()

                    task_timeout = 300
                    env_timeout = os.environ.get("INFER_TASK_TIMEOUT")
                    if env_timeout:
                        try:
                            task_timeout = int(env_timeout)
                        except ValueError:
                            pass

                    n = len(tasks)
                    print(f"[delegate_task] Starting {n} parallel agent(s)...", file=sys.stderr, flush=True)

                    def run_single_agent(t_desc, idx):
                        cmd_args = [ai_bin, "-y", "-q", t_desc]
                        try:
                            proc = subprocess.run(
                                cmd_args,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                timeout=task_timeout
                            )
                            out = (proc.stdout or "").strip()
                            # Only surface stderr when the agent failed
                            if proc.returncode != 0:
                                err = (proc.stderr or "").strip()
                                if err:
                                    out = f"[exit {proc.returncode}] {out}\n[stderr]: {err[:500]}"
                            if not out:
                                out = f"Agent #{idx+1} completed with no output (exit {proc.returncode})."
                            # Cap per-agent output
                            if len(out) > _AGENT_OUTPUT_CAP:
                                out = out[:_AGENT_OUTPUT_CAP] + f"\n... [truncated at {_AGENT_OUTPUT_CAP//1024} KB]"
                            return idx, out
                        except subprocess.TimeoutExpired:
                            return idx, f"Error: agent #{idx+1} timed out after {task_timeout}s."
                        except Exception as ex:
                            return idx, f"Error in agent #{idx+1}: {ex}"

                    results_map = {}
                    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
                        futures = {executor.submit(run_single_agent, t, i): i for i, t in enumerate(tasks)}
                        for future in concurrent.futures.as_completed(futures):
                            try:
                                idx, res = future.result()
                                results_map[idx] = res
                                print(f"[delegate_task] Agent #{idx+1}/{n} done.", file=sys.stderr, flush=True)
                            except Exception as e:
                                idx = futures[future]
                                results_map[idx] = f"Error in thread for agent #{idx+1}: {e}"

                    combined = ""
                    for i in range(n):
                        label = f"--- Agent {i+1}/{n} ---\n" if n > 1 else ""
                        combined += label + results_map.get(i, "") + "\n\n"
                    print(combined.strip())
                except Exception as e:
                    print(f"Error in delegate_task: {e}")

        elif tool_name == "parallel_fetch" or server_name == "parallel_fetch":
            import concurrent.futures
            urls = arguments.get("urls", [])
            if not urls:
                print("Error: parallel_fetch requires 'urls' array with at least one URL.")
            else:
                n = len(urls)
                print(f"[parallel_fetch] Fetching {n} URL(s) concurrently...", file=sys.stderr, flush=True)

                def _fetch_one(url, idx):
                    try:
                        text = fetch_webpage(url)
                        if len(text) > _PARALLEL_FETCH_CAP:
                            text = text[:_PARALLEL_FETCH_CAP] + f"\n... [truncated at {_PARALLEL_FETCH_CAP//1024} KB]"
                        print(f"[parallel_fetch] {idx+1}/{n} done: {url[:60]}", file=sys.stderr, flush=True)
                        return idx, text
                    except Exception as ex:
                        return idx, f"Error fetching {url}: {ex}"

                results_map = {}
                with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
                    futures = {executor.submit(_fetch_one, u, i): i for i, u in enumerate(urls)}
                    for future in concurrent.futures.as_completed(futures):
                        idx, res = future.result()
                        results_map[idx] = res

                combined = ""
                for i in range(n):
                    combined += f"=== URL {i+1}: {urls[i]} ===\n{results_map.get(i, '')}\n\n"
                print(combined.strip())
        elif tool_name == "load_skill" or server_name == "load_skill":
            import re as _re
            skill_name = arguments.get("name", "").strip()
            skill_dirs = [
                os.path.join(os.getcwd(), ".agents", "skills"),
                os.path.join(os.path.expanduser("~"), ".config", "ai", "skills"),
            ]
            if not skill_name:
                # List mode: return index of name + description for every available skill
                index = "Available skills (call load_skill(name) to read full guidance):\n"
                seen = set()
                for base in skill_dirs:
                    if not os.path.isdir(base):
                        continue
                    for entry in sorted(os.listdir(base)):
                        if entry in seen:
                            continue
                        skill_path = os.path.join(base, entry, "SKILL.md")
                        if os.path.isfile(skill_path):
                            try:
                                with open(skill_path, "r", encoding="utf-8", errors="replace") as f:
                                    header = f.read(512)
                                m = _re.search(r'^description:\s*(.+)', header, _re.MULTILINE)
                                desc = m.group(1).strip() if m else "(no description)"
                            except Exception:
                                desc = "(unreadable)"
                            index += f"- {entry}: {desc}\n"
                            seen.add(entry)
                print(index if seen else "No skills found.")
            else:
                # Load mode: return full content of named skill
                found = False
                for base in skill_dirs:
                    skill_path = os.path.join(base, skill_name, "SKILL.md")
                    if os.path.isfile(skill_path):
                        try:
                            with open(skill_path, "r", encoding="utf-8", errors="replace") as f:
                                content = f.read()
                            print(f"[Skill: {skill_name}]\n{content}")
                        except Exception as e:
                            print(f"Error reading skill '{skill_name}': {e}")
                        found = True
                        break
                if not found:
                    available = []
                    for base in skill_dirs:
                        if os.path.isdir(base):
                            available.extend(os.listdir(base))
                    print(f"Skill '{skill_name}' not found. Call load_skill() with no argument to list available skills.")
        elif tool_name == "computer_control" or server_name == "computer_control":
            result = computer_control(arguments)
            print(result)
        elif tool_name == "pubmed_research_round" or server_name == "pubmed_research_round":
            result = pubmed_research_round(
                query=arguments.get("query", ""),
                known_dois=arguments.get("known_dois"),
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
            )
            print(result)
        elif tool_name == "pubmed_search" or server_name == "pubmed_search":
            result = pubmed_search(
                query=arguments.get("query", ""),
                top_k=arguments.get("top_k", 10),
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                high_quality_only=arguments.get("high_quality_only", True),
            )
            print(result)
        else:
            # Route to MCP server
            cfg = mcp_servers.get(server_name)
            if not cfg:
                # Try matching clean server name
                for k in mcp_servers.keys():
                    clean_k = "".join(c if c.isalnum() or c == "_" else "_" for c in k)
                    if clean_k == server_name:
                        cfg = mcp_servers[k]
                        break

            if not cfg:
                print(json.dumps({"error": f"MCP server '{server_name}' not found in config"}))
                sys.exit(1)

            result = call_tool(server_name, cfg, tool_name, arguments)
            print(json.dumps(result))

if __name__ == "__main__":
    main()
