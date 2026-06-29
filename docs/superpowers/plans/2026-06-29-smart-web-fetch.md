# Smart Web Fetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `fetch_smart` tool that cascades curl_cffi TLS impersonation → Playwright+stealth → urllib fallback, and upgrade `fetch_webpage_js` with playwright-stealth anti-detection.

**Architecture:** `fetch_smart` is a new function/tool that tries `curl_cffi` (browser TLS fingerprint) first for speed, detects blocked/empty responses via heuristics, then escalates to an upgraded `fetch_webpage_js` that applies `playwright-stealth` to hide headless browser tells. The old tools remain unchanged for direct use; graceful fallback handles missing deps.

**Tech Stack:** Python 3, `curl_cffi` (libcurl + CFFI browser impersonation), `playwright-stealth` (Playwright anti-detection patch), existing `trafilatura` + `markdownify` extraction pipeline.

## Global Constraints

- No test suite — verify by running `python3 ai_mcp.py call-tool fetch_smart fetch_smart '{"url":"..."}'` directly.
- All dep failures must be non-fatal: if `curl_cffi` or `playwright-stealth` are not installed, fall back silently.
- Do not break existing `fetch_webpage`, `fetch_webpage_js`, or `parallel_fetch` behaviour.
- Keep all changes inside `ai_mcp.py` (function + schema), `install.sh`, and the new skill file.
- Match existing code style: no type hints, no dataclasses, bare `except Exception`.

---

### Task 1: Install dependencies

**Files:**
- Modify: `install.sh`

**Interfaces:**
- Produces: `curl_cffi` and `playwright-stealth` available in the Python environment

- [ ] **Step 1: Add pip installs to install.sh**

Find the existing pip install line in `install.sh`:
```bash
pip install --quiet trafilatura markdownify pdfplumber 2>/dev/null || true
```
Add after it:
```bash
pip install --quiet "curl-cffi>=0.7" playwright-stealth 2>/dev/null || true
```

- [ ] **Step 2: Install now in this session**

```bash
pip install --quiet "curl-cffi>=0.7" playwright-stealth
```

- [ ] **Step 3: Verify imports work**

```bash
python3 -c "from curl_cffi import requests; print('curl_cffi ok')"
python3 -c "from playwright_stealth import stealth_sync; print('playwright_stealth ok')"
```
Expected: both lines print `ok`.

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "feat: add curl-cffi and playwright-stealth to install deps"
```

---

### Task 2: Add `_is_blocked()` helper and `fetch_smart()` to ai_mcp.py

**Files:**
- Modify: `ai_mcp.py` — insert after line 360 (end of `fetch_webpage_js`)

**Interfaces:**
- Consumes: `fetch_webpage(url)`, `fetch_webpage_js(url)`, `_HAS_TRAFILATURA`, `trafilatura`, `_html_to_text_fallback(html, url)`, `os.environ.get("INFER_MAX_TOOL_OUTPUT")`
- Produces: `fetch_smart(url)` → `str` with `[Source (smart/...): <url>]` prefix

- [ ] **Step 1: Add `_is_blocked()` helper immediately after `fetch_webpage_js` (after line ~360)**

Insert this function in `ai_mcp.py` after the `fetch_webpage_js` function:

```python
def _is_blocked(text, status_code=200):
    """Return True if a fetched response looks like a bot-block or empty page."""
    if status_code in (403, 429, 503):
        return True
    if not text or len(text.split()) < 30:
        return True
    lower = text.lower()
    cf_markers = [
        'cf-browser-verification', 'cf_chl_opt', 'checking your browser',
        'please wait while we verify', 'ddos-guard', 'enable javascript',
        'javascript is required',
    ]
    return any(m in lower for m in cf_markers)
```

- [ ] **Step 2: Add `fetch_smart()` immediately after `_is_blocked()`**

```python
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
        # curl_cffi not installed — skip to urllib fallback directly
        return fetch_webpage(url)
    except Exception:
        pass  # network error or parse failure — try Playwright

    # ── Step 2: Playwright + stealth ─────────────────────────────────────────
    try:
        js_result = fetch_webpage_js(url)
        # fetch_webpage_js returns "[Source (JS-rendered): url]\n\ncontent"
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
```

- [ ] **Step 3: Smoke-test the function directly**

```bash
cd /home/dzyla/Code/ai-buddy
python3 -c "
import sys; sys.path.insert(0, '.')
from ai_mcp import fetch_smart
r = fetch_smart('https://example.com')
print(r[:300])
"
```
Expected: prints `[Source (smart/curl): https://example.com]` followed by page text.

- [ ] **Step 4: Commit**

```bash
git add ai_mcp.py
git commit -m "feat: add _is_blocked helper and fetch_smart cascade function"
```

---

### Task 3: Upgrade `fetch_webpage_js` with playwright-stealth + realistic context

**Files:**
- Modify: `ai_mcp.py` — `fetch_webpage_js()` function (~lines 288–360)

**Interfaces:**
- Consumes: `playwright.sync_api.sync_playwright`, optional `playwright_stealth.stealth_sync`
- Produces: same return signature as before; internally uses stealth + 1920×1080 context + scroll

- [ ] **Step 1: Replace the inner browser block in `fetch_webpage_js`**

Find this block (approximately lines 302–313):
```python
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"})
                page.goto(url, timeout=timeout_ms)
                try:
                    page.wait_for_load_state(wait_for, timeout=timeout_ms)
                except Exception:
                    pass  # timeout on networkidle is fine — grab what we have
                html = page.content()
            finally:
                browser.close()
```

Replace with:
```python
        try:
            from playwright_stealth import stealth_sync as _stealth_sync
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
                    _stealth_sync(page)
                page.goto(url, timeout=timeout_ms)
                try:
                    page.wait_for_load_state(wait_for, timeout=timeout_ms)
                except Exception:
                    pass  # timeout on networkidle is fine — grab what we have
                # Scroll to bottom to trigger lazy-loaded content
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(800)
                except Exception:
                    pass
                html = page.content()
            finally:
                browser.close()
```

- [ ] **Step 2: Smoke-test the upgraded function**

```bash
cd /home/dzyla/Code/ai-buddy
python3 -c "
from ai_mcp import fetch_webpage_js
r = fetch_webpage_js('https://example.com')
print(r[:300])
"
```
Expected: `[Source (JS-rendered): https://example.com]` + content. No crash.

- [ ] **Step 3: Commit**

```bash
git add ai_mcp.py
git commit -m "feat: upgrade fetch_webpage_js with playwright-stealth and realistic browser context"
```

---

### Task 4: Add `fetch_smart` tool schema and update existing tool descriptions

**Files:**
- Modify: `ai_mcp.py` — `list-tools` branch (~lines 1514–1540 for fetch_webpage schema)

**Interfaces:**
- Consumes: nothing new
- Produces: `fetch_smart` available as a named tool in `list-tools` output; `fetch_webpage` and `fetch_webpage_js` descriptions updated

- [ ] **Step 1: Update `fetch_webpage` description**

Find (approximately line 1519):
```python
                "description": "Download and read the text content of a URL. Use after web_search to read actual page content. Required before task_complete if search returned URLs — never present links without reading them.",
```
Replace with:
```python
                "description": "Download and read a static, unprotected URL quickly. For any URL that might be JS-rendered or bot-protected, use fetch_smart instead. Required before task_complete if search returned URLs — never present links without reading them.",
```

- [ ] **Step 2: Update `fetch_webpage_js` description**

Find (approximately line 1781):
```python
                    "Use this when fetch_webpage returns empty content, a JS-required warning, or a login page. "
                    "Slower than fetch_webpage (~5-15s) — prefer fetch_webpage for static pages."
```
Replace with:
```python
                    "Explicit Playwright override: renders the page with a real headless browser + stealth patches. "
                    "Use only when you need direct control over JS rendering. For most cases, prefer fetch_smart which cascades automatically."
```

- [ ] **Step 3: Insert `fetch_smart` schema after `fetch_webpage` schema block**

Find the end of the `fetch_webpage` schema block. It ends just before `fetch_webpage_js`. Insert this new block between them:

```python
        # 4b. fetch_smart
        if not tools_filter or "fetch_smart" in tools_filter:
            tools.append({
                "type": "function",
                "function": {
                    "name": "fetch_smart",
                    "description": (
                        "Preferred fetch tool. Downloads a URL using a browser TLS fingerprint (curl_cffi) "
                        "to bypass Cloudflare and similar bot protections. Automatically escalates to "
                        "Playwright+stealth for JS-rendered pages if the fast path is blocked. "
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
```

- [ ] **Step 4: Add routing in `call-tool` branch**

Find the `elif tool_name == "fetch_webpage_js"` routing block and add before it:

```python
        elif tool_name == "fetch_smart" or server_name == "fetch_smart":
            url = arguments.get("url", "")
            result = fetch_smart(url)
            print(result)
```

- [ ] **Step 5: Add `fetch_smart` to the required-args map**

Find:
```python
    "fetch_webpage":   ["url"],
```
Add after it:
```python
    "fetch_smart":     ["url"],
```

- [ ] **Step 6: Verify tool appears in list-tools output**

```bash
cd /home/dzyla/Code/ai-buddy
python3 ai_mcp.py list-tools | python3 -c "
import sys, json
tools = json.load(sys.stdin)
names = [t['function']['name'] for t in tools]
assert 'fetch_smart' in names, 'fetch_smart missing'
print('OK — tools:', [n for n in names if 'fetch' in n])
"
```
Expected: `OK — tools: ['fetch_webpage', 'fetch_smart', 'fetch_webpage_js', 'parallel_fetch']`

- [ ] **Step 7: Commit**

```bash
git add ai_mcp.py
git commit -m "feat: add fetch_smart tool schema and update fetch_webpage/fetch_webpage_js descriptions"
```

---

### Task 5: Write skill file

**Files:**
- Create: `.agents/skills/smart_web_fetch/SKILL.md`

**Interfaces:**
- Produces: skill loaded into system prompt guiding model to use `fetch_smart` as default

- [ ] **Step 1: Create skill directory and SKILL.md**

```bash
mkdir -p /home/dzyla/Code/ai-buddy/.agents/skills/smart_web_fetch
```

Write `.agents/skills/smart_web_fetch/SKILL.md`:

```markdown
---
name: smart-web-fetch
description: Guidance for choosing between fetch_smart, fetch_webpage, and fetch_webpage_js. Use whenever fetching URLs to ensure the right tool is used for bot-protected or JS-heavy sites.
---

# Smart Web Fetch

## Default tool: fetch_smart

Use `fetch_smart` for all web fetches unless you have a specific reason not to.

`fetch_smart` cascades automatically:
1. TLS browser impersonation (curl_cffi) — fast (~1–2 s), bypasses Cloudflare and Akamai
2. Playwright + stealth — handles JS-rendered SPAs, bypasses headless-browser detection
3. urllib fallback — last resort

## When to use each tool

| Tool | When |
|---|---|
| `fetch_smart` | Default for any URL — news sites, docs, APIs, search results |
| `fetch_webpage` | Known-static, unprotected content: raw GitHub files, plain text APIs, local URLs |
| `fetch_webpage_js` | You need explicit Playwright control (e.g. waiting for a specific element, custom wait_for) |
| `parallel_fetch` | Fetching 2+ URLs concurrently — uses fetch_webpage internally, not fetch_smart |

## Handling FETCH_WARN

If `fetch_smart` returns `[FETCH_WARN: site resisted all fetch methods]`:
- The site likely requires login, a CAPTCHA solve, or is aggressively blocking all automated access
- Report the limitation to the user
- Suggest they visit the URL directly in a browser
- Do not retry the same URL with different tools — it will not help

## Search → fetch workflow

Always read at least one result URL after web_search before calling task_complete:

```
web_search("query") → fetch_smart(top_url) → task_complete(summary)
```

Never present search snippet text as the answer — snippets are always truncated and may be stale.
```

- [ ] **Step 2: Verify skill file is valid**

```bash
head -5 /home/dzyla/Code/ai-buddy/.agents/skills/smart_web_fetch/SKILL.md
```
Expected: frontmatter block with `name:` and `description:` fields.

- [ ] **Step 3: Commit**

```bash
git add .agents/skills/smart_web_fetch/SKILL.md
git commit -m "feat: add smart_web_fetch skill with tool selection guidance"
```

---

### Task 6: End-to-end manual tests

**Files:** None modified — verification only.

- [ ] **Step 1: Test static page via curl_cffi fast path**

```bash
cd /home/dzyla/Code/ai-buddy
python3 ai_mcp.py call-tool fetch_smart fetch_smart '{"url":"https://example.com"}'
```
Expected: `[Source (smart/curl): https://example.com]` + "Example Domain" text.

- [ ] **Step 2: Test a Cloudflare-protected news site**

```bash
python3 ai_mcp.py call-tool fetch_smart fetch_smart '{"url":"https://www.reuters.com/world/"}'
```
Expected: actual news headlines/content, NOT a Cloudflare challenge page or 403 error.

- [ ] **Step 3: Test a JS-heavy site (escalation path)**

```bash
python3 ai_mcp.py call-tool fetch_smart fetch_smart '{"url":"https://news.ycombinator.com"}'
```
Expected: HN story titles and links in the output.

- [ ] **Step 4: Test that fetch_webpage still works independently**

```bash
python3 ai_mcp.py call-tool fetch_webpage fetch_webpage '{"url":"https://example.com"}'
```
Expected: `[Source: https://example.com]` + content. No regression.

- [ ] **Step 5: Test the binary end-to-end (requires INFER_* env vars set)**

```bash
echo "fetch https://www.reuters.com/ and tell me the top headline" | ./ai
```
Expected: ai uses `fetch_smart`, returns a real headline from reuters.com.

- [ ] **Step 6: Sync skills to ~/.config/ai/skills**

```bash
cp -r /home/dzyla/Code/ai-buddy/.agents/skills/. ~/.config/ai/skills/
```

- [ ] **Step 7: Final commit**

```bash
git add install.sh ai_mcp.py .agents/skills/smart_web_fetch/
git commit -m "feat: smart web fetch — curl_cffi cascade + playwright-stealth upgrade + skill" --allow-empty
```
