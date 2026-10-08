"""Web search (DuckDuckGo Lite, Brave, SearXNG) and page fetching (fetch_smart cascade, basic, JS)."""
import os
import json
import urllib.request
import urllib.parse
import urllib.error
import re
import time

from .files import effective_max_tool_output

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
    max_tool = effective_max_tool_output()
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
                    max_tool = effective_max_tool_output()
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
    if isinstance(url, dict):
        url = url.get("url") or url.get("link") or ""
    if not isinstance(url, str):
        url = str(url or "")
    if os.environ.get("INFER_FETCH_BASIC") == "1":
        return fetch_webpage_basic(url)
    return fetch_smart(url)


def fetch_webpage_js(url, wait_for="networkidle", timeout_ms=30000):
    """Fetch a JS-rendered page via Playwright and return its content as markdown."""
    if isinstance(url, dict):
        url = url.get("url") or url.get("link") or ""
    if not isinstance(url, str):
        url = str(url or "")
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

        max_tool = effective_max_tool_output()
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
            max_tool = effective_max_tool_output()
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
