# Smart Web Fetch — Design Spec
**Date:** 2026-06-29  
**Status:** Approved

## Problem

The existing `fetch_webpage` and `fetch_webpage_js` tools fail on a wide class of real-world sites:

- **Cloudflare / Akamai protected sites** — Python's default TLS stack exposes a non-browser fingerprint; these CDNs block requests before the page is served.
- **JS-heavy SPAs** — `fetch_webpage` returns an empty shell; `fetch_webpage_js` exists but is not used automatically.
- **Headless-browser detection** — `fetch_webpage_js` runs plain Playwright with `navigator.webdriver = true` and missing browser APIs, triggering fingerprint-based blocks.

## Goal

Add a `fetch_smart` tool that is the primary fetch path for the AI, handling all three failure modes with a speed-first cascade. The old tools remain available for explicit use.

## Architecture

### Fetch Cascade

```
fetch_smart(url)
    │
    ├─► 1. curl_cffi  (browser TLS impersonation, ~0.5–2 s)
    │       success + ≥30 words → extract → return
    │       blocked/empty        → escalate to step 2
    │
    ├─► 2. Playwright + playwright-stealth  (~5–15 s)
    │       success              → extract → return
    │       still blocked        → return best-effort + [FETCH_WARN]
    │
    └─► graceful fallback: original urllib path (if deps missing)
```

### Block Detection Heuristics

A response is considered "blocked" if any of the following are true:
- HTTP status 403, 429, or 503
- Response body contains Cloudflare challenge markers: `"cf-browser-verification"`, `"cf_chl_opt"`, `"Checking your browser"`
- Extracted word count < 30 after trafilatura/regex extraction
- JS-only warning string present in extracted text

### Components

#### 1. `fetch_smart(url)` — new function in `ai_mcp.py`

- Uses `curl_cffi.requests.get(url, impersonate="chrome124")` for TLS fingerprint impersonation.
- On success, runs the same trafilatura → regex extraction pipeline as `fetch_webpage`.
- On block detection, calls the upgraded `fetch_webpage_js(url)`.
- If `curl_cffi` is not installed, calls `fetch_webpage(url)` directly (no crash).
- Returns `[Source (smart): <url>]` prefix for provenance.

#### 2. Upgraded `fetch_webpage_js()` — modified in `ai_mcp.py`

- After `browser.new_page()`, applies `playwright_stealth.stealth_sync(page)` if available.
- Sets realistic context: `viewport={"width": 1920, "height": 1080}`, `locale="en-US"`, `timezone_id="America/New_York"`.
- After `wait_for_load_state`, scrolls to bottom to trigger lazy-loaded content.
- If `playwright-stealth` is not installed, runs exactly as before.

#### 3. Tool schema — new `fetch_smart` entry in `list-tools`

- Positioned after `fetch_webpage` in the schema list.
- Description marks it as the **preferred** fetch tool for any URL that might be protected.
- `fetch_webpage` description updated to "fast, static-only pages — use fetch_smart for anything that might be JS or bot-protected".
- `fetch_webpage_js` description updated to "explicit manual override for JS rendering without the smart cascade".

#### 4. Skill file — `.agents/skills/smart_web_fetch/SKILL.md`

Guidance for the model:
- Use `fetch_smart` as the default for all web fetches (replaces reaching for `fetch_webpage` first).
- Use `fetch_webpage` only for known-static, unprotected content (e.g. raw GitHub files, plain APIs).
- Use `fetch_webpage_js` only when you need direct control over Playwright behavior.
- If `fetch_smart` returns `[FETCH_WARN]`, note the limitation in the response and suggest the user visit the URL directly.

#### 5. `install.sh` — dependency additions

```bash
pip install --quiet curl-cffi playwright-stealth 2>/dev/null || true
```

Added after the existing `pip install trafilatura markdownify` line. Failures are non-fatal (graceful fallback handles missing deps).

## Error Handling Matrix

| Scenario | Behaviour |
|---|---|
| `curl_cffi` not installed | Falls back to `fetch_webpage` silently |
| `playwright-stealth` not installed | Playwright runs without stealth (existing behaviour) |
| Playwright not installed | `fetch_smart` returns `curl_cffi` result or `fetch_webpage` result |
| Both escalation steps fail | Returns best-effort content + `[FETCH_WARN: site resisted all fetch methods]` |
| PDF / binary at URL | Handed to existing PDF extraction chain (no change) |
| HTTP 429 rate limit | Returns content as-is with status note; no retry (avoid hammering) |
| Redirect chain | `curl_cffi` and `urllib` both follow redirects natively |

## Testing Plan

Manual tests after implementation:

1. **Static page** — `https://example.com` → should return content via `curl_cffi` fast path.
2. **Cloudflare-protected** — e.g. a news site like `https://www.reuters.com/` → `curl_cffi` impersonation should bypass.
3. **JS SPA** — `https://news.ycombinator.com` (light) and a heavier React app → Playwright escalation if needed.
4. **Already-working page** — verify `fetch_webpage` still works independently.
5. **Missing dep** — uninstall `curl_cffi` temporarily, confirm graceful fallback.

## Out of Scope

- Proxy rotation / residential proxies (adds operational complexity, not needed for most use cases)
- Cookie/session persistence across calls
- CAPTCHA solving
- Handling login-gated content
