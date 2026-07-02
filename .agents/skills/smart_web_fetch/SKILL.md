---
name: smart-web-fetch
description: CRITICAL — when fetching web pages, URLs, or scraping online content: Guidance for choosing between fetch_smart, fetch_webpage, fetch_webpage_js, and parallel_fetch.
---

# Smart Web Fetch

## Default tool: fetch_smart

Use `fetch_smart` for all web fetches unless you have a specific reason not to.

`fetch_smart` cascades automatically:
1. **curl_cffi** — browser TLS fingerprint impersonation (~1–2 s), bypasses Cloudflare and Akamai
2. **Playwright + stealth** — headless browser with anti-detection patches, handles JS-rendered SPAs
3. **urllib fallback** — last resort if neither dep is installed

## When to use each tool

| Tool | When |
|---|---|
| `fetch_smart` | Default for any URL — news sites, docs, APIs, search results |
| `fetch_webpage` | Known-static, unprotected content: raw GitHub files, plain text APIs |
| `fetch_webpage_js` | You need explicit Playwright control (custom `wait_for`, waiting for a specific element) |
| `parallel_fetch` | Fetching 2+ URLs concurrently — uses `fetch_webpage` internally, not `fetch_smart` |

## Handling FETCH_WARN

If `fetch_smart` returns `[FETCH_WARN: site resisted all fetch methods]`:
- The site likely requires login, a CAPTCHA solve, or aggressively blocks all automation
- Report the limitation to the user
- Suggest they visit the URL directly in a browser
- Do not retry the same URL with different tools — it will not help

## Search → fetch workflow

Always read at least one result URL after `web_search` before calling `task_complete`:

```
web_search("query") → fetch_smart(top_url) → task_complete(summary)
```

Never present search snippet text as the answer — snippets are always truncated and may be stale.
