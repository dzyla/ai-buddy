#!/usr/bin/env python3
import sys
import os
import json
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import re
import time
import xml.etree.ElementTree as ET
import sqlite3

try:
    import trafilatura
    _HAS_TRAFILATURA = True
except ImportError:
    _HAS_TRAFILATURA = False

SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://search.inetol.net",
    "https://searxng.online",
    "https://priv.au",
    "https://searx.tiekoetter.com",
    "https://search.sapti.me",
    "https://paulgo.io",
    "https://searx.lunar.icu",
    "https://search.rhscz.eu",
    "https://etsi.me",
]

CONFIG_PATHS = [
    os.path.join(os.getcwd(), "mcp.json"),
    os.path.join(os.getcwd(), "mcp_config.json"),
    os.path.expanduser("~/.config/ai/mcp.json"),
    os.path.expanduser("~/.config/ai/mcp_config.json"),
    os.path.expanduser("~/.gemini/config/mcp_config.json"),
    os.path.expanduser("~/.lmstudio/mcp.json"),
]

VAULT_DIR = os.path.expanduser("~/.config/ai/vault")

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

def log_metric(tool_name, duration_ms, success=True):
    try:
        import datetime
        metrics_file = os.path.expanduser("~/.cache/ai/metrics.jsonl")
        os.makedirs(os.path.dirname(metrics_file), exist_ok=True)
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "tool": tool_name,
            "duration_ms": round(duration_ms, 2),
            "success": success
        }
        with open(metrics_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

def show_metrics():
    metrics_file = os.path.expanduser("~/.cache/ai/metrics.jsonl")
    if not os.path.isfile(metrics_file):
        print("No metrics logged yet.")
        return
    
    stats = {}
    total_calls = 0
    try:
        with open(metrics_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    t = data.get("tool", "unknown")
                    dur = data.get("duration_ms", 0)
                    succ = data.get("success", True)
                    total_calls += 1
                    if t not in stats:
                        stats[t] = {"count": 0, "success": 0, "total_ms": 0.0}
                    stats[t]["count"] += 1
                    if succ:
                        stats[t]["success"] += 1
                    stats[t]["total_ms"] += dur
                except Exception:
                    pass
    except Exception as e:
        print(f"Error reading metrics: {e}")
        return

    print("=== AI Buddy Metrics & Tool Usage Statistics ===")
    print(f"Total Tool Executions Logged: {total_calls}\n")
    print(f"{'Tool Name':<28} | {'Calls':<7} | {'Success':<8} | {'Avg Time (ms)':<14}")
    print("-" * 65)
    for t, s in sorted(stats.items(), key=lambda x: x[1]["count"], reverse=True):
        avg_ms = round(s["total_ms"] / s["count"], 1) if s["count"] > 0 else 0.0
        succ_rate = f"{s['success']}/{s['count']}"
        print(f"{t:<28} | {s['count']:<7} | {succ_rate:<8} | {avg_ms:<14}")

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

# --- Helper functions for improvements ---
CONTEXT_POOL_FILE = os.path.expanduser("~/.config/ai/context_pool.json")

def _load_context_pool():
    if os.path.exists(CONTEXT_POOL_FILE):
        try:
            with open(CONTEXT_POOL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def _save_context_pool(pool):
    os.makedirs(os.path.dirname(CONTEXT_POOL_FILE), exist_ok=True)
    with open(CONTEXT_POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, indent=2)

def append_to_context_pool(entry):
    pool = _load_context_pool()
    pool.append({"id": len(pool), "timestamp": time.time(), "content": str(entry)})
    _save_context_pool(pool)

def get_context_snippet(index):
    pool = _load_context_pool()
    try:
        idx = int(index)
        if 0 <= idx < len(pool):
            return json.dumps(pool[idx])
        return f"Error: Context snippet index {idx} out of range (0-{len(pool)-1})."
    except Exception as e:
        return f"Error: {e}"

def search_context(query):
    pool = _load_context_pool()
    if not query:
        return "Error: query required"
    results = []
    q_lower = query.lower()
    for item in pool:
        content = item.get("content", "")
        if q_lower in content.lower():
            results.append(f"[{item['id']}]: {content[:400]}")
    if not results:
        return f"No context entries matching query '{query}'."
    return "\n".join(results)

def execute_command(command, timeout=120):
    if not command or not command.strip():
        return "Error: empty command"
    try:
        if isinstance(timeout, str):
            timeout = int(timeout)
    except ValueError:
        timeout = 120
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        out = proc.stdout + proc.stderr
        if proc.returncode == 0:
            res = f"success\n{out}" if out else "success"
        else:
            res = f"failed (exit {proc.returncode})\n{out}\n[SYSTEM WARNING: Command failed. You MUST use the `think` tool to analyze the failure, explain why it failed, and formulate a new plan before executing another command.]"
        append_to_context_pool(res[:1000])
        return res
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing command: {e}"

def structured_query(target, filter_expr=None, transform=None, aggregate=None):
    text = ""
    if target.startswith("file:"):
        fpath = target[5:]
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        else:
            return f"Error: file '{fpath}' not found"
    elif target.startswith("command:"):
        cmd = target[8:]
        text = execute_command(cmd)
    else:
        text = target

    lines = text.splitlines()
    if filter_expr:
        import re
        lines = [l for l in lines if re.search(filter_expr, l)]

    if transform:
        if transform.startswith("head:"):
            n = int(transform[5:])
            lines = lines[:n]
        elif transform.startswith("tail:"):
            n = int(transform[5:])
            lines = lines[-n:]
        elif transform == "sort":
            lines = sorted(lines)
        elif transform == "unique":
            lines = list(dict.fromkeys(lines))

    if aggregate:
        if aggregate == "count":
            return str(len(lines))
        elif aggregate == "first":
            return lines[0] if lines else ""
        elif aggregate == "last":
            return lines[-1] if lines else ""

    return "\n".join(lines)

AGENT_STORE_DIR = os.path.expanduser("~/.config/ai/agents")

def spawn_agent(name, prompt, tools=None, persistent=True):
    os.makedirs(AGENT_STORE_DIR, exist_ok=True)
    agent_id = f"agent_{name}_{int(time.time())}"
    data = {
        "id": agent_id,
        "name": name,
        "prompt": prompt,
        "tools": tools or ["execute_command", "read_file"],
        "history": [],
        "created_at": time.time(),
        "status": "idle"
    }
    with open(os.path.join(AGENT_STORE_DIR, f"{agent_id}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return f"Spawned agent {name} (ID: {agent_id})"

def _resolve_ai_bin():
    local_ai = os.path.join(os.getcwd(), "ai")
    if os.path.isfile(local_ai) and os.access(local_ai, os.X_OK):
        return local_ai
    return shutil.which("ai") or "ai"

def resume_agent(agent_id, user_message):
    path = os.path.join(AGENT_STORE_DIR, f"{agent_id}.json")
    if not os.path.exists(path):
        return f"Error: Agent '{agent_id}' not found."
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["history"].append({"role": "user", "content": user_message})
    ai_bin = _resolve_ai_bin()
    res = subprocess.run([ai_bin, "-y", "-q", user_message], capture_output=True, text=True, timeout=180)
    out = res.stdout or res.stderr
    data["history"].append({"role": "assistant", "content": out})
    data["last_activity"] = time.time()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return out

def list_agents():
    if not os.path.exists(AGENT_STORE_DIR):
        return "No agents registered."
    files = [f for f in os.listdir(AGENT_STORE_DIR) if f.endswith(".json")]
    if not files:
        return "No agents found."
    agents_info = []
    for fname in files:
        with open(os.path.join(AGENT_STORE_DIR, fname), "r", encoding="utf-8") as f:
            d = json.load(f)
            agents_info.append(f"- {d.get('name')} ({d.get('id')}): {d.get('status', 'idle')} - {d.get('prompt')[:60]}")
    return "\n".join(agents_info)

SESSION_LOG_FILE = os.path.expanduser("~/.config/ai/session_outcomes.json")

def session_report(success=True, failure_modes=None, notes=""):
    os.makedirs(os.path.dirname(SESSION_LOG_FILE), exist_ok=True)
    logs = []
    if os.path.exists(SESSION_LOG_FILE):
        try:
            with open(SESSION_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "success": bool(success),
        "failure_modes": failure_modes or [],
        "notes": notes
    }
    logs.append(entry)
    with open(SESSION_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2)
    return "Session outcome logged successfully."

def count_tokens(model, text):
    if not text:
        return "0"
    try:
        if "gpt" in model.lower():
            import tiktoken
            enc = tiktoken.encoding_for_model(model)
            return str(len(enc.encode(text)))
        else:
            return str(len(text) // 4)
    except Exception:
        return str(len(text) // 4)


def arxiv_search(query, max_results=5):
    try:
        max_results = min(int(max_results), 10)
        url = f"http://export.arxiv.org/api/query?search_query=all:{urllib.parse.quote(query)}&start=0&max_results={max_results}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        if not entries:
            return "No results found on arXiv."
        
        results = []
        for i, entry in enumerate(entries, 1):
            title = entry.find("atom:title", ns)
            title = title.text.replace("\\n", " ").strip() if title is not None else "No title"
            summary = entry.find("atom:summary", ns)
            summary = summary.text.replace("\\n", " ").strip() if summary is not None else "No summary"
            link = entry.find("atom:id", ns)
            link = link.text if link is not None else ""
            authors = [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns) if a.find("atom:name", ns) is not None]
            results.append(f"[{i}] Title: {title}\\nAuthors: {', '.join(authors)}\\nURL: {link}\\nAbstract: {summary}\\n")
        return "\\n".join(results)
    except Exception as e:
        return f"Error during arXiv search: {e}"

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

_SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}

def brave_search(query, num_results=8):
    """Scrape Brave Search HTML; return (list_of_result_strs, top_url) or (None, None)."""
    q = urllib.parse.quote(query)
    url = f"https://search.brave.com/search?q={q}&source=web"
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=_SEARCH_HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                html = r.read().decode('utf-8', errors='ignore')
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(2)
                continue
            return None, None
        except Exception:
            return None, None

    blocks = re.findall(r'data-type="web".*?(?=data-type="web"|</main>)', html, re.DOTALL)
    lines = []
    top_url = None
    for block in blocks[:num_results]:
        url_m = re.search(
            r'<a href="(https?://(?!imgs\.search\.brave\.com|cdn\.search\.brave\.com)[^"]+)"',
            block,
        )
        url = url_m.group(1) if url_m else ''
        title_m = re.search(r'class="[^"]*search-snippet-title[^"]*" title="([^"]+)"', block)
        title = title_m.group(1) if title_m else 'No title'
        snip_m = re.search(
            r'class="[^"]*desktop-default-regular[^"]*t-primary[^"]*"[^>]*>(.*?)</div>',
            block, re.DOTALL,
        )
        snippet = re.sub(r'<!--.*?-->|<[^>]+>', '', snip_m.group(1) if snip_m else '').strip()
        if url and not top_url:
            top_url = url
        lines.append(f"Title: {title}\nURL: {url}\nSnippet: {snippet}\n")
    if not lines:
        return None, None
    return lines, top_url

def searxng_search(query, num_results=8):
    """Try public SearXNG instances; return (list_of_result_strs, top_url) or (None, None)."""
    params = urllib.parse.urlencode({
        'q': query, 'format': 'json',
        'engines': 'google,bing,duckduckgo', 'pageno': 1,
    })
    for base_url in SEARXNG_INSTANCES:
        try:
            req = urllib.request.Request(f"{base_url}/search?{params}", headers=_SEARCH_HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            results = data.get('results', [])[:num_results]
            if not results:
                continue
            lines = []
            top_url = None
            for r in results:
                url = r.get('url', '')
                title = r.get('title', 'No title')
                snippet = r.get('content', '')
                if not top_url and url.startswith('http'):
                    top_url = url
                lines.append(f"Title: {title}\nURL: {url}\nSnippet: {snippet}\n")
            return lines, top_url
        except Exception:
            continue
    return None, None

def _finalize_search(lines, top_url):
    """Join result lines and auto-fetch the top result for full content."""
    output = "\n".join(lines)
    if top_url:
        try:
            full = fetch_webpage(top_url)
            body = full.split('\n\n', 1)[-1] if '\n\n' in full else full
            if len(body.split()) > 40:
                body_trimmed = body[:4000] + ("\n... [more at URL]" if len(body) > 4000 else "")
                output += f"\n---\n[Top result full content — {top_url}]\n{body_trimmed}"
        except Exception:
            pass
    return output

def web_search(query):
    """Search: Brave Search → SearXNG instances → DuckDuckGo Lite."""
    lines, top_url = brave_search(query)
    if lines:
        return _finalize_search(lines, top_url)

    lines, top_url = searxng_search(query)
    if lines:
        return _finalize_search(lines, top_url)

    return ddg_lite_search(query)

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


def fetch_webpage_basic(url):
    """Plain fetch: trafilatura extraction → urllib + regex fallback (no TLS
    impersonation, no JS). Used as the final fallback rung of fetch_smart."""
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


def fetch_webpage(url):
    """Default page fetch. Routes through the robust fetch_smart cascade
    (curl_cffi TLS impersonation → Playwright+stealth → plain urllib) so the
    model's everyday fetch handles bot-walls and JS-heavy sites, not just
    static HTML. Set INFER_FETCH_BASIC=1 to force the plain path."""
    if os.environ.get("INFER_FETCH_BASIC") == "1":
        return fetch_webpage_basic(url)
    return fetch_smart(url)


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
        return fetch_webpage_basic(url)
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
    result = fetch_webpage_basic(url)
    body_part = result.split('\n\n', 1)[-1] if '\n\n' in result else result
    if _is_blocked(body_part):
        result = f"[FETCH_WARN: site resisted all fetch methods — content may be incomplete]\n\n{result}"
    return result


MEMORY_PATH = os.path.expanduser("~/.config/ai/memory.txt")
MEMORY_DB = os.path.expanduser("~/.config/ai/memory.db")

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

def _ensure_memories_schema(conn):
    """Ensure the memories FTS5 table has a metadata column.
    FTS5 virtual tables cannot be ALTERED, so we rebuild if needed."""
    # First, make sure the table exists at all (old schema with just content)
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(content)")
    # Check if metadata column exists
    try:
        cursor = conn.execute("PRAGMA table_info(memories)")
        columns = {row[1] for row in cursor.fetchall()}
    except Exception:
        columns = set()
    if "metadata" in columns:
        return  # Already has the column
    # Rebuild: backup content, drop table, recreate with metadata, reinsert
    old_rows = conn.execute("SELECT content FROM memories").fetchall()
    conn.execute("DROP TABLE IF EXISTS memories")
    conn.execute(
        "CREATE VIRTUAL TABLE memories USING fts5(content, metadata)"
    )
    for (content,) in old_rows:
        conn.execute(
            "INSERT INTO memories(content, metadata) VALUES (?, ?)",
            (content, ""),
        )
    conn.commit()


def _ensure_vault_schema(conn):
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS vault_fts USING fts5(title, content);"
    )

def vault_write(title, content, links=""):
    try:
        os.makedirs(VAULT_DIR, exist_ok=True)
        title = title.replace("/", "_").replace("\\\\", "_")
        if not title.endswith(".md"):
            title += ".md"
        
        path = os.path.join(VAULT_DIR, title)
        
        full_content = content
        if links:
            # Parse links (comma separated) and format as [[Link]]
            link_list = [l.strip() for l in links.split(",") if l.strip()]
            if link_list:
                full_content += "\\n\\n---\\n**Links:** " + ", ".join([f"[[{l}]]" for l in link_list])
                
        with open(path, "w") as f:
            f.write(full_content)
            
        # Update SQLite index
        os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)
        conn = sqlite3.connect(MEMORY_DB)
        _ensure_vault_schema(conn)
        conn.execute("DELETE FROM vault_fts WHERE title = ?", (title,))
        conn.execute("INSERT INTO vault_fts(title, content) VALUES (?, ?)", (title, full_content))
        conn.commit()
        conn.close()
        return f"Successfully wrote note '{title}' to vault."
    except Exception as e:
        return f"Error writing to vault: {e}"

def vault_read(title):
    try:
        if not title.endswith(".md"):
            title += ".md"
        path = os.path.join(VAULT_DIR, title)
        if not os.path.exists(path):
            return f"Note '{title}' not found in vault."
        with open(path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading vault note: {e}"

def vault_search(query):
    try:
        os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)
        conn = sqlite3.connect(MEMORY_DB)
        _ensure_vault_schema(conn)
        safe_query = query.replace("'", "''")
        rows = conn.execute(
            "SELECT title, content FROM vault_fts WHERE vault_fts MATCH ? LIMIT 10",
            (safe_query,),
        ).fetchall()
        conn.close()
        
        if not rows:
            return "No matching notes found."
            
        results = []
        for t, c in rows:
            preview = c[:200].replace("\\n", " ") + ("..." if len(c) > 200 else "")
            results.append(f"- **{t}**: {preview}")
        return "\\n".join(results)
    except Exception as e:
        return f"Error searching vault: {e}"

def vault_backlinks(title):
    try:
        if title.endswith(".md"):
            title = title[:-3] # Remove .md for backlink search
        os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)
        conn = sqlite3.connect(MEMORY_DB)
        _ensure_vault_schema(conn)
        # Search for [[title]] using exact phrase match
        safe_query = f'"{title}"' 
        rows = conn.execute(
            "SELECT title FROM vault_fts WHERE content MATCH ? LIMIT 20",
            (safe_query,),
        ).fetchall()
        conn.close()
        
        if not rows:
            return f"No backlinks found for '{title}'."
            
        results = [f"- [[{t[0]}]]" for t in rows]
        return f"Notes linking to '{title}':\\n" + "\\n".join(results)
    except Exception as e:
        return f"Error finding backlinks: {e}"


def remember(content, metadata=""):
    """Save a piece of information to the FTS5 memory database with optional metadata."""
    try:
        os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)
        conn = sqlite3.connect(MEMORY_DB)
        _ensure_memories_schema(conn)
        if len(content) > 4000:
            content = content[-4000:]
        if metadata:
            metadata = str(metadata)[:200]
        conn.execute(
            "INSERT INTO memories(content, metadata) VALUES (?, ?)",
            (content, metadata),
        )
        conn.commit()
        conn.close()
        return f"Remembered: {content[:80]}{'...' if len(content) > 80 else ''}"
    except Exception as e:
        return f"Error saving memory: {e}"


def recall(query):
    """Search memories using FTS5 full-text search with fallback to LIKE search."""
    try:
        os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)
        conn = sqlite3.connect(MEMORY_DB)
        _ensure_memories_schema(conn)
        
        # Extract alphanumeric words for safe FTS query
        words = re.findall(r'\w+', query)
        fts_query = " OR ".join(words) if words else query
        
        rows = []
        if fts_query:
            try:
                rows = conn.execute(
                    "SELECT content, metadata FROM memories WHERE memories MATCH ? ORDER BY rank LIMIT 10",
                    (fts_query,),
                ).fetchall()
            except sqlite3.Error:
                rows = []

        if not rows and words:
            # Fallback to LIKE search for key words
            like_clauses = " OR ".join(["content LIKE ? OR metadata LIKE ?" for _ in words[:5]])
            like_params = []
            for w in words[:5]:
                like_params.extend([f"%{w}%", f"%{w}%"])
            rows = conn.execute(
                f"SELECT content, metadata FROM memories WHERE {like_clauses} LIMIT 10",
                tuple(like_params),
            ).fetchall()

        conn.close()
        if not rows:
            return "No memories found matching that query."
        results = []
        for content, meta in rows:
            entry = content
            if meta:
                entry += f"\n[metadata: {meta}]"
            results.append(entry)
        return "\n---\n".join(results)
    except Exception as e:
        return f"Error searching memories: {e}"


# ── Searchable conversation history (backup + FTS index) ────────────────────
# Every conversation is backed up to BOTH the cache (fast, ~/.cache/ai/sessions)
# and the persistent local user-data dir (~/.local/share/ai/sessions), and each
# turn is appended to ~/.cache/ai/history.jsonl with a session_id. This module
# builds a fast SQLite FTS5 index over all of it so the agent can search and
# learn from past conversations quickly.

CACHE_SESSIONS = os.path.expanduser("~/.cache/ai/sessions")
DATA_SESSIONS = os.path.expanduser("~/.local/share/ai/sessions")
HISTORY_LOG = os.path.expanduser("~/.cache/ai/history.jsonl")
HISTORY_DB = os.path.expanduser("~/.local/share/ai/history_index.db")


def _session_dirs():
    return [d for d in (CACHE_SESSIONS, DATA_SESSIONS) if os.path.isdir(d)]


def _ensure_history_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS history(session_id TEXT, ts TEXT, prompt TEXT, response TEXT, body TEXT)")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(session_id, ts, prompt, response, body)")
    conn.execute("CREATE TABLE IF NOT EXISTS hmeta(k TEXT PRIMARY KEY, v TEXT)")
    conn.commit()


def _iter_history_log_lines():
    """Yield parsed records from history.jsonl. Returns (line_skip, records)."""
    records = []
    if not os.path.isfile(HISTORY_LOG):
        return records
    try:
        with open(HISTORY_LOG, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                records.append(rec)
    except Exception:
        pass
    return records


def _history_is_stale():
    """Rebuild the index if the history log or session set changed since the last
    build. Uses byte-size (history.jsonl is append-only) and session-file count so
    it's reliable even when files share the same mtime second."""
    if not os.path.isfile(HISTORY_DB):
        return True
    try:
        conn = sqlite3.connect(HISTORY_DB)
        row = conn.execute("SELECT v FROM hmeta WHERE k='log_size'").fetchone()
        sess = conn.execute("SELECT v FROM hmeta WHERE k='sess_count'").fetchone()
        conn.close()
    except Exception:
        return True
    try:
        log_size = os.path.getsize(HISTORY_LOG) if os.path.isfile(HISTORY_LOG) else 0
    except Exception:
        log_size = 0
    sess_count = 0
    for d in _session_dirs():
        try:
            sess_count += len([f for f in os.listdir(d) if f.endswith(".json")])
        except Exception:
            pass
    prev_log = int(row[0]) if row else -1
    prev_sess = int(sess[0]) if sess else -1
    return log_size != prev_log or sess_count != prev_sess


def rebuild_history_index():
    """(Re)build the full-text conversation index from history.jsonl + session files.
    Idempotent: wipes and reinserts (history.jsonl is bounded and typically small-to-moderate).
    Returns a human-readable summary of how many turns/sessions were indexed."""
    try:
        os.makedirs(os.path.dirname(HISTORY_DB), exist_ok=True)
        conn = sqlite3.connect(HISTORY_DB)
        conn.execute("DROP TABLE IF EXISTS history")
        conn.execute("DROP TABLE IF EXISTS history_fts")
        _ensure_history_schema(conn)

        session_ids = set()
        n_turns = 0
        n_sessions = 0

        # 1) From history.jsonl (prompt -> response pairs)
        for rec in _iter_history_log_lines():
            prompt = rec.get("prompt", "")
            response = rec.get("response", "")
            sid = rec.get("session_id", "") or "unknown"
            ts = rec.get("timestamp", "")
            body = (prompt or "") + "\n" + (response or "")
            if not prompt and not response:
                continue
            try:
                conn.execute(
                    "INSERT INTO history(session_id, ts, prompt, response, body) VALUES (?,?,?,?,?)",
                    (sid, ts, prompt, response, body),
                )
            except Exception:
                continue
            n_turns += 1
            if sid and sid != "unknown":
                session_ids.add(sid)

        # 2) From session files (full transcripts) — index session summaries
        seen = set()
        for d in _session_dirs():
            if not os.path.isdir(d):
                continue
            try:
                for fn in sorted(os.listdir(d)):
                    if not fn.endswith(".json"):
                        continue
                    p = os.path.join(d, fn)
                    if p in seen:
                        continue
                    seen.add(p)
                    try:
                        with open(p, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        messages = json.loads(content)
                        if not isinstance(messages, list):
                            continue
                        sid = fn[:-5]
                        # Build a compact transcript text for FTS.
                        parts = []
                        last_user = ""
                        for m in messages:
                            if not isinstance(m, dict):
                                continue
                            role = m.get("role", "")
                            txt = m.get("content", "")
                            if isinstance(txt, list):
                                txt = " ".join(
                                    str(c.get("text", "")) for c in txt if isinstance(c, dict)
                                )
                            txt = str(txt).strip()
                            if role == "user" and txt:
                                last_user = txt
                                parts.append("USER: " + txt)
                            elif role == "assistant" and txt:
                                parts.append("ASSISTANT: " + txt)
                        body = "\n".join(parts)
                        prompt = last_user or ""
                        ts = ""
                        conn.execute(
                            "INSERT INTO history(session_id, ts, prompt, response, body) VALUES (?,?,?,?,?)",
                            (sid, ts, prompt, body[:2000], body),
                        )
                        n_turns += 1
                        n_sessions += 1
                    except Exception:
                        continue
            except Exception:
                continue

        # Copy everything into the FTS virtual table (columns: session_id,ts,prompt,response,body)
        try:
            conn.execute("INSERT INTO history_fts(session_id, ts, prompt, response, body) SELECT session_id, ts, prompt, response, body FROM history")
        except Exception:
            pass
        # Record source fingerprints for staleness detection.
        try:
            conn.execute("INSERT OR REPLACE INTO hmeta(k, v) VALUES ('log_size', ?)",
                         (str(os.path.getsize(HISTORY_LOG)) if os.path.isfile(HISTORY_LOG) else "0",))
            conn.execute("INSERT OR REPLACE INTO hmeta(k, v) VALUES ('sess_count', ?)",
                         (str(n_sessions),))
        except Exception:
            pass
        conn.commit()
        conn.close()
        return f"[history] Indexed {n_turns} turn(s) / {n_sessions} session file(s): {HISTORY_DB}"
    except Exception as e:
        return f"Error building history index: {e}"


def search_history(query, limit=8):
    """FTS search across all past conversations. Returns matched turns/sessions
    with snippet + session_id, so the agent can learn from earlier sessions and
    then load the full session if needed. Rebuilds the index if it's stale/missing."""
    if not query:
        return "Error: query required"
    try:
        if _history_is_stale():
            rebuild_history_index()
        conn = sqlite3.connect(HISTORY_DB)
        words = re.findall(r"\w+", query)
        limit = int(limit)
        rows = []

        def _try(q, cols):
            # cols: "fts" -> SELECT rows w/ (session_id, ts, prompt, response) using rank;
            # covered by the FTS virtual table columns.
            try:
                if cols == "fts":
                    return conn.execute(
                        "SELECT session_id, ts, prompt, response FROM history_fts "
                        "WHERE history_fts MATCH ? ORDER BY rank LIMIT ?",
                        (q, max(limit, 1)),
                    ).fetchall()
                return conn.execute(
                    f"SELECT session_id, ts, {cols} FROM history WHERE {cols} LIKE ? LIMIT ?",
                    (f"%{q}%", max(limit, 1)),
                ).fetchall()
            except Exception:
                return []

        # Prefer exact phrase, then all-terms (AND), then any-term (OR), then LIKE.
        if words:
            phrase = '"' + " ".join(words) + '"'
            all_terms = " AND ".join(words)
            any_terms = " OR ".join(words)
            rows = _try(phrase, "fts") or _try(all_terms, "fts") or _try(any_terms, "fts")
            if not rows:
                like = " OR ".join(["body LIKE ? OR prompt LIKE ?" for _ in words[:5]])
                params = [f"%{w}%" for w in words[:5] for _ in range(2)]
                try:
                    rows = conn.execute(
                        f"SELECT session_id, ts, substr(body,1,240) FROM history WHERE {like} LIMIT ?",
                        tuple(params) + (max(limit, 1),),
                    ).fetchall()
                except Exception:
                    rows = []
        conn.close()

        seen = set()
        deduped = []
        for r in rows:
            sid, ts, *_rest = r
            key = (sid, str(_rest[0])[:40])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)

        if not deduped:
            return f"No past conversation matches '{query}'. Try a broader term, or use list_sessions to see recent history."
        out = [f"[history] {len(deduped)} match(es) for '{query}':\n"]
        for sid, ts, *rest in deduped:
            text = " | ".join(str(x) for x in rest if x)
            out.append(f"- session {sid or '?'} ({ts or '?'}): {text[:200]}")
        return "\n".join(out)
    except Exception as e:
        return f"Error searching history: {e}"


def list_sessions(limit=12):
    """List the most recent backed-up conversations (session id, mtime, bytes)."""
    try:
        items = []
        for d in _session_dirs():
            for fn in os.listdir(d):
                if fn.endswith(".json"):
                    p = os.path.join(d, fn)
                    try:
                        st = os.stat(p)
                        items.append((st.st_mtime, fn[:-5], st.st_size, p))
                    except Exception:
                        continue
        items.sort(reverse=True)
        items = items[: int(limit)]
        if not items:
            return "No backed-up sessions found."
        lines = ["Recent backed-up sessions:"]
        for _mt, sid, size, p in items:
            lines.append(f"- {sid}  ({size} B)  -> {p}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing sessions: {e}"


def get_session(session_id, max_chars=4000):
    """Load a full backed-up conversation by session_id and return a readable transcript."""
    if not session_id:
        return "Error: session_id required (use list_sessions or search_history to find one)."
    for d in _session_dirs():
        p = os.path.join(d, f"{session_id}.json")
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                messages = json.loads(content)
                parts = []
                for m in messages:
                    if not isinstance(m, dict):
                        continue
                    role = m.get("role", "")
                    txt = m.get("content", "")
                    if isinstance(txt, list):
                        txt = " ".join(str(c.get("text", "")) for c in txt if isinstance(c, dict))
                    if isinstance(txt, str) and txt.strip():
                        parts.append(f"[{role}]\n{txt.strip()}")
                body = "\n\n".join(parts)
                if len(body) > int(max_chars):
                    body = body[: int(max_chars)] + "\n... [truncated — read the file for the full transcript]"
                return f"[session {session_id}] from {p}\n\n{body}"
            except Exception as e:
                return f"Error reading session {session_id}: {e}"
    return f"Session '{session_id}' not found."


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

def _extract_text_from_pdf_impl(path):
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

    return source_header + "Error: Could not extract text from PDF using any backend."

def extract_text_from_pdf(path):
    import hashlib
    cache_dir = os.path.expanduser("~/.cache/ai/pdf_cache")
    os.makedirs(cache_dir, exist_ok=True)
    try:
        mtime = os.path.getmtime(path)
    except:
        mtime = 0
    cache_key = hashlib.md5(f"{path}_{mtime}".encode('utf-8')).hexdigest()
    cache_path = os.path.join(cache_dir, f"{cache_key}.txt")
    
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return f.read()
        except:
            pass
            
    content = _extract_text_from_pdf_impl(path)
    
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(content)
    except:
        pass
        
    return content

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
            content = extract_text_from_pdf(abs_path)
        else:
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
            # If search_content doesn't end with a newline, strip the trailing newline from original_span
            # so we don't eat it during replacement.
            if not search_content.endswith(('\n', '\r')):
                if original_span.endswith('\r\n'):
                    original_span = original_span[:-2]
                elif original_span.endswith('\n'):
                    original_span = original_span[:-1]
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
    if not text:
        return ""
    try:
        from rich.console import Console
        from rich.markdown import Markdown, Heading, BlockQuote
        from rich.text import Text
        from rich.segment import Segment
        import shutil

        def custom_heading_rich_console(self, console, options):
            text = self.text
            text.justify = "left"
            if self.tag == "h1":
                yield Text("")
                yield Text("# ", style="bold cyan") + text
                yield Text("")
            elif self.tag == "h2":
                yield Text("")
                yield Text("## ", style="bold magenta") + text
                yield Text("")
            else:
                yield Text("")
                yield Text("#" * int(self.tag[1]) + " ", style="bold yellow") + text
                yield Text("")

        def custom_blockquote_rich_console(self, console, options):
            render_options = options.update(width=options.max_width - 4)
            lines = console.render_lines(self.elements, render_options, style=self.style)
            style = self.style
            new_line = Segment("\n")
            padding = Segment("  ", style)
            for line in lines:
                yield padding
                yield from line
                yield new_line

        Heading.__rich_console__ = custom_heading_rich_console
        BlockQuote.__rich_console__ = custom_blockquote_rich_console

        cols = shutil.get_terminal_size((80, 24)).columns
        console = Console(width=min(cols, 110), force_terminal=True, legacy_windows=False)
        with console.capture() as capture:
            console.print(Markdown(text))
        raw_res = capture.get()
        lines = [line.rstrip() for line in raw_res.splitlines()]
        res = "\n".join(lines).strip('\n')
        if res:
            return res
    except Exception:
        pass

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

        def render_wrapped_row(row_cells, is_header=False, row_idx=None):
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
                    # Alternating row shading for body rows
                    if row_idx is not None and row_idx % 2 == 1 and not is_header:
                        # Subtle dim background for even-indexed body rows
                        padded = f"\033[48;5;236m{padded}\033[0m"
                    line_parts.append(f" {padded} ")
                row_lines.append('\033[90m│\033[0m' + '\033[90m│\033[0m'.join(line_parts) + '\033[90m│\033[0m')
            return row_lines

        top_parts = ['─' * (w + 2) for w in col_widths]
        top_line = '\033[90m┌' + '┬'.join(top_parts) + '┐\033[0m'
        
        header_lines = render_wrapped_row(header_cells, is_header=True)
        
        sep_parts = ['─' * (w + 2) for w in col_widths]
        sep_line = '\033[90m├' + '┼'.join(sep_parts) + '┤\033[0m'
        
        body_lines = []
        for row_idx, row in enumerate(aligned_body):
            body_lines.extend(render_wrapped_row(row, is_header=False, row_idx=row_idx))
            
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
                lang_display = lang if lang else "text"
                # Badge-style language label with dim background
                badge = f"\033[38;5;245m {lang_display} \033[0m"
                rendered.append(f"{badge}  \033[90m{'─' * 40}\033[0m")
                code_line_num = 0
            else:
                in_code_block = False
                lang = ""
                rendered.append(f"\033[90m{'─' * 42}\033[0m")
            i += 1
            continue
            
        if in_code_block:
            # Line numbers in dim gray, content with syntax highlighting
            rendered.append(f"  \033[90m{code_line_num:4d}\033[0m {highlight_line(line, lang)[2:]}")
            code_line_num += 1
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
            if level == 1:
                rendered.append(f"\n\033[1;35m{content}\033[0m")
                rendered.append(f"\033[35m{'─' * len(content)}\033[0m")
            elif level == 2:
                rendered.append(f"\n\033[1;34m▸ {content}\033[0m")
            else:
                color = "36" if level == 3 else "90"
                rendered.append(f"\n\033[1;{color}m{content}\033[0m")
            i += 1
            continue

        # Blockquotes: render with a left accent border
        if line.startswith('> '):
            bq_content = line[2:]
            rendered.append(f"  \033[33m│\033[0m {bq_content}")
            i += 1
            continue
            
        list_match = re.match(r'^(\s*[-*+])\s+(.*)', line)
        if list_match:
            indent = list_match.group(1)[:-1]
            content = list_match.group(2)
            # Use colored bullets
            line = f"{indent}\033[36m•\033[0m {content}"
            
        num_match = re.match(r'^(\s*\d+\.)\s+(.*)', line)
        if num_match:
            prefix = num_match.group(1)
            content = num_match.group(2)
            line = f"\033[36m{prefix}\033[0m {content}"

        line = re.sub(r'\*\*(.*?)\*\*', r'\033[1m\1\033[22m', line)
        line = re.sub(r'(?<!\w)__(.*?)__(?!\w)', r'\033[1m\1\033[22m', line)
        line = re.sub(r'\*(.*?)\*', r'\033[3m\1\033[23m', line)
        line = re.sub(r'(?<!\w)_(.*?)_(?!\w)', r'\033[3m\1\033[23m', line)
        line = re.sub(r'`(.*?)`', r'\033[33m\1\033[39m', line)
        
        line = render_math_safely(line)
        
        rendered.append(line)
        i += 1
        
    return "\n".join(rendered)

def get_system_status():
    """Return a compact one-line CPU/RAM/disk summary for quick status checks."""
    import re as _re
    # CPU: parse /proc/stat for aggregate idle fraction
    try:
        with open("/proc/stat") as _f:
            line = _f.readline()
        parts = line.split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + vals[4]  # idle + iowait
        total = sum(vals)
        usage_pct = round(100.0 - (idle * 100.0 / total), 1) if total else 0
    except Exception:
        usage_pct = "?"
    # RAM
    try:
        mem = {}
        with open("/proc/meminfo") as _f:
            for ln in _f:
                k, _, v = ln.partition(":")
                mem[k.strip()] = int(v.split()[0])
        total_kb = mem["MemTotal"]
        avail_kb = mem["MemAvailable"]
        used_pct = round((1 - avail_kb / total_kb) * 100, 1) if total_kb else 0
        used_mb = (total_kb - avail_kb) // 1024
        total_mb = total_kb // 1024
    except Exception:
        used_pct = "?"
        used_mb = "?"
        total_mb = "?"
    # Disk (root)
    try:
        import subprocess as _sp
        out = _sp.run(["df", "-h", "/"], capture_output=True, text=True, check=True)
        disk_line = out.stdout.strip().splitlines()[-1]
        parts = disk_line.split()
        disk_total = parts[1]
        disk_used = parts[2]
        disk_pct = parts[4]
    except Exception:
        disk_total = "?"
        disk_used = "?"
        disk_pct = "?"
    # nproc
    try:
        nproc = os.cpu_count() or "?"
    except Exception:
        nproc = "?"
    return (
        f"CPU: {usage_pct}% | "
        f"RAM: {used_mb}/{total_mb} MB ({used_pct}%) | "
        f"Disk: {disk_used}/{disk_total} ({disk_pct}) | "
        f"Cores: {nproc}"
    )

def get_clipboard():
    """Read the current X/Wayland clipboard content."""
    import subprocess as _sp
    for cmd in [["xclip", "-selection", "clipboard", "-o"],
                ["wl-paste"],
                ["xsel", "--clipboard", "--output"]]:
        try:
            out = _sp.run(cmd, capture_output=True, text=True, timeout=5)
            if out.returncode == 0:
                return out.stdout
        except Exception:
            pass
    return "Error: could not read clipboard (no xclip/wl-paste/xsel available or clipboard is empty)."


def list_processes():
    """Return the top 10 running processes sorted by CPU usage."""
    import subprocess as _sp
    try:
        out = _sp.run(
            ["ps", "aux", "--sort=-%cpu"],
            capture_output=True, text=True, check=True, timeout=10
        )
        lines = out.stdout.strip().splitlines()
        header = lines[0] if lines else ""
        body = lines[1:11]  # top 10 (excluding header)
        if not body:
            return "No running processes found."
        result_lines = [header] + body
        return "\n".join(result_lines)
    except Exception as e:
        return f"Error listing processes: {e}"

def start_background_process(command, log_file=None):
    """Start a command in the background, writing output to a log file for status monitoring."""
    import subprocess, os, time
    if not command:
        return "Error: command string is required."
    if not log_file:
        log_dir = os.path.expanduser("~/.config/ai/logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"proc_{int(time.time())}.log")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
    
    try:
        with open(log_file, "a") as f:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=f,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True
            )
        return (f"[Background Process Started]\n"
                f"PID: {proc.pid}\n"
                f"Command: `{command}`\n"
                f"Log File: `{log_file}`\n"
                f"Use `check_process_status` with pid={proc.pid} or log_file='{log_file}' to evaluate execution health and logs.")
    except Exception as e:
        return f"Error starting background process: {e}"

def check_process_status(pid=None, log_file=None):
    """Inspect process state, read log tail, evaluate execution health, and return a decision summary."""
    import os
    if not pid and not log_file:
        return "Error: provide at least 'pid' or 'log_file' to check status."

    pid_status = "UNKNOWN"
    if pid is not None:
        try:
            pid_int = int(pid)
            os.kill(pid_int, 0)
            pid_status = "RUNNING"
        except ProcessLookupError:
            pid_status = "TERMINATED/EXITED"
        except PermissionError:
            pid_status = "RUNNING (active)"
        except Exception as e:
            pid_status = f"ERROR ({e})"

    log_tail = ""
    target_log = log_file
    if target_log and os.path.isfile(target_log):
        try:
            with open(target_log, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                log_tail = "".join(lines[-50:])
        except Exception as ex:
            log_tail = f"Error reading log file: {ex}"
    elif pid is not None:
        log_dir = os.path.expanduser("~/.config/ai/logs")
        if os.path.isdir(log_dir):
            for fname in sorted(os.listdir(log_dir), reverse=True):
                if fname.endswith(".log"):
                    fpath = os.path.join(log_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read(4096)
                            if f"PID: {pid}" in content or fname.startswith("proc_"):
                                target_log = fpath
                                log_tail = content[-2000:]
                                break
                    except Exception:
                        pass

    health = "HEALTHY"
    if any(term in log_tail for term in ["Error", "Exception", "FAILED", "Traceback", "CRITICAL"]):
        health = "NEEDS ATTENTION (Errors detected in log output)"
    elif pid_status == "TERMINATED/EXITED":
        health = "FINISHED"

    out = [
        f"[Process Health Evaluation Report | PID: {pid if pid is not None else 'N/A'}]",
        f"State: {pid_status}",
        f"Health Verdict: {health}",
        f"Log File: `{target_log if target_log else 'None'}`"
    ]
    if log_tail:
        out.append(f"--- Log Tail ---\n{log_tail.strip()}")
    else:
        out.append("No log output recorded yet.")

    return "\n".join(out)

def stop_process(pid):
    """Terminate a running background process by PID."""
    import os, signal, time
    try:
        pid_int = int(pid)
        os.kill(pid_int, signal.SIGTERM)
        time.sleep(0.2)
        try:
            os.kill(pid_int, 0)
            os.kill(pid_int, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return f"Successfully stopped process PID {pid_int}."
    except ProcessLookupError:
        return f"Process PID {pid} is not running."
    except Exception as e:
        return f"Error stopping process PID {pid}: {e}"


def normalize_tool_arguments(tool_name, arguments):
    if isinstance(arguments, str):
        if tool_name in ("execute_command", "execute_remote_command"):
            return {"command": arguments}
        elif tool_name in ("read_file", "write_file", "edit_file", "list_directory"):
            return {"path": arguments}
        elif tool_name in ("web_search", "recall", "search_context", "arxiv_search"):
            return {"query": arguments}
        elif tool_name in ("fetch_webpage", "fetch_smart"):
            return {"url": arguments}
        elif tool_name == "think":
            return {"reasoning": arguments}
        elif tool_name == "task_complete":
            return {"summary": arguments}
        elif tool_name == "save_memory":
            return {"content": arguments}
        return arguments

    if not isinstance(arguments, dict):
        return arguments

    alias_map = {
        "execute_command": ("command", ["cmd", "command_line", "CommandLine", "args", "script", "code", "c"]),
        "execute_remote_command": ("command", ["cmd", "command_line", "CommandLine", "args", "script", "code", "c"]),
        "read_file": ("path", ["file", "filepath", "filename", "file_path", "p"]),
        "write_file": ("path", ["file", "filepath", "filename", "file_path", "p"]),
        "edit_file": ("path", ["file", "filepath", "filename", "file_path", "p"]),
        "list_directory": ("path", ["dir", "directory", "folder", "path_name", "p"]),
        "web_search": ("query", ["q", "search", "prompt", "term", "keywords"]),
        "arxiv_search": ("query", ["q", "search", "prompt", "term", "keywords"]),
        "fetch_webpage": ("url", ["uri", "link", "address", "u"]),
        "fetch_smart": ("url", ["uri", "link", "address", "u"]),
        "think": ("reasoning", ["thought", "thoughts", "plan", "reason"]),
        "task_complete": ("summary", ["text", "response", "result", "message", "final_answer", "answer"]),
        "save_memory": ("content", ["memory", "text", "entry"])
    }

    if tool_name in alias_map:
        target, aliases = alias_map[tool_name]
        if target not in arguments or not arguments[target]:
            for alias in aliases:
                if alias in arguments and arguments[alias]:
                    val = arguments[alias]
                    if isinstance(val, list):
                        val = " ".join(str(x) for x in val)
                    arguments[target] = val
                    break

    if tool_name in ("execute_command", "execute_remote_command") and isinstance(arguments.get("command"), list):
        arguments["command"] = " ".join(str(x) for x in arguments["command"])

    return arguments


TOOL_REQUIRED_ARGS = {
    "execute_command": ["command"],
    "web_search":      ["query"],
    "arxiv_search":    ["query"],
    "fetch_webpage":   ["url"],
    "fetch_smart":     ["url"],
    "read_file":       ["path"],
    "write_file":      ["path", "content"],
    "edit_file":       ["path", "search_content", "replace_content"],
    "save_memory":     ["content"],
    "remember":        ["content"],
    "recall":          ["query"],
    "delegate_task":   ["tasks"],
    "parallel_fetch":  ["urls"],
    "think":           ["reasoning"],
    "task_complete":   ["summary"],
    "computer_control": ["action"],
    "learn_rule":             ["rule_text"],
    "reset_context":          [],
    "vault_write":            ["title", "content"],
    "vault_read":             ["title"],
    "vault_search":           ["query"],
    "vault_backlinks":        ["title"],
    "pubmed_search":          ["query"],
    "pubmed_research_round":  ["query"],
    "start_background_process": ["command"],
    "check_process_status": [],
    "stop_process": ["pid"],
    "schedule_task":   ["task_id", "prompt", "interval_seconds"],
    "unschedule_task": ["task_id"],
    "list_scheduled_tasks": [],
    "get_system_status": [],
    "get_clipboard": [],
    "list_processes":   [],
    "remote_exec":      ["action"],
}

# Per-agent and per-URL output caps for parallel tools
_AGENT_OUTPUT_CAP = 10 * 1024       # 10 KB per sub-agent result
_PARALLEL_FETCH_CAP = 10 * 1024     # 10 KB per fetched URL

def _resolve_ai_bin():
    """Return path to the ai binary, checking environment override first."""
    candidates = [
        os.environ.get("INFER_BIN_PATH", ""),
        os.path.expanduser("~/.local/bin/ai"),
        "/usr/local/bin/ai",
        "./ai",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return "./ai"

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
    if not s:
        return "{}"
    s = s.strip()

    # Remove markdown code block markers if model wrapped JSON in ```json ... ```
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()

    # Repair raw unescaped newlines/tabs inside JSON string literals
    out = []
    in_string = False
    escape = False
    for c in s:
        if in_string:
            if escape:
                out.append(c)
                escape = False
            elif c == '\\':
                out.append(c)
                escape = True
            elif c == '"':
                out.append(c)
                in_string = False
            elif c == '\n':
                out.append('\\n')
            elif c == '\r':
                out.append('\\r')
            elif c == '\t':
                out.append('\\t')
            else:
                out.append(c)
        else:
            if c == '"':
                in_string = True
            out.append(c)

    if in_string:
        out.append('"')

    fixed_str = "".join(out)

    # Remove trailing commas before } or ]
    fixed_str = re.sub(r',\s*([}\]])', r'\1', fixed_str)

    # Balance unclosed braces/brackets
    in_str = False
    esc = False
    stack = []
    for c in fixed_str:
        if esc:
            esc = False
        elif c == '\\':
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c == '{' or c == '[':
                stack.append('}' if c == '{' else ']')
            elif c == '}' or c == ']':
                if stack and stack[-1] == c:
                    stack.pop()
    while stack:
        fixed_str += stack.pop()

    return fixed_str

def schedule_task(task_id, prompt, interval_seconds, run_once=False, extra=None):
    """Schedule a recurring or one-shot background task.

    Extra optional fields (pass via extra={...} or set in extra param):
      max_runs  (int)   — auto-cancel after this many agent invocations (0 = unlimited).
      ttl_hours (float) — auto-cancel this many hours after creation (0 = unlimited).
    """
    import json
    import os
    import subprocess
    import sys
    import time

    safe_task_id = "".join(c for c in task_id if c.isalnum() or c in ("_", "-"))
    if not safe_task_id:
        return "Error: task_id must contain alphanumeric characters, hyphens, or underscores."

    task_dir = os.path.expanduser("~/.config/ai/scheduled_tasks")
    os.makedirs(task_dir, exist_ok=True)

    task_file = os.path.join(task_dir, f"{safe_task_id}.json")
    pid_file  = os.path.join(task_dir, f"{safe_task_id}.pid")

    saved_env = {}
    for k, v in os.environ.items():
        if k.startswith("INFER_") or k.startswith("ZULIP_") or k.startswith("AI_REMINDER_") or k == "PATH":
            saved_env[k] = v

    existed = os.path.exists(task_file)

    # --- Build or update task data ---
    if existed:
        try:
            with open(task_file) as f:
                task_data = json.load(f)
        except Exception:
            task_data = {}
        # Preserve creation time and run counters on update
        task_data["prompt"]           = prompt
        task_data["interval_seconds"] = max(10, int(interval_seconds))
        task_data["run_once"]         = bool(run_once)
        task_data["env"]              = saved_env
    else:
        task_data = {
            "task_id":          safe_task_id,
            "prompt":           prompt,
            "interval_seconds": max(10, int(interval_seconds)),
            "run_once":         bool(run_once),
            "created_at":       time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_run":         "never",
            "run_count":        0,
            "fail_count":       0,
            "consec_failures":  0,
            "env":              saved_env,
        }
    if extra:
        task_data.update(extra)
    # Ensure tracking fields exist on old tasks
    for field, default in [("run_count", 0), ("fail_count", 0),
                           ("consec_failures", 0), ("max_runs", 0), ("ttl_hours", 0)]:
        task_data.setdefault(field, default)

    try:
        with open(task_file, "w") as f:
            json.dump(task_data, f, indent=2)
    except Exception as e:
        return f"Error writing task file: {e}"

    if existed:
        # Check if a scheduler process is still alive for this task
        alive = False
        if os.path.exists(pid_file):
            try:
                pid = int(open(pid_file).read().strip())
                os.kill(pid, 0)   # signal 0 = existence check
                alive = True
            except (ProcessLookupError, PermissionError, ValueError):
                pass   # stale PID
        if alive:
            return (f"Updated scheduled task '{safe_task_id}' "
                    f"(interval={interval_seconds}s, scheduler already running).")
        # Scheduler died without cleaning up — respawn it
        try:
            os.remove(pid_file)
        except Exception:
            pass

    # Spawn a new scheduler daemon
    try:
        cmd = [sys.executable, __file__, "run-scheduler", safe_task_id]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True
        )
        if existed:
            return (f"Respawned scheduler for dead task '{safe_task_id}' "
                    f"(running every {interval_seconds}s in the background).")
        return (f"Successfully scheduled task '{safe_task_id}' "
                f"(running every {interval_seconds}s in the background).")
    except Exception as e:
        try:
            os.remove(task_file)
        except Exception:
            pass
        return f"Error spawning scheduler process: {e}"

def set_reminder(message, when=None, delay_seconds=None, zulip_to=None,
                 zulip_stream=None, zulip_topic=None, task_id=None):
    """Schedule a one-shot reminder delivered directly to Zulip (no LLM spawn).

    Provide either `when` (an ISO 8601 timestamp, e.g. '2026-07-05T09:00:00' —
    convert phrases like 'tomorrow 9am' to ISO yourself using the current time)
    or `delay_seconds` (relative). The reminder is sent to a Zulip DM (`zulip_to`
    email) or a stream (`zulip_stream` + `zulip_topic`). If no recipient is
    given, it falls back to the AI_REMINDER_ZULIP_TO env var (set by the Zulip
    bridge to the requester) so 'remind me ...' just works from chat."""
    import datetime
    import time as _time

    if not message or not str(message).strip():
        return "Error: reminder 'message' is required."

    # Resolve delay
    if delay_seconds is not None:
        try:
            delay = float(delay_seconds)
        except (TypeError, ValueError):
            return "Error: delay_seconds must be a number."
    elif when:
        try:
            when_dt = datetime.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        except ValueError:
            return (f"Error: could not parse when='{when}'. Use ISO 8601, e.g. "
                    "'2026-07-05T09:00:00'.")
        if when_dt.tzinfo is None:
            now = datetime.datetime.now()
        else:
            now = datetime.datetime.now(when_dt.tzinfo)
        delay = (when_dt - now).total_seconds()
    else:
        return "Error: provide either 'when' (ISO timestamp) or 'delay_seconds'."

    if delay < 5:
        return (f"Error: reminder time is in the past or too soon ({int(delay)}s). "
                "Pick a future time.")

    # Resolve recipient
    zulip_to = zulip_to or os.environ.get("AI_REMINDER_ZULIP_TO")
    if not zulip_to and not zulip_stream:
        return ("Error: no Zulip recipient. Provide zulip_to (email for a DM) or "
                "zulip_stream + zulip_topic. From the Zulip bridge this is filled "
                "in automatically.")
    if zulip_stream and not zulip_topic:
        zulip_topic = "Reminders"

    # Build a unique-ish task id
    if not task_id:
        task_id = "reminder_" + _time.strftime("%Y%m%d_%H%M%S")

    extra = {
        "kind": "reminder",
        "message": str(message),
        "zulip_to": zulip_to,
        "zulip_stream": zulip_stream,
        "zulip_topic": zulip_topic,
        "when": when,
    }
    label = f"[reminder] {str(message)[:60]}"
    result = schedule_task(task_id, label, delay, run_once=True, extra=extra)
    if result.startswith("Error"):
        return result

    # Friendly confirmation with the local delivery time
    fire_at = datetime.datetime.now() + datetime.timedelta(seconds=delay)
    dest = f"DM to {zulip_to}" if zulip_to else f"#{zulip_stream} > {zulip_topic}"
    mins = int(delay // 60)
    when_str = fire_at.strftime("%Y-%m-%d %H:%M")
    return (f"⏰ Reminder set for {when_str} (in ~{mins} min) → {dest}: "
            f"\"{str(message)[:80]}\" (task id: {task_id})")


def _deliver_reminder(task_data):
    """Send a scheduled reminder straight to Zulip. Returns a status string."""
    message = task_data.get("message", "")
    text = f"⏰ **Reminder:** {message}"
    zulip_to = task_data.get("zulip_to")
    zulip_stream = task_data.get("zulip_stream")
    zulip_topic = task_data.get("zulip_topic") or "Reminders"
    try:
        import zulip_mcp_server as zms
        if zulip_stream:
            args = {"message_type": "stream", "to": zulip_stream,
                    "topic": zulip_topic, "content": text}
        else:
            args = {"message_type": "private", "to": zulip_to, "content": text}
        return zms.do_send_message(args)
    except Exception as e:
        # Fallback: desktop notification so the reminder is not silently lost.
        try:
            import subprocess
            subprocess.run(["notify-send", "AI Reminder", message], timeout=10)
            return f"Zulip delivery failed ({e}); sent desktop notification instead."
        except Exception:
            return f"Error: could not deliver reminder: {e}"


def unschedule_task(task_id):
    import os
    safe_task_id = "".join(c for c in task_id if c.isalnum() or c in ("_", "-"))
    task_dir = os.path.expanduser("~/.config/ai/scheduled_tasks")
    task_file = os.path.join(task_dir, f"{safe_task_id}.json")
    
    if os.path.exists(task_file):
        try:
            os.remove(task_file)
            return f"Successfully unscheduled/cancelled task '{safe_task_id}'."
        except Exception as e:
            return f"Error removing task file: {e}"
    else:
        return f"Task '{safe_task_id}' not found or already unscheduled."

def list_scheduled_tasks():
    import os, json, time
    task_dir = os.path.expanduser("~/.config/ai/scheduled_tasks")
    if not os.path.exists(task_dir) or not os.path.isdir(task_dir):
        return "No scheduled tasks found."

    files = [f for f in os.listdir(task_dir) if f.endswith(".json")]
    if not files:
        return "No scheduled tasks found."

    lines = []
    now = time.time()
    for f in sorted(files):
        path = os.path.join(task_dir, f)
        pid_path = path.replace(".json", ".pid")
        try:
            with open(path) as tf:
                data = json.load(tf)
        except Exception:
            continue

        tid      = data.get("task_id", f[:-5])
        interval = data.get("interval_seconds", "?")
        created  = data.get("created_at", "?")
        last_run = data.get("last_run", "never")
        prompt   = data.get("prompt", "")
        run_count     = data.get("run_count", "?")
        fail_count    = data.get("fail_count", "?")
        consec_fail   = data.get("consec_failures", 0)
        max_runs      = data.get("max_runs", 0)
        ttl_hours     = data.get("ttl_hours", 0)

        # Check if scheduler process is alive
        scheduler_alive = False
        scheduler_pid   = None
        if os.path.exists(pid_path):
            try:
                pid = int(open(pid_path).read().strip())
                os.kill(pid, 0)
                scheduler_alive = True
                scheduler_pid   = pid
            except Exception:
                pass

        # TTL remaining
        ttl_str = ""
        if ttl_hours > 0 and created != "?":
            try:
                from datetime import datetime
                created_dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
                elapsed_h = (datetime.now() - created_dt).total_seconds() / 3600
                remaining_h = ttl_hours - elapsed_h
                ttl_str = f" | TTL: {remaining_h:.1f}h remaining" if remaining_h > 0 else " | TTL: EXPIRED"
            except Exception:
                pass

        status = (f"[PID {scheduler_pid} ✓ alive]" if scheduler_alive
                  else "[⚠ scheduler DEAD — will respawn on next schedule_task call]")
        lines.append(f"Task ID: {tid}  {status}")
        lines.append(f"  Interval:     {interval}s  |  Created: {created}  |  Last run: {last_run}")
        lines.append(f"  Runs: {run_count}  |  Failures: {fail_count}  |  Consec fails: {consec_fail}"
                     + (f"  |  max_runs: {max_runs}" if max_runs else "") + ttl_str)
        lines.append(f"  Prompt:       {prompt[:120]}")
        lines.append("-" * 60)

    return "\n".join(lines) if lines else "No scheduled tasks found."

def run_scheduler_loop(task_id):
    import time
    import json
    import os
    import signal
    import subprocess
    from datetime import datetime

    safe_task_id = "".join(c for c in task_id if c.isalnum() or c in ("_", "-"))
    if not safe_task_id:
        return

    task_dir  = os.path.expanduser("~/.config/ai/scheduled_tasks")
    task_file = os.path.join(task_dir, f"{safe_task_id}.json")
    pid_file  = os.path.join(task_dir, f"{safe_task_id}.pid")

    cache_dir = os.path.expanduser("~/.cache/ai")
    os.makedirs(cache_dir, exist_ok=True)
    log_file = os.path.join(cache_dir, "scheduler.log")

    def log_message(msg):
        try:
            t = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(log_file, "a") as lf:
                lf.write(f"[{t}] [Task: {safe_task_id}] {msg}\n")
        except Exception:
            pass

    # ── PID lock: prevent duplicate scheduler processes ─────────────────────
    my_pid = os.getpid()
    if os.path.exists(pid_file):
        try:
            old_pid = int(open(pid_file).read().strip())
            if old_pid != my_pid:
                try:
                    os.kill(old_pid, 0)   # check if alive
                    log_message(f"Another scheduler (PID {old_pid}) already running. Exiting.")
                    return   # defer to the older process
                except (ProcessLookupError, PermissionError):
                    log_message(f"Stale PID file (PID {old_pid} dead). Taking over.")
        except (ValueError, OSError):
            pass

    try:
        with open(pid_file, "w") as pf:
            pf.write(str(my_pid))
    except Exception as e:
        log_message(f"Warning: could not write PID file: {e}")

    def cleanup_pid():
        try:
            if os.path.exists(pid_file):
                stored = int(open(pid_file).read().strip())
                if stored == my_pid:
                    os.remove(pid_file)
        except Exception:
            pass

    def interruptible_sleep(seconds):
        """Sleep for `seconds`, checking every 5s if the task file is gone."""
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True   # continue
            time.sleep(min(remaining, 5.0))
            if not os.path.exists(task_file):
                return False  # cancelled

    log_message(f"Scheduler loop started (PID {my_pid}).")

    # ── Main loop ────────────────────────────────────────────────────────────
    while True:
        if not os.path.exists(task_file):
            log_message("Task file deleted. Exiting scheduler loop.")
            break

        try:
            with open(task_file) as f:
                task_data = json.load(f)
        except Exception as e:
            log_message(f"Error reading task file: {e}")
            time.sleep(1)
            continue

        interval        = int(task_data.get("interval_seconds", 300))
        prompt          = task_data.get("prompt", "")
        saved_env       = task_data.get("env", {})
        run_once        = task_data.get("run_once", False)
        max_runs        = int(task_data.get("max_runs", 0))     # 0 = unlimited
        ttl_hours       = float(task_data.get("ttl_hours", 0))  # 0 = unlimited
        run_count       = int(task_data.get("run_count", 0))
        consec_failures = int(task_data.get("consec_failures", 0))
        created_at      = task_data.get("created_at", "")
        MAX_CONSEC_FAIL = 3   # auto-retire after this many back-to-back failures

        # ── Guard: max_runs limit ────────────────────────────────────────────
        if max_runs > 0 and run_count >= max_runs:
            log_message(f"max_runs={max_runs} reached ({run_count} runs). Auto-cancelling task.")
            try:
                os.remove(task_file)
            except Exception:
                pass
            break

        # ── Guard: TTL expiry ────────────────────────────────────────────────
        if ttl_hours > 0 and created_at:
            try:
                created_dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                elapsed_h  = (datetime.now() - created_dt).total_seconds() / 3600
                if elapsed_h >= ttl_hours:
                    log_message(f"TTL expired ({elapsed_h:.1f}h >= {ttl_hours}h). Auto-cancelling task.")
                    try:
                        os.remove(task_file)
                    except Exception:
                        pass
                    break
            except Exception:
                pass

        # ── Guard: consecutive failure limit ─────────────────────────────────
        if consec_failures >= MAX_CONSEC_FAIL:
            log_message(f"consec_failures={consec_failures} >= {MAX_CONSEC_FAIL}. Auto-retiring task.")
            try:
                os.remove(task_file)
            except Exception:
                pass
            break

        # ── Interruptible sleep ──────────────────────────────────────────────
        if not interruptible_sleep(interval):
            log_message("Task file deleted during sleep. Exiting.")
            break

        # Re-read after sleep (may have been updated)
        if not os.path.exists(task_file):
            log_message("Task file gone after sleep. Exiting.")
            break
        try:
            with open(task_file) as f:
                task_data = json.load(f)
        except Exception:
            pass

        # ── run_once: delete before spawning so crash can't repeat ───────────
        if run_once:
            try:
                os.remove(task_file)
                log_message("run_once: task file removed before agent spawn.")
            except Exception as e:
                log_message(f"run_once: could not remove task file: {e}")

        # ── Reminder shortcut (direct Zulip, no LLM) ─────────────────────────
        if task_data.get("kind") == "reminder":
            _saved_env = dict(os.environ)
            try:
                os.environ.update({k: v for k, v in saved_env.items() if isinstance(v, str)})
                status = _deliver_reminder(task_data)
            finally:
                os.environ.clear()
                os.environ.update(_saved_env)
            log_message(f"Reminder delivered: {status}")
            if run_once or not os.path.exists(task_file):
                log_message("Reminder run_once: exiting.")
                break
            continue

        # ── Update run metadata ───────────────────────────────────────────────
        if not run_once and os.path.exists(task_file):
            try:
                task_data["last_run"]  = time.strftime("%Y-%m-%d %H:%M:%S")
                task_data["run_count"] = run_count + 1
                with open(task_file, "w") as f:
                    json.dump(task_data, f, indent=2)
            except Exception as e:
                log_message(f"Error updating task metadata: {e}")

        # ── Spawn agent ──────────────────────────────────────────────────────
        ai_bin   = _resolve_ai_bin()
        cmd      = [ai_bin, "-y", "-q", prompt]
        run_env  = os.environ.copy()
        run_env.update(saved_env)

        # Use INFER_TASK_TIMEOUT if set, else 15 min per sub-agent run.
        # This is distinct from the scheduler-level interval — it caps how long
        # a single agent invocation may run before we consider it stuck.
        try:
            agent_timeout = int(run_env.get("INFER_TASK_TIMEOUT", 0)) or 900
        except Exception:
            agent_timeout = 900

        success = False
        try:
            log_message(f"Spawning: {cmd}")
            proc = subprocess.run(
                cmd, env=run_env, capture_output=True, text=True,
                timeout=agent_timeout
            )
            rc = proc.returncode
            out_snippet = (proc.stdout or proc.stderr or "").strip()[:300]
            if rc == 0:
                log_message(f"Agent finished successfully. Output: {out_snippet}")
                success = True
            else:
                log_message(f"Agent failed (exit {rc}). Stderr: {out_snippet}")
        except subprocess.TimeoutExpired:
            log_message(f"Agent timed out after {agent_timeout}s — treating as failure.")
        except Exception as e:
            log_message(f"Error running agent: {e}")

        # ── Update failure counters ───────────────────────────────────────────
        if not run_once and os.path.exists(task_file):
            try:
                with open(task_file) as f:
                    task_data = json.load(f)
                if success:
                    task_data["consec_failures"] = 0
                else:
                    task_data["fail_count"]       = int(task_data.get("fail_count", 0)) + 1
                    task_data["consec_failures"]  = int(task_data.get("consec_failures", 0)) + 1
                    task_data["last_error"]        = time.strftime("%Y-%m-%d %H:%M:%S")
                with open(task_file, "w") as f:
                    json.dump(task_data, f, indent=2)
            except Exception as e:
                log_message(f"Error persisting failure counters: {e}")

        # ── Exit if task was cancelled during agent run ───────────────────────
        if run_once or not os.path.exists(task_file):
            log_message("Task file gone after agent run. Exiting scheduler loop.")
            break

    # ── Cleanup ───────────────────────────────────────────────────────────────
    cleanup_pid()
    log_message("Scheduler loop exited cleanly.")


# ── Continuous self-improvement: skill create/update/note ─────────────────────
# The agent persists what it learns into skills so future sessions inherit it.
# Skills are written to the project .agents/skills (checked into the repo) AND
# to ~/.config/ai/skills (global, persistent). Returns a machine-recognisable
# marker "[SKILL_CREATED:name]" / "[SKILL_UPDATED:name]" / "[SKILL:note:name]"
# that ai.c surfaces to the user as a notification.

def _skill_targets(skill_name):
    """Return [(path, base_dir)] write targets for a skill (each a <dir>/SKILL.md),
    project first so it's checked into the repo."""
    san = skill_name.replace("/", "_").replace("\\", "_").replace("..", "_").strip()
    if not san:
        san = "learned"
    targets = []
    proj = os.path.join(os.getcwd(), ".agents", "skills", san, "SKILL.md")
    glob = os.path.join(os.path.expanduser("~"), ".config", "ai", "skills", san, "SKILL.md")
    if proj != glob:
        targets.append((proj, "project"))
    targets.append((glob, "global"))
    return targets, san


def _learning_log_path():
    return os.path.join(os.path.expanduser("~"), ".config", "ai", "skills_learning_log.md")


def _append_learning_log(entry):
    try:
        path = _learning_log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n## {ts}\n{entry}\n")
    except Exception as e:
        return f"(log write failed: {e})"
    return ""


def skill_create(name, description, content):
    """Create (or overwrite) a skill. Returns a [SKILL_CREATED:name] marker."""
    targets, san = _skill_targets(name)
    if not description:
        description = "Learned during an ai session; auto-generated skill."
    body = (
        "---\n"
        f"name: {san}\n"
        f"description: {description}\n"
        "---\n\n"
        f"# {san}\n\n"
        f"{content}\n"
    )
    wrote = []
    for path, _where in targets:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            wrote.append(path)
        except Exception as e:
            return f"Error writing skill {name}: {e}"
    _append_learning_log(f"Created skill **{san}** (wrote to {len(wrote)} location(s)).\n\n```\n{description}\n```\n\n{content[:1000]}")
    joined = ", ".join(wrote)
    return f"[SKILL_CREATED:{san}]\nSkill '{san}' created/updated.\nSaved to: {joined}\n\nDescription: {description}"


def skill_update(name, note):
    """Update an existing skill with a note (good-to-know / discrepancy fix).
    Appends a 'Recent learning' section; returns a [SKILL_UPDATED:name] marker."""
    targets, san = _skill_targets(name)
    updated = 0
    for path, _where in targets:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            if "## Recent learning" not in content:
                content += "\n\n## Recent learning\n"
            content += f"\n- [{ts}] {note}\n"
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            updated += 1
        except Exception as e:
            return f"Error updating skill {name}: {e}"
    if updated == 0:
        # Skill doesn't exist yet anywhere; create it as a note.
        return skill_create(name, f"Auto-generated from learning note: {note[:140]}", note)
    _append_learning_log(f"Updated skill **{san}** ({updated} location(s)): {note}")
    return f"[SKILL_UPDATED:{san}]\nSkill '{san}' updated in {updated} location(s).\nRecorded learning: {note}"


def skill_note(name, note):
    """Attach a standalone learning note to a skill's learning log without touching its body."""
    _append_learning_log(f"Note for skill **{name or 'general'}**: {note}")
    return f"[SKILL:note:{name or 'general'}]\nNoted: {note}\n(Stored in {_learning_log_path()})"


def list_skills_dir():
    """Return human-readable index of all skills in project + global skill dirs."""
    bases = [
        os.path.join(os.getcwd(), ".agents", "skills"),
        os.path.join(os.path.expanduser("~"), ".config", "ai", "skills"),
    ]
    seen = set()
    lines = []
    for base in bases:
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            if entry in seen:
                continue
            if os.path.isfile(os.path.join(base, entry, "SKILL.md")):
                lines.append(entry)
                seen.add(entry)
    return "Available skills:\n" + ("\n".join(lines) if lines else "(none yet — use skill_create to save what you learn)")


# ── Automatic failure-learning ledger ────────────────────────────────────────
# The C harness records tool failures deterministically, without relying on the
# model choosing to persist anything. This solves "the agent makes the same
# mistake twice and doesn't remember making it." Every failure goes to a JSONL
# ledger; recurring signatures auto-promote to a lessons.md entry; when a tool
# that previously failed later succeeds, the working approach is auto-learned
# as a FIX lesson. On a future error, `lessons_for` returns the stored lessons
# so the harness can surface the memory right where the model needs it.
import re as _re

def _si_dir():
    d = os.path.join(os.path.expanduser("~"), ".config", "ai", "self_improve")
    os.makedirs(d, exist_ok=True)
    return d

def _ledger_path():
    return os.path.join(_si_dir(), "ledger.jsonl")

def _lessons_path():
    return os.path.join(_si_dir(), "lessons.md")

def _si_recurrence_threshold():
    raw = os.environ.get("INFER_SELF_IMPROVE_RECURRENCE", "2")
    try:
        return max(1, int(raw))
    except Exception:
        return 2

def _err_signature(tool, error):
    """Normalise an error into a stable dedupe key: tool + stripped/normalised
    error tokens (numbers collapsed) so the SAME mistake is recognised across
    sessions even when paths/ids differ."""
    s = ("%s %s" % ((tool or ""), (error or ""))).lower()
    s = _re.sub(r"\d+", "#", s)
    s = _re.sub(r"[^a-z0-9#%]+", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s

def _ledger_signature_count(signature):
    n = 0
    if os.path.isfile(_ledger_path()):
        try:
            with open(_ledger_path(), encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("signature") == signature:
                        n += 1
        except Exception:
            pass
    return n

def _block_tool(block):
    for line in block.splitlines():
        if line.lower().startswith("tool:"):
            return line.split(":", 1)[1].strip().lower()
    return ""

def _has_lesson(kind, tool, signature):
    if not os.path.isfile(_lessons_path()):
        return False
    try:
        with open(_lessons_path(), encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return False
    needle_prefix = "\n## %s " % (kind or "")
    if needle_prefix not in "\n" + content:
        return False
    return (signature and signature in content) or (
        tool and ("\ntool: %s\n" % tool.lower() in "\n" + content.lower()))

def _append_lesson(kind, tool, signature, body):
    path = _lessons_path()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n## %s %s\ntool: %s\nsignature: %s\n> %s\n"
                % (kind, ts, (tool or ""), (signature or ""), body))

def record_failure(tool="", args="", error="", phase="execution"):
    """Record a failed tool call in the ledger.

    Returns (recurring_bool, lesson_text). If the SAME failure signature has
    now been seen >= INFER_SELF_IMPROVE_RECURRENCE times and isn't already
    captured, an auto-generated recurring-pitfall lesson is persisted and
    returned so the harness can surface it immediately."""
    tool = (tool or "").strip()
    error = (error or "").strip()
    sig = _err_signature(tool, error)
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "kind": "failure", "tool": tool,
           "args": (args or "")[:200], "error": error[:300],
           "signature": sig, "phase": phase}
    try:
        with open(_ledger_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        return False, "Error writing failure ledger: %s" % e
    n = _ledger_signature_count(sig)
    thresh = _si_recurrence_threshold()
    if n >= thresh and not _has_lesson("PITFALL", tool, sig):
        body = ("Recurring pitfall (%dx and counting): `%s` failed with:\n"
                "> %s\n"
                "Before retrying the identical call, look up the '## FIX' "
                "lessons for this tool and change your approach."
                % (n, tool or "?", (error or "?")[:160]))
        _append_lesson("PITFALL", tool, sig, body)
        return True, "[RECURRING FAILURE] " + body
    return False, ""

def record_recovery(tool="", args="", prior_error="", phase="execution"):
    """Record that the model recovered from a prior failure of the SAME tool.

    This is harness-driven (no model discipline required): the C loop notices a
    failed call to a tool, then later a succeeding call to the same tool, and
    persists the working approach as a FIX lesson. Returns the lesson text."""
    tool = (tool or "").strip()
    prior_error = (prior_error or "").strip()
    args = (args or "").strip()
    sig = _err_signature(tool, prior_error)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(_ledger_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": ts, "kind": "recovery", "tool": tool,
                                "approach": args[:200],
                                "prior_error": prior_error[:300],
                                "signature": sig}) + "\n")
    except Exception as e:
        return "Error writing recovery ledger: %s" % e
    lesson_text = ("`%s` failed (%s) then succeeded with approach: `%s`."
                   % (tool or "?", (prior_error or "?")[:160], args[:200]))
    if not _has_lesson("FIX", tool, sig):
        _append_lesson("FIX", tool, sig, lesson_text)
    return lesson_text

def lessons_for(tool="", error=""):
    """Return persisted lessons relevant to a tool name / error signature.

    Matches lessons whose declared tool equals the given tool, or whose error
    signature overlaps the current error. Returns '' when nothing matches, else
    a compact markdown block (capped ~1600 chars) for injection."""
    if not os.path.isfile(_lessons_path()):
        return ""
    try:
        with open(_lessons_path(), encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return ""
    target_tool = (tool or "").strip().lower()
    sig = _err_signature(tool, error)
    blocks, cur = [], []
    for line in content.splitlines():
        if line.startswith("## "):
            if cur:
                blocks.append("\n".join(cur))
                cur = []
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    hits, seen = [], set()
    for b in blocks:
        if not b.strip():
            continue
        key = b.splitlines()[0]
        if key in seen:
            continue
        tool_match = target_tool and _block_tool(b) == target_tool
        sig_match = sig and sig in b.lower()
        if tool_match or sig_match:
            seen.add(key)
            hits.append(b)
    if not hits:
        return ""
    joined = "\n".join(hits)
    if len(joined) > 1600:
        joined = joined[:1600] + "\n... (truncated)"
    return joined

def self_improve_status():
    """Human-readable summary of the self-improvement ledger/lessons (debugging)."""
    nfail = nrec = 0
    if os.path.isfile(_ledger_path()):
        try:
            with open(_ledger_path(), encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("kind") == "failure":
                        nfail += 1
                    elif rec.get("kind") == "recovery":
                        nrec += 1
        except Exception:
            pass
    lessons = ""
    if os.path.isfile(_lessons_path()):
        try:
            with open(_lessons_path(), encoding="utf-8", errors="replace") as f:
                lessons = f.read()
        except Exception:
            lessons = ""
    lines = ["Self-improvement state (%s):" % _si_dir(),
             "- failures recorded : %d" % nfail,
             "- recoveries recorded: %d" % nrec,
             "- lessons.md         : %s" % _lessons_path(),
             "- recurrence threshold: %d (INFER_SELF_IMPROVE_RECURRENCE)" % _si_recurrence_threshold()]
    if lessons.strip():
        lines.append("--- lessons.md ---")
        lines.append(lessons.strip())
    return "\n".join(lines)
def main():
    # A pipe-writing backend must not raise a BrokenPipeError traceback when the
    # parent (ai.c) closes the pipe on interrupt/exit. Restore default SIGPIPE so
    # the process dies silently instead of dumping "Exception ignored on flushing
    # sys.stdout: BrokenPipeError" at interpreter shutdown.
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except Exception:
        pass
    if len(sys.argv) < 2:
        print("Usage: ai_mcp.py [list-tools | call-tool | render-markdown | trim-messages]", file=sys.stderr)
        sys.exit(1)

    action = sys.argv[1]
    mcp_servers = load_config()

    if action == "run-scheduler":
        if len(sys.argv) < 3:
            sys.exit(1)
        task_id = sys.argv[2]
        run_scheduler_loop(task_id)
        sys.exit(0)

    if action == "render-markdown":
        if len(sys.argv) < 3:
            sys.exit(0)
        text = sys.argv[2]
        if os.environ.get("INFER_RAW_OUTPUT") == "1":
            print(text)
        else:
            print(render_markdown(text))
        sys.exit(0)

    if action == "rag-memories":
        if len(sys.argv) < 3:
            sys.exit(0)
        query = sys.argv[2]
        try:
            res = recall(query)
            if "No memories found" not in res and not res.startswith("Error"):
                print(res)
        except:
            pass
        sys.exit(0)

    if action == "save-memories":
        if len(sys.argv) < 3:
            sys.exit(0)
        content = sys.argv[2]
        try:
            memories = json.loads(content)
            if isinstance(memories, list):
                for mem in memories:
                    if isinstance(mem, str):
                        remember(mem, "Auto-compacted memory map")
        except:
            # Fallback if not proper JSON
            remember(content, "Auto-compacted memory map")
        sys.exit(0)

    # ── Automatic failure-learning ledger (harness-internal; not exposed as model tools) ──
    if action == "record-failure":
        rec = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        _, lesson = record_failure(rec.get("tool", ""), rec.get("args", ""),
                                   rec.get("error", ""), rec.get("phase", "execution"))
        if lesson:
            print(lesson)
        sys.exit(0)

    if action == "record-recovery":
        rec = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        lesson = record_recovery(rec.get("tool", ""), rec.get("args", ""),
                                 rec.get("prior_error", ""), rec.get("phase", "execution"))
        if lesson:
            print(lesson)
        sys.exit(0)

    if action == "lessons-for":
        rec = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        print(lessons_for(rec.get("tool", ""), rec.get("error", "")), end="")
        sys.exit(0)

    if action == "self-improve-status":
        print(self_improve_status())
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

    if action == "session-transcript":
        # Convert a saved raw messages array (from a previous run) into a clean
        # user/assistant transcript suitable for resuming a conversation. Emits
        # one compact JSON message object per line (JSONL) so the C caller can
        # append each via its existing append_message(). Dropping the old system
        # message, intermediate tool churn, and dangling tool_calls avoids the
        # "assistant tool_call without tool response" API errors on the next turn.
        if len(sys.argv) < 3:
            sys.exit(0)
        try:
            with open(sys.argv[2]) as f:
                messages = json.load(f)
        except Exception:
            sys.exit(0)

        def _text_of(content):
            if isinstance(content, str):
                return content
            if isinstance(content, list):  # multimodal: keep text parts only
                parts = [p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text"]
                return "\n".join(t for t in parts if t)
            return ""

        # User-role messages that ai.c injects internally to steer a stalling
        # model — never real user turns, so drop them from a resumed transcript.
        _INTERNAL_NUDGES = (
            "Please call task_complete",
            "Your last response was empty",
            "[TIMEOUT]",
        )

        out = []
        for msg in messages:
            role = msg.get("role")
            if role == "user":
                txt = _text_of(msg.get("content"))
                if txt.strip() and not any(txt.lstrip().startswith(n) for n in _INTERNAL_NUDGES):
                    out.append({"role": "user", "content": txt})
            elif role == "assistant":
                # Prefer a task_complete summary (the model's real final answer),
                # otherwise any assistant text. Ignore other tool_calls.
                summary = None
                for call in (msg.get("tool_calls") or []):
                    fn = call.get("function", {})
                    if fn.get("name") == "task_complete":
                        try:
                            summary = json.loads(fn.get("arguments", "{}")).get("summary")
                        except Exception:
                            summary = None
                txt = summary or _text_of(msg.get("content"))
                if txt and txt.strip():
                    out.append({"role": "assistant", "content": txt})
            # system / tool roles are dropped

        # Collapse consecutive same-role messages defensively and cap length so a
        # huge prior run cannot blow up the next request's context.
        MAX_TRANSCRIPT = 40000
        total = 0
        emitted = []
        for m in out:
            total += len(m["content"])
            if total > MAX_TRANSCRIPT:
                break
            emitted.append(m)
        # Keep the most recent turns if we had to truncate from the front
        if len(emitted) < len(out):
            emitted = out[-len(emitted):] if emitted else out[-1:]
        for m in emitted:
            print(json.dumps(m, ensure_ascii=False))
        sys.exit(0)

    if action == "list-tools":
        openai_tools = []

        # 1. think — first so small models see it first
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "think",
                "description": "Plan, reflect, or analyze before taking action. Use this tool before complex steps, to verify your previous actions, or to correct course. Highly recommended for multi-step tasks. Keep reasoning concise.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reasoning": {
                            "type": "string",
                            "description": "Brief plan or reflection (≤100 words): what you are analyzing and what steps you will take next."
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
                "description": "Run a shell command on the host system and return its stdout and stderr. Use for any system task, file inspection, or quick scripts. IMPORTANT: For long-running tasks (e.g. training, servers, heavy builds), DO NOT use this tool as it blocks the main thread and locks the GUI. Instead, use `start_background_process` to detach the job so you can monitor or stop it if needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact shell command to execute."
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Optional timeout in seconds. Defaults to 120s. For tasks requiring more than a few minutes, use `start_background_process` instead."
                        }
                    },
                    "required": ["command"]
                }
            }
        })

        openai_tools.append({
            "type": "function",
            "function": {
                "name": "execute_remote_command",
                "description": "Run a command on a remote host via SSH. You must have passwordless SSH access (e.g. key-based) already configured for the host.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {
                            "type": "string",
                            "description": "The SSH host (e.g., user@hostname or just hostname)."
                        },
                        "command": {
                            "type": "string",
                            "description": "The bash command to run remotely."
                        }
                    },
                    "required": ["host", "command"]
                }
            }
        })

        openai_tools.append({
            "type": "function",
            "function": {
                "name": "remote_exec",
                "description": "Remote Server Control Tool. Connect, discover, execute commands, monitor resources, submit jobs on a remote server cluster. Supports connection lifecycle management, auto-discovery of compute nodes, HPC job submission (Slurm/PBS/Torque), and resource monitoring.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "Action to perform: connect, disconnect, discover, status, exec, mount, jobs, submit",
                            "enum": ["connect", "disconnect", "discover", "status", "exec", "mount", "jobs", "submit"]
                        },
                        "host": {
                            "type": "string",
                            "description": "Remote server hostname or IP (required for connect)"
                        },
                        "port": {
                            "type": "integer",
                            "description": "SSH port (default: 22)"
                        },
                        "user": {
                            "type": "string",
                            "description": "SSH username (required for connect)"
                        },
                        "pass": {
                            "type": "string",
                            "description": "SSH password (optional - prefer key-based auth)"
                        },
                        "command": {
                            "type": "string",
                            "description": "Command to execute (required for exec, submit)"
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Command timeout in seconds (default: 120)"
                        }
                    },
                    "required": ["action"]
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

        # 3b. arxiv_search
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "arxiv_search",
                "description": "Search the arXiv API for scientific papers. Returns metadata and abstracts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query. e.g. 'electron' or 'au:smith'"
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max number of results to return (default 5, max 10)."
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
                "description": "Download and read any URL. This is the robust default: it uses a browser TLS fingerprint (curl_cffi) to get past Cloudflare and bot-walls, and auto-escalates to a headless browser for JS-rendered pages when needed. Use it for essentially every page. Required before task_complete if search returned URLs — never present links without reading them.",
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

        # 10. remember — save to FTS5 memory database
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "remember",
                "description": "Save key facts, user preferences, or context to long-term persistent memory using SQLite FTS5 full-text search. Memories can be later retrieved via the 'recall' tool.",
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

        # 10b. recall — search memories
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "recall",
                "description": "Search long-term memories using natural language full-text search (SQLite FTS5). Returns matching saved memories.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query — can be a word, phrase, or FTS5 boolean expression."
                        }
                    },
                    "required": ["query"]
                }
            }
        })

        # 10c. list_processes — top 10 running processes
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "list_processes",
                "description": "Return the top 10 running processes sorted by CPU usage.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        })

        # 10d. start_background_process
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "start_background_process",
                "description": "Start a command in the background, writing stdout/stderr output to a log file for status & health monitoring.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The command line string to run asynchronously in background."
                        },
                        "log_file": {
                            "type": "string",
                            "description": "Optional log file path. Defaults to ~/.config/ai/logs/proc_<timestamp>.log if omitted."
                        }
                    },
                    "required": ["command"]
                }
            }
        })

        # 10e. check_process_status
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "check_process_status",
                "description": "Inspect a background process state, read recent log output, evaluate health status, and receive a decision summary.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pid": {
                            "type": "integer",
                            "description": "PID of the background process to inspect."
                        },
                        "log_file": {
                            "type": "string",
                            "description": "Path to the log file to inspect."
                        }
                    },
                    "required": []
                }
            }
        })

        # 10f. stop_process
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "stop_process",
                "description": "Terminate a running background process by PID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pid": {
                            "type": "integer",
                            "description": "PID of the process to terminate."
                        }
                    },
                    "required": ["pid"]
                }
            }
        })

        # 11. delegate_task
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

        # 13. fetch_webpage_js — Playwright-based for JS-protected sites
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

        # 14. get_system_status — quick CPU/RAM/disk snapshot
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "get_system_status",
                "description": "Return a compact one-line summary of CPU usage, RAM, and disk. Useful for quickly checking system resources before running heavy operations.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        })

        # 15. get_clipboard — read X11/Wayland clipboard
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "get_clipboard",
                "description": "Read the current system clipboard content (X11 via xclip or Wayland via wl-paste). Returns the plain text currently on the clipboard, or an error if nothing is available.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        })

        # Continuous Local Learning
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "learn_rule",
                "description": "Save a permanent system rule or constraint to your dynamic rules file. These rules are injected into your system prompt on all future runs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rule_text": {
                            "type": "string",
                            "description": "The exact text of the rule to learn."
                        }
                    },
                    "required": ["rule_text"]
                }
            }
        })
        # Scheduled Context Resets
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "reset_context",
                "description": "Clear your conversational history to prevent context window bloat. Use this after completing a sub-task or when transitioning to a new topic to regain full reasoning capacity.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        })
        # Graph Vault Tools
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "vault_write",
                "description": "Create or update a Markdown note in the Obsidian Graph Vault. Used to store long-term structured knowledge, agentic state, and plans.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "The title of the note (will become the filename)."},
                        "content": {"type": "string", "description": "The Markdown content of the note."},
                        "links": {"type": "string", "description": "Optional comma-separated list of other note titles to link to (e.g. 'Project Alpha, Database Schema')."}
                    },
                    "required": ["title", "content"]
                }
            }
        })
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "vault_read",
                "description": "Read the full Markdown content of a specific note from the Graph Vault.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"}
                    },
                    "required": ["title"]
                }
            }
        })
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "vault_search",
                "description": "Perform a full-text FTS5 search across all notes in the Graph Vault.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"}
                    },
                    "required": ["query"]
                }
            }
        })
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "vault_backlinks",
                "description": "Traverse the Knowledge Graph by finding all notes that link to (mention) a specific note title via [[WikiLinks]].",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"}
                    },
                    "required": ["title"]
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

        # Google Calendar tools
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "gcal_list_events",
                "description": "List events from Google Calendar in a specified time range.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "time_min": {
                            "type": "string",
                            "description": "Start time in ISO 8601 format (e.g. '2026-07-01T00:00:00-06:00' or '2026-07-01T00:00:00Z'). Defaults to current time."
                        },
                        "time_max": {
                            "type": "string",
                            "description": "End time in ISO 8601 format. Defaults to 7 days from start time."
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of events to return. Defaults to 20.",
                            "default": 20
                        },
                        "calendar_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of calendar IDs to search. Omit or set to ['all'] to search all of your selected Google calendars."
                        }
                    }
                }
            }
        })

        openai_tools.append({
            "type": "function",
            "function": {
                "name": "gcal_create_event",
                "description": "Create a new event in Google Calendar with exact start/end times. For natural-language phrases ('lunch with Sam tomorrow 1pm'), prefer gcal_quick_add instead.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Title/summary of the calendar event."
                        },
                        "start_time": {
                            "type": "string",
                            "description": "Start time, ISO 8601 (e.g. '2026-07-01T14:00:00'). A timezone offset is optional — without one it is interpreted in the user's local timezone (or the time_zone arg). Use a bare date ('2026-07-01') for an all-day event."
                        },
                        "end_time": {
                            "type": "string",
                            "description": "End time, ISO 8601 (e.g. '2026-07-01T15:00:00'). Offset optional. Use a bare date for all-day events."
                        },
                        "time_zone": {
                            "type": "string",
                            "description": "Optional IANA timezone name (e.g. 'America/New_York') for naive start/end times. Defaults to the system local timezone."
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional detailed description of the event."
                        },
                        "location": {
                            "type": "string",
                            "description": "Optional location of the event."
                        },
                        "attendees": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of attendee emails."
                        },
                        "calendar_id": {
                            "type": "string",
                            "description": "Optional ID of the calendar to create the event in. Defaults to 'primary' (your main calendar)."
                        }
                    },
                    "required": ["summary", "start_time", "end_time"]
                }
            }
        })

        openai_tools.append({
            "type": "function",
            "function": {
                "name": "gcal_check_availability",
                "description": "Check free/busy time slots for Google Calendar to query availability.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "time_min": {
                            "type": "string",
                            "description": "Start time in ISO 8601 format (e.g. '2026-07-01T08:00:00-06:00')."
                        },
                        "time_max": {
                            "type": "string",
                            "description": "End time in ISO 8601 format (e.g. '2026-07-01T17:00:00-06:00')."
                        },
                        "calendar_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of calendar IDs to check. Defaults to ['primary']."
                        }
                    },
                    "required": ["time_min", "time_max"]
                }
            }
        })

        openai_tools.append({
            "type": "function",
            "function": {
                "name": "gcal_quick_add",
                "description": "Create a Google Calendar event from a natural-language phrase — Google parses the date/time/title for you. Best default for casual event creation, e.g. 'Dentist appointment next Tuesday at 3pm' or 'Lunch with Sam tomorrow 12-1pm'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Natural-language description including when and what, e.g. 'Team sync Friday 10am'."
                        },
                        "calendar_id": {
                            "type": "string",
                            "description": "Optional calendar ID. Defaults to 'primary'."
                        }
                    },
                    "required": ["text"]
                }
            }
        })

        openai_tools.append({
            "type": "function",
            "function": {
                "name": "gcal_update_event",
                "description": "Reschedule or modify an existing Google Calendar event. Only the fields you pass are changed. Call gcal_list_events first to get the event_id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string", "description": "ID of the event to update (from gcal_list_events)."},
                        "summary": {"type": "string", "description": "New title (optional)."},
                        "start_time": {"type": "string", "description": "New start time, ISO 8601 (optional). Offset optional; naive times use time_zone or local tz."},
                        "end_time": {"type": "string", "description": "New end time, ISO 8601 (optional)."},
                        "description": {"type": "string", "description": "New description (optional)."},
                        "location": {"type": "string", "description": "New location (optional)."},
                        "attendees": {"type": "array", "items": {"type": "string"}, "description": "Replacement attendee email list (optional)."},
                        "time_zone": {"type": "string", "description": "Optional IANA timezone for naive start/end times."},
                        "calendar_id": {"type": "string", "description": "Optional calendar ID. Defaults to 'primary'."}
                    },
                    "required": ["event_id"]
                }
            }
        })

        openai_tools.append({
            "type": "function",
            "function": {
                "name": "gcal_delete_event",
                "description": "Cancel/delete a Google Calendar event by its ID. Call gcal_list_events first to get the event_id. This is irreversible.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string", "description": "ID of the event to delete (from gcal_list_events)."},
                        "calendar_id": {"type": "string", "description": "Optional calendar ID. Defaults to 'primary'."}
                    },
                    "required": ["event_id"]
                }
            }
        })

        # 11b. check_time
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "check_time",
                "description": "Returns the current local date and time.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        })

        # schedule_task
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "schedule_task",
                "description": (
                    "MANDATORY replacement for `sleep` delays. NEVER use execute_command with sleep for timed work — "
                    "it blocks the terminal and the Zulip bridge, hanging the session with no response. "
                    "Use this tool instead for ANY deferred, timed, or periodic work: desktop notifications, "
                    "reminders, polling folders, checking job status, sending messages when a condition is met. "
                    "This returns IMMEDIATELY — the task runs in a fully detached background process. "
                    "IMPORTANT: Every polling task MUST have an explicit termination condition. "
                    "Always set max_runs (e.g. 20) OR ttl_hours (e.g. 4.0) as a safety guardrail in case the "
                    "sub-agent never calls unschedule_task — this prevents orphaned background tasks. "
                    "For ONE-SHOT tasks: use run_once=True (guaranteed single execution, no agent needed) "
                    "or end the prompt with 'then call unschedule_task(task_id)'. "
                    "For POLLING tasks: include the unschedule_task call in the agent prompt AND set max_runs. "
                    "After calling this, immediately call task_complete to confirm the schedule to the user."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "A unique snake_case identifier, e.g. 'notify_go_home' or 'check_binders_done'."
                        },
                        "prompt": {
                            "type": "string",
                            "description": (
                                "Full self-contained instruction for the background agent. Must include everything it needs. "
                                "For polling: always end with 'If done, call unschedule_task(task_id). Else do nothing.' "
                                "Example one-shot: 'Run: execute_command(\"notify-send \\\"Go home!\\\" \\\"Time to leave 🏠\\\"\"); "
                                "then call unschedule_task(notify_go_home).' "
                                "Example polling: 'Count files in /data/results. If count >= 10000: send Zulip private message "
                                "to user@example.com saying how many passed filters; then call unschedule_task(check_binders). Else do nothing.'"
                            )
                        },
                        "interval_seconds": {
                            "type": "integer",
                            "description": (
                                "How often to run (minimum 10s). "
                                "For one-shot reminders, set this to the full delay (e.g. 300 for 5 minutes). "
                                "For polling, set to a shorter check interval (e.g. 120 for every 2 min)."
                            )
                        },
                        "run_once": {
                            "type": "boolean",
                            "description": (
                                "If true, the task is guaranteed to run exactly once after interval_seconds and then stop — "
                                "no matter what the sub-agent does. Use this for simple one-shot reminders/notifications "
                                "instead of relying on the sub-agent to call unschedule_task. Default is false."
                            )
                        },
                        "max_runs": {
                            "type": "integer",
                            "description": (
                                "Safety cap: automatically cancel the task after this many agent invocations, "
                                "regardless of whether unschedule_task was called. Use 0 (default) for unlimited. "
                                "STRONGLY RECOMMENDED for all polling tasks — set to the maximum reasonable number "
                                "of checks (e.g. 20 for a task polling every 5min over ~2h). "
                                "Prevents orphaned tasks if the agent never calls unschedule_task."
                            )
                        },
                        "ttl_hours": {
                            "type": "number",
                            "description": (
                                "Safety expiry: automatically cancel the task after this many hours since creation, "
                                "regardless of run count. Use 0 (default) for unlimited. "
                                "STRONGLY RECOMMENDED as a backstop (e.g. 4.0 for a task expected to finish in ~2h). "
                                "Prevents orphaned tasks from surviving across reboots or multi-day runs."
                            )
                        }
                    },
                    "required": ["task_id", "prompt", "interval_seconds"]
                }
            }
        })


        # set_reminder
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "set_reminder",
                "description": (
                    "Set a one-shot reminder that is delivered straight to Zulip at a future time — "
                    "use this for 'remind me tomorrow to ...', 'ping me in 2 hours to ...', etc. "
                    "Preferred over schedule_task for reminders: delivery is deterministic (no LLM run) so it "
                    "can't be garbled. Convert natural phrases like 'tomorrow 9am' into an ISO timestamp yourself "
                    "using the current local time from the system context, and pass it as `when`. "
                    "When the request comes from Zulip chat, the recipient is filled in automatically — you only "
                    "need `message` and `when`. After calling this, call task_complete to confirm."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The reminder text to send back to the user, e.g. 'Submit the grant report'."
                        },
                        "when": {
                            "type": "string",
                            "description": "Absolute delivery time as an ISO 8601 timestamp, e.g. '2026-07-05T09:00:00'. Convert 'tomorrow 9am' etc. yourself from the current time. Provide this OR delay_seconds."
                        },
                        "delay_seconds": {
                            "type": "integer",
                            "description": "Alternative to `when`: how many seconds from now to fire (e.g. 7200 for 2 hours)."
                        },
                        "zulip_to": {
                            "type": "string",
                            "description": "Recipient email for a Zulip DM. Optional — from the Zulip bridge this defaults to the requester automatically."
                        },
                        "zulip_stream": {
                            "type": "string",
                            "description": "Alternatively, a Zulip stream/channel name to post the reminder to."
                        },
                        "zulip_topic": {
                            "type": "string",
                            "description": "Topic for the stream message (defaults to 'Reminders'). Only used with zulip_stream."
                        }
                    },
                    "required": ["message"]
                }
            }
        })


        # unschedule_task
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "unschedule_task",
                "description": "Cancel a previously scheduled background task and stop its recurring execution.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "The unique identifier of the task to cancel/stop."
                        }
                    },
                    "required": ["task_id"]
                }
            }
        })

        # list_scheduled_tasks
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "list_scheduled_tasks",
                "description": "List all currently scheduled background tasks with their ID, prompt, interval, and last run time.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        })

        # 12. present_plan — investigate → plan → ask before changing
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "present_plan",
                "description": "PLAN MODE: Investigate first, then present your findings and the ordered list of exact changes you intend to make, and wait for approval before making ANY change. Call this before modifying state (write/edit files, run state-changing commands, save memory, schedule, delegate subagents, etc.). On approval you get a BOUNDED number of state-changing actions (INFER_PLAN_STEP_BUDGET, default 8): execute those steps ONE at a time, validate each result, and when the budget is exhausted call present_plan again with the next chunk. Never start a new change without an in-force approval. The user approves or rejects; do NOT proceed with changes until approved.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "plan": {
                            "type": "string",
                            "description": "Your findings so far, the exact changes or commands you intend to run, and your reasoning / suggestions. Be concrete."
                        }
                    },
                    "required": ["plan"]
                }
            }
        })

        # 13. Searchable conversation history — learn from past sessions
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "search_history",
                "description": "Search all past conversations (backed up locally) with full-text search. Use this to learn from earlier sessions — recall how a previous problem was solved, which commands worked, what the user asked before, etc. Returns matched snippets + session IDs; then call get_session to read a full conversation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keywords to find in past conversations."},
                        "limit": {"type": "integer", "description": "Max results (default 8)."}
                    },
                    "required": ["query"]
                }
            }
        })
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "list_sessions",
                "description": "List the most recent backed-up conversations (session id, size, path). Use it to see what you've worked on recently or to find a session_id for get_session.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Number of recent sessions to list (default 12)."}
                    }
                }
            }
        })
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "get_session",
                "description": "Load a full backed-up conversation by session_id as a readable transcript. Use after search_history / list_sessions to read the entire prior conversation in context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Session id, e.g. sess_1786247517 or 'last'."},
                        "max_chars": {"type": "integer", "description": "Cap on transcript length (default 4000)."}
                    },
                    "required": ["session_id"]
                }
            }
        })
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "rebuild_history_index",
                "description": "Rebuild the full-text search index over all backed-up conversations. Normally automatic; call if history search seems stale or returned nothing.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        })

        # 14. Continuous self-improvement: skill_create / skill_update / skill_note
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "skill_create",
                "description": "SELF-IMPROVEMENT: Persist what you learned during this session into a reusable skill so future sessions inherit it. Call this after completing a non-trivial task, discovering a useful technique/workaround, or when a skill states something you found to be wrong. Saved to the repo .agents/skills AND ~/.config/ai/skills. The user is notified when a skill is created.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill name (directory, e.g. 'pandas_merge_fix')."},
                        "description": {"type": "string", "description": "One-line description: when to use this skill."},
                        "content": {"type": "string", "description": "Full skill body: numbered steps, exact commands, pitfalls, verification, sources."}
                    },
                    "required": ["name", "description", "content"]
                }
            }
        })
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "skill_update",
                "description": "SELF-IMPROVEMENT: Add a 'good to know' note or discrepancy fix to an existing skill after you loaded it and learned something new or found it wrong/outdated. The user is notified when a skill is updated.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name of the existing skill to update."},
                        "note": {"type": "string", "description": "Concise note: what to fix / what you learned, with any exact command or correction."}
                    },
                    "required": ["name", "note"]
                }
            }
        })
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "skill_note",
                "description": "SELF-IMPROVEMENT: Append a standalone learning note to the persisted skills learning log without editing any skill body. Lower-commitment than skill_create/update.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Optional skill name to associate the note with."},
                        "note": {"type": "string", "description": "The lesson / insight to remember across sessions."}
                    },
                    "required": ["note"]
                }
            }
        })

        # 14. task_complete — last so model only sees it as exit
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

    elif action == "show-metrics" or action == "metrics":
        show_metrics()

    elif action == "count-tokens":
        model = sys.argv[2] if len(sys.argv) > 2 else "default"
        text = sys.argv[3] if len(sys.argv) > 3 else ""
        print(count_tokens(model, text))

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

        arguments = normalize_tool_arguments(tool_name, arguments)

        # Validate required arguments before dispatch
        required = TOOL_REQUIRED_ARGS.get(tool_name, [])
        if tool_name == "delegate_task":
            if "task" in arguments or "tasks" in arguments:
                required = []
        missing = [k for k in required if k not in arguments]
        if missing:
            print(json.dumps({"error": f"Missing required argument(s): {', '.join(missing)}"}))
            sys.exit(0)

        # Validate argument types for core tools
        if tool_name in ("read_file", "write_file", "edit_file", "list_directory"):
            p = arguments.get("path")
            if p is not None and not isinstance(p, str):
                print(json.dumps({"error": f"Invalid argument type for 'path': expected string, got {type(p).__name__}"}))
                sys.exit(0)
        elif tool_name in ("web_search", "fetch_webpage"):
            q = arguments.get("query") if tool_name == "web_search" else arguments.get("url")
            if q is not None and not isinstance(q, str):
                print(json.dumps({"error": f"Invalid argument type: expected string, got {type(q).__name__}"}))
                sys.exit(0)

        _t0 = time.time()
        import atexit
        atexit.register(lambda: log_metric(tool_name, (time.time() - _t0) * 1000))

        # Route custom tools
        if tool_name == "think" or server_name == "think":
            # Handled natively in C; this is a safety fallback
            print('{"ok": true}')
        elif tool_name == "task_complete" or server_name == "task_complete":
            # Handled natively in C; this is a safety fallback
            print('{"ok": true}')
        elif tool_name == "execute_remote_command" or server_name == "execute_remote_command":
            host = arguments.get("host", "")
            cmd = arguments.get("command", "")
            if not host or not cmd:
                print(json.dumps({"error": "Missing host or command"}))
            else:
                try:
                    res = subprocess.run(["ssh", host, cmd], capture_output=True, text=True, timeout=120)
                    out = res.stdout + res.stderr
                    if res.returncode != 0:
                        out = f"[Command Failed with exit status {res.returncode}]\n" + out
                    print(out if out else "[Command Success, no output]")
                except Exception as e:
                    print(json.dumps({"error": str(e)}))
        elif tool_name == "remote_exec" or server_name == "remote_exec":
            def ssh_cmd_base(user, host, port):
                """Build SSH command with key or password auth."""
                if password:
                    return f"sshpass -p '{password}' ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p {port}"
                return f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p {port}"
            
            action = arguments.get("action", "connect")
            host = arguments.get("host", "")
            port = arguments.get("port", 22)
            user = arguments.get("user", "")
            password = arguments.get("pass", "")
            cmd = arguments.get("command", "")
            timeout = arguments.get("timeout", 120)
            
            if action == "connect":
                if not host or not user:
                    print(json.dumps({"error": "host and user required for connect"}))
                else:
                    try:
                        ssh_base = ssh_cmd_base(user, host, port)
                        res = subprocess.run(f"{ssh_base} {user}@{host} 'echo Connected'", shell=True, capture_output=True, text=True, timeout=timeout)
                        if res.returncode == 0:
                            print(json.dumps({"status": "connected", "host": host, "user": user}))
                        else:
                            print(json.dumps({"error": f"Failed to connect: {res.stderr}"}))
                    except Exception as e:
                        print(json.dumps({"error": str(e)}))
            
            elif action == "disconnect":
                print(json.dumps({"status": "disconnected"}))
            
            elif action == "discover":
                try:
                    ssh_base = ssh_cmd_base(user, host, port)
                    res = subprocess.run(f"{ssh_base} {user}@{host} 'cat /etc/hosts | grep -v localhost | grep -v ::1'", shell=True, capture_output=True, text=True, timeout=timeout)
                    if res.returncode == 0:
                        nodes = [line.split()[1] for line in res.stdout.strip().split('\n') if len(line.split()) >= 2]
                        print(json.dumps({"discovered_nodes": nodes, "total": len(nodes)}))
                    else:
                        print(json.dumps({"error": "Discovery failed"}))
                except Exception as e:
                    print(json.dumps({"error": str(e)}))
            
            elif action == "status":
                try:
                    ssh_base = ssh_cmd_base(user, host, port)
                    res = subprocess.run(f"{ssh_base} {user}@{host} 'uname -a; echo ---; nproc; echo ---; free -h | head -2; echo ---; df -h / | tail -1'", shell=True, capture_output=True, text=True, timeout=timeout)
                    if res.returncode == 0:
                        print(res.stdout if res.stdout else "[No output]")
                    else:
                        print(json.dumps({"error": "Status failed"}))
                except Exception as e:
                    print(json.dumps({"error": str(e)}))
            
            elif action == "exec":
                if not cmd:
                    print(json.dumps({"error": "command required for exec"}))
                else:
                    try:
                        ssh_base = ssh_cmd_base(user, host, port)
                        res = subprocess.run(f"{ssh_base} {user}@{host} '{cmd}'", shell=True, capture_output=True, text=True, timeout=timeout)
                        out = res.stdout + res.stderr
                        if res.returncode != 0:
                            out = f"[Command Failed with exit status {res.returncode}]\n" + out
                        print(out if out else "[Command Success, no output]")
                    except Exception as e:
                        print(json.dumps({"error": str(e)}))
            
            elif action == "mount":
                if not cmd:
                    print(json.dumps({"error": "command required for mount (format: '<remote_path> <local_mount>')"}))
                else:
                    try:
                        key = "~/.ssh/id_ed25519" if os.path.exists("~/.ssh/id_ed25519") else "~/.ssh/id_rsa"
                        mount_cmd = f"sshfs -o IdentityFile={key} -p {port} {user}@{host}:{cmd} /tmp/mnt_remote"
                        res = subprocess.run(mount_cmd, shell=True, capture_output=True, text=True, timeout=timeout)
                        if res.returncode == 0:
                            print(json.dumps({"status": "mounted", "mount_point": "/tmp/mnt_remote"}))
                        else:
                            print(json.dumps({"error": f"Mount failed: {res.stderr}"}))
                    except Exception as e:
                        print(json.dumps({"error": str(e)}))
            
            elif action == "jobs":
                try:
                    ssh_base = ssh_cmd_base(user, host, port)
                    res = subprocess.run(f"{ssh_base} {user}@{host} 'squeue -u {user} -o \"%i %u %j %T %t %M %N %L %v %c %m %n\"'", shell=True, capture_output=True, text=True, timeout=timeout)
                    if res.returncode == 0:
                        print(res.stdout if res.stdout else "No jobs found")
                    else:
                        print(json.dumps({"error": "Job listing failed"}))
                except Exception as e:
                    print(json.dumps({"error": str(e)}))
            
            elif action == "submit":
                if not cmd:
                    print(json.dumps({"error": "command required for submit"}))
                else:
                    try:
                        ssh_base = ssh_cmd_base(user, host, port)
                        submit_cmd = f"{ssh_base} {user}@{host} 'bash -s' << 'SCRIPT'\n{cmd}\nSCRIPT"
                        res = subprocess.run(submit_cmd, shell=True, capture_output=True, text=True, timeout=timeout)
                        if res.returncode == 0:
                            print(res.stdout if res.stdout else "[Job submitted successfully]")
                        else:
                            print(json.dumps({"error": f"Submit failed: {res.stderr}"}))
                    except Exception as e:
                        print(json.dumps({"error": str(e)}))
            
            else:
                print(json.dumps({"error": f"Unknown action: {action}"}))
        elif tool_name == "check_time" or server_name == "check_time":
            import datetime
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(now)
        elif tool_name == "schedule_task" or server_name == "schedule_task":
            task_id = arguments.get("task_id")
            prompt = arguments.get("prompt")
            interval_seconds = arguments.get("interval_seconds", 300)
            run_once = arguments.get("run_once", False)
            # Optional safety guardrails — passed via extra so schedule_task stores them in the JSON
            extra_sched = {}
            if "max_runs" in arguments and arguments["max_runs"]:
                extra_sched["max_runs"] = int(arguments["max_runs"])
            if "ttl_hours" in arguments and arguments["ttl_hours"]:
                extra_sched["ttl_hours"] = float(arguments["ttl_hours"])
            result = schedule_task(task_id, prompt, interval_seconds, run_once=run_once,
                                   extra=extra_sched or None)
            print(result)

        elif tool_name == "set_reminder" or server_name == "set_reminder":
            result = set_reminder(
                message=arguments.get("message"),
                when=arguments.get("when"),
                delay_seconds=arguments.get("delay_seconds"),
                zulip_to=arguments.get("zulip_to"),
                zulip_stream=arguments.get("zulip_stream"),
                zulip_topic=arguments.get("zulip_topic"),
                task_id=arguments.get("task_id"),
            )
            print(result)
        elif tool_name == "unschedule_task" or server_name == "unschedule_task":
            task_id = arguments.get("task_id")
            result = unschedule_task(task_id)
            print(result)
        elif tool_name == "list_scheduled_tasks" or server_name == "list_scheduled_tasks":
            result = list_scheduled_tasks()
            print(result)
        elif tool_name == "arxiv_search" or server_name == "arxiv_search":
            query = arguments.get("query")
            max_results = arguments.get("max_results", 5)
            result = arxiv_search(query, max_results)
            print(result)
        elif tool_name == "list_directory" or server_name == "list_directory":
            path = arguments.get("path", ".")
            result = list_directory(path)
            print(result)
        elif tool_name == "web_search" or server_name == "web_search":
            query = arguments.get("query", "")
            result = web_search(query)
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
        elif tool_name == "remember" or server_name == "remember":
            content = arguments.get("content", "")
            metadata = arguments.get("metadata", "")
            result = remember(content, metadata=metadata)
            print(result)
        elif tool_name == "recall" or server_name == "recall":
            query = arguments.get("query", "")
            result = recall(query)
            print(result)
        elif tool_name == "execute_command" or server_name == "execute_command":
            cmd = arguments.get("command", "")
            timeout = arguments.get("timeout", 120)
            result = execute_command(cmd, timeout=timeout)
            print(result)
        elif tool_name == "get_context_snippet" or server_name == "get_context_snippet":
            idx = arguments.get("index", 0)
            result = get_context_snippet(idx)
            print(result)
        elif tool_name == "search_context" or server_name == "search_context":
            query = arguments.get("query", "")
            result = search_context(query)
            print(result)
        elif tool_name == "structured_query" or server_name == "structured_query":
            target = arguments.get("target", "")
            filter_expr = arguments.get("filter_expr")
            transform = arguments.get("transform")
            aggregate = arguments.get("aggregate")
            result = structured_query(target, filter_expr=filter_expr, transform=transform, aggregate=aggregate)
            print(result)
        elif tool_name == "spawn_agent" or server_name == "spawn_agent":
            name = arguments.get("name", "subagent")
            prompt = arguments.get("prompt", "")
            tools = arguments.get("tools")
            persistent = arguments.get("persistent", True)
            result = spawn_agent(name, prompt, tools=tools, persistent=persistent)
            print(result)
        elif tool_name == "resume_agent" or server_name == "resume_agent":
            agent_id = arguments.get("agent_id", "")
            msg = arguments.get("message", "")
            result = resume_agent(agent_id, msg)
            print(result)
        elif tool_name == "list_agents" or server_name == "list_agents":
            result = list_agents()
            print(result)
        elif tool_name == "session_report" or server_name == "session_report":
            success = arguments.get("success", True)
            failure_modes = arguments.get("failure_modes")
            notes = arguments.get("notes", "")
            result = session_report(success=success, failure_modes=failure_modes, notes=notes)
            print(result)
        elif tool_name == "list_processes" or server_name == "list_processes":
            result = list_processes()
            print(result)
        elif tool_name == "start_background_process" or server_name == "start_background_process":
            try:
                res = start_background_process(arguments.get("command", ""), arguments.get("log_file"))
                print(res)
            except Exception as e:
                print(f"Error in start_background_process: {e}")
        elif tool_name == "check_process_status" or server_name == "check_process_status":
            try:
                res = check_process_status(arguments.get("pid"), arguments.get("log_file"))
                print(res)
            except Exception as e:
                print(f"Error in check_process_status: {e}")
        elif tool_name == "stop_process" or server_name == "stop_process":
            try:
                res = stop_process(arguments.get("pid"))
                print(res)
            except Exception as e:
                print(f"Error in stop_process: {e}")
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
            try:
                result = computer_control(arguments)
                print(result)
            except Exception as e:
                print(f"Error in computer_control: {e}")
        elif tool_name == "pubmed_research_round" or server_name == "pubmed_research_round":
            try:
                result = pubmed_research_round(
                    query=arguments.get("query", ""),
                    known_dois=arguments.get("known_dois"),
                    start_date=arguments.get("start_date"),
                    end_date=arguments.get("end_date"),
                )
                print(result)
            except Exception as e:
                print(f"Error in pubmed_research_round: {e}")
        elif tool_name == "get_system_status" or server_name == "get_system_status":
            try:
                result = get_system_status()
                print(result)
            except Exception as e:
                print(f"Error in get_system_status: {e}")
        elif tool_name == "get_clipboard" or server_name == "get_clipboard":
            try:
                result = get_clipboard()
                print(result)
            except Exception as e:
                print(f"Error in get_clipboard: {e}")
        elif tool_name == "learn_rule" or server_name == "learn_rule":
            try:
                rule_text = arguments.get("rule_text", "")
                if not rule_text:
                    print("Error: rule_text required")
                else:
                    rules_file = os.path.expanduser("~/.config/ai/rules.txt")
                    os.makedirs(os.path.dirname(rules_file), exist_ok=True)
                    with open(rules_file, "a") as f:
                        f.write(rule_text.strip() + "\n")
                    print(f"Rule successfully saved to {rules_file}")
            except Exception as e:
                print(f"Error in learn_rule: {e}")
        elif tool_name == "reset_context" or server_name == "reset_context":
            print("Context reset initiated. ai.c will process this and clear the message array.")
        elif tool_name == "vault_write" or server_name == "vault_write":
            try:
                res = vault_write(arguments.get("title", ""), arguments.get("content", ""), arguments.get("links", ""))
                print(res)
            except Exception as e:
                import traceback
                print(f"[SYSTEM EXCEPTION INTERCEPTED]\nAn internal error occurred in the middleware during tool execution:\n{str(e)}\n{traceback.format_exc()}\n\n[GRAPH ENFORCEMENT]\nYou must pause, recalculate your approach, and try a different strategy.")
        elif tool_name == "vault_read" or server_name == "vault_read":
            try:
                res = vault_read(arguments.get("title", ""))
                print(res)
            except Exception as e:
                import traceback
                print(f"[SYSTEM EXCEPTION INTERCEPTED]\nAn internal error occurred in the middleware during tool execution:\n{str(e)}\n{traceback.format_exc()}\n\n[GRAPH ENFORCEMENT]\nYou must pause, recalculate your approach, and try a different strategy.")
        elif tool_name == "vault_search" or server_name == "vault_search":
            try:
                res = vault_search(arguments.get("query", ""))
                print(res)
            except Exception as e:
                import traceback
                print(f"[SYSTEM EXCEPTION INTERCEPTED]\nAn internal error occurred in the middleware during tool execution:\n{str(e)}\n{traceback.format_exc()}\n\n[GRAPH ENFORCEMENT]\nYou must pause, recalculate your approach, and try a different strategy.")
        elif tool_name == "vault_backlinks" or server_name == "vault_backlinks":
            try:
                res = vault_backlinks(arguments.get("title", ""))
                print(res)
            except Exception as e:
                import traceback
                print(f"[SYSTEM EXCEPTION INTERCEPTED]\nAn internal error occurred in the middleware during tool execution:\n{str(e)}\n{traceback.format_exc()}\n\n[GRAPH ENFORCEMENT]\nYou must pause, recalculate your approach, and try a different strategy.")
        elif tool_name == "pubmed_search" or server_name == "pubmed_search":
            try:
                result = pubmed_search(
                    query=arguments.get("query", ""),
                    top_k=arguments.get("top_k", 10),
                    start_date=arguments.get("start_date"),
                    end_date=arguments.get("end_date"),
                    high_quality_only=arguments.get("high_quality_only", True),
                )
                print(result)
            except Exception as e:
                print(f"Error in pubmed_search: {e}")
        elif tool_name == "gcal_list_events" or server_name == "gcal_list_events":
            try:
                import gcal
                result = gcal.list_events(
                    time_min=arguments.get("time_min"),
                    time_max=arguments.get("time_max"),
                    max_results=arguments.get("max_results", 20),
                    calendar_ids=arguments.get("calendar_ids")
                )
                print(result)
            except Exception as e:
                print(f"Error in gcal_list_events: {e}")
        elif tool_name == "gcal_create_event" or server_name == "gcal_create_event":
            try:
                import gcal
                result = gcal.create_event(
                    summary=arguments.get("summary"),
                    start_time=arguments.get("start_time"),
                    end_time=arguments.get("end_time"),
                    description=arguments.get("description"),
                    location=arguments.get("location"),
                    attendees=arguments.get("attendees"),
                    calendar_id=arguments.get("calendar_id", 'primary')
                )
                print(result)
            except Exception as e:
                print(f"Error in gcal_create_event: {e}")
        elif tool_name == "gcal_check_availability" or server_name == "gcal_check_availability":
            try:
                import gcal
                result = gcal.check_availability(
                    time_min=arguments.get("time_min"),
                    time_max=arguments.get("time_max"),
                    calendar_ids=arguments.get("calendar_ids")
                )
                print(result)
            except Exception as e:
                print(f"Error in gcal_check_availability: {e}")
        elif tool_name == "gcal_quick_add" or server_name == "gcal_quick_add":
            try:
                import gcal
                result = gcal.quick_add(
                    text=arguments.get("text", ""),
                    calendar_id=arguments.get("calendar_id", 'primary')
                )
                print(result)
            except Exception as e:
                print(f"Error in gcal_quick_add: {e}")
        elif tool_name == "gcal_update_event" or server_name == "gcal_update_event":
            try:
                import gcal
                result = gcal.update_event(
                    event_id=arguments.get("event_id"),
                    summary=arguments.get("summary"),
                    start_time=arguments.get("start_time"),
                    end_time=arguments.get("end_time"),
                    description=arguments.get("description"),
                    location=arguments.get("location"),
                    attendees=arguments.get("attendees"),
                    time_zone=arguments.get("time_zone"),
                    calendar_id=arguments.get("calendar_id", 'primary')
                )
                print(result)
            except Exception as e:
                print(f"Error in gcal_update_event: {e}")
        elif tool_name == "gcal_delete_event" or server_name == "gcal_delete_event":
            try:
                import gcal
                result = gcal.delete_event(
                    event_id=arguments.get("event_id"),
                    calendar_id=arguments.get("calendar_id", 'primary')
                )
                print(result)
            except Exception as e:
                print(f"Error in gcal_delete_event: {e}")
        elif tool_name == "present_plan" or server_name == "present_plan":
            # Native approval flow is handled in ai.c; this is a safety fallback for
            # direct ai_mcp.py invocations.
            plan = arguments.get("plan", "")
            print('{"ok": true, "note": "present_plan acknowledged (handled natively by the agent loop in ai.c). If you are in plan mode, the user will be asked to approve or reject this plan."}')
        elif tool_name == "search_history" or server_name == "search_history":
            try:
                print(search_history(query=arguments.get("query", ""), limit=arguments.get("limit", 8)))
            except Exception as e:
                print(f"Error in search_history: {e}")
        elif tool_name == "list_sessions" or server_name == "list_sessions":
            try:
                print(list_sessions(limit=arguments.get("limit", 12)))
            except Exception as e:
                print(f"Error in list_sessions: {e}")
        elif tool_name == "get_session" or server_name == "get_session":
            try:
                print(get_session(session_id=arguments.get("session_id", ""), max_chars=arguments.get("max_chars", 4000)))
            except Exception as e:
                print(f"Error in get_session: {e}")
        elif tool_name == "rebuild_history_index" or server_name == "rebuild_history_index":
            try:
                print(rebuild_history_index())
            except Exception as e:
                print(f"Error in rebuild_history_index: {e}")
        elif tool_name == "skill_create" or server_name == "skill_create":
            try:
                print(skill_create(
                    name=arguments.get("name", ""),
                    description=arguments.get("description", ""),
                    content=arguments.get("content", ""),
                ))
            except Exception as e:
                print(f"Error in skill_create: {e}")
        elif tool_name == "skill_update" or server_name == "skill_update":
            try:
                print(skill_update(
                    name=arguments.get("name", ""),
                    note=arguments.get("note", ""),
                ))
            except Exception as e:
                print(f"Error in skill_update: {e}")
        elif tool_name == "skill_note" or server_name == "skill_note":
            try:
                print(skill_note(
                    name=arguments.get("name", ""),
                    note=arguments.get("note", ""),
                ))
            except Exception as e:
                print(f"Error in skill_note: {e}")
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
