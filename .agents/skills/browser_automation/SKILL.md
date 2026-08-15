---
name: browser_automation
description: CRITICAL — when a page needs JavaScript, a login-free interactive site, forms, clicks, or screenshots (anything fetch_webpage can't read): use the persistent `browser` tool, not a one-shot fetch.
---

# `browser_automation`

The `browser` tool drives a **persistent** headless Chromium (a small daemon over a
unix socket), so page state — current URL, filled forms, JS context, scroll —
survives across calls. This is the tool for anything `fetch_webpage` / `fetch_smart`
cannot handle: JS-rendered content, forms, click-throughs, and screenshots.

## Use browser ONLY when the page is interactive or JS-only

For a plain static page, `fetch_webpage` is faster and cheaper. Reach for `browser`
when you need to click, type, wait for JS, read post-render text, or screenshot.

## Actions

- `goto(url)` — navigate.
- `content()` — visible text of the CURRENT page (this is the post-JS text; use this
  to read JS-rendered sites, not `fetch_webpage`).
- `html()` — raw HTML of the current page.
- `links()` — up to 150 `{text, href}` pairs.
- `click(selector)` / `fill(selector, value)` / `select(selector, value)` — interact
  with elements by CSS selector.
- `press(key)` — press a key (Enter, Tab, Escape).
- `js(expr)` — evaluate a JS expression and return the result (e.g.
  `document.querySelectorAll('a').length`).
- `wait(selector?)` — wait for a selector or for networkidle.
- `back()` — go back.
- `screenshot(path?)` — save a PNG; returns `[IMAGE_DATA_SUCCESS:<path>]`.
- `status()` — current url/title/idle time (cheap liveness check).
- `shutdown` — stop the daemon (it also idles out after 10 min).

## Workflow

1. `goto(url)`.
2. If content is JS-rendered or you just interacted, `wait` (selector or
   networkidle), then `content()`.
3. Interact with `click`/`fill`/`select` as needed; re-`content()` after each step
   to confirm the effect (don't assume a click worked).
4. `screenshot` to capture visual state when the user needs to see it.
5. `status` is a cheap sanity check between steps.

## Pitfalls

- **Selectors:** use stable CSS selectors (`#id`, `a[href="..."]`, `input[name=...]`),
  not brittle nth-child chains. If a click fails, `content()` to see the real page
  state, then adjust the selector.
- **State persists** — if a step fails, `status()` tells you where you actually are;
  don't re-`goto` blindly.
- **Never** type passwords, credit cards, or other secrets into a form via the
  browser tool.
- **Screenshots** are saved to `~/.cache/ai/browser_shot.png` by default; pass
  `path` to save elsewhere.
- The daemon logs every action to `~/.cache/ai/browser_actions.log` for audit.
- If the daemon is dead, the next call auto-restarts it (it may take a few seconds
  for Playwright/Chromium to boot).
