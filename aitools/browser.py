"""computer_control and the persistent Playwright browser daemon behind the browser tool."""
import sys
import os
import json
import subprocess
import time
from . import entry_script as _entry_script

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

# ── Browser automation (headless Chromium via a persistent Playwright daemon) ─
# Each browser_* call talks to a small long-lived daemon over a unix socket, so
# page state (URL, login, JS context) survives across tool calls. The daemon
# auto-starts, idles out after BROWSER_IDLE_TIMEOUT seconds, and every page
# action is logged to the session transcript path for auditability.

BROWSER_SOCKET = os.path.expanduser("~/.cache/ai/browser.sock")
BROWSER_LOG = os.path.expanduser("~/.cache/ai/browser_actions.log")
BROWSER_IDLE_TIMEOUT = 600

def _browser_log(line):
    try:
        os.makedirs(os.path.dirname(BROWSER_LOG), exist_ok=True)
        with open(BROWSER_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass

def _browser_daemon_main():
    """Runs inside the daemon process: serve JSON commands over the socket."""
    import socket as _socket
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        _browser_log("DAEMON FATAL: playwright import failed: %s" % e)
        return
    os.makedirs(os.path.dirname(BROWSER_SOCKET), exist_ok=True)
    try:
        os.unlink(BROWSER_SOCKET)
    except OSError:
        pass
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(BROWSER_SOCKET)
    srv.listen(4)
    srv.settimeout(15)
    idle_since = time.time()
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(user_agent=("Mozilla/5.0 (X11; Linux x86_64) "
                                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                                              "Chrome/126.0.0.0 Safari/537.36"))
    page = context.new_page()
    last_action = None
    try:
        while True:
            try:
                conn, _ = srv.accept()
            except _socket.timeout:
                if time.time() - idle_since > BROWSER_IDLE_TIMEOUT:
                    break
                continue
            conn.settimeout(90)
            try:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                if not data:
                    conn.close()
                    continue
                req = json.loads(data.decode("utf-8", "replace").strip())
                idle_since = time.time()
                action = req.get("action")
                last_action = action
                res = {}
                if action == "goto":
                    page.goto(req.get("url", "about:blank"), timeout=int(req.get("timeout_ms", 30000)))
                    res = {"url": page.url, "title": page.title()}
                elif action == "back":
                    page.go_back()
                    res = {"url": page.url, "title": page.title()}
                elif action == "content":
                    res = {"title": page.title(), "url": page.url,
                           "text": page.evaluate("() => document.body ? document.body.innerText : ''")[:int(req.get("max_chars", 20000))]}
                elif action == "html":
                    res = {"html": page.content()[:int(req.get("max_chars", 100000))]}
                elif action == "click":
                    page.click(req.get("selector", ""), timeout=int(req.get("timeout_ms", 15000)))
                    res = {"url": page.url, "title": page.title()}
                elif action == "fill":
                    page.fill(req.get("selector", ""), str(req.get("value", "")), timeout=int(req.get("timeout_ms", 15000)))
                    res = {"ok": True}
                elif action == "select":
                    page.select_option(req.get("selector", ""), req.get("value", ""), timeout=int(req.get("timeout_ms", 15000)))
                    res = {"ok": True}
                elif action == "press":
                    page.keyboard.press(req.get("key", "Enter"))
                    res = {"ok": True}
                elif action == "js":
                    res = {"result": page.evaluate("() => (%s)" % req.get("expr", "1"))}
                elif action == "wait":
                    if req.get("selector"):
                        page.wait_for_selector(req["selector"], timeout=int(req.get("timeout_ms", 30000)))
                    else:
                        page.wait_for_load_state("networkidle", timeout=int(req.get("timeout_ms", 30000)))
                    res = {"ok": True, "url": page.url}
                elif action == "screenshot":
                    pth = req.get("path") or os.path.join(os.path.expanduser("~"), ".cache", "ai", "browser_shot.png")
                    pth = os.path.expanduser(pth)
                    os.makedirs(os.path.dirname(pth), exist_ok=True)
                    page.screenshot(path=pth)
                    res = {"path": pth}
                elif action == "links":
                    items = page.evaluate("""() => Array.from(document.querySelectorAll('a[href]'))
                        .slice(0, 200).map(a => ({text: (a.innerText||'').trim().slice(0,80), href: a.href}))
                        .filter(x => x.href && !x.href.startsWith('javascript:'))""")
                    res = {"links": items}
                elif action == "status":
                    res = {"alive": True, "url": page.url, "title": page.title(),
                           "idle_seconds": int(time.time() - idle_since)}
                elif action == "shutdown":
                    res = {"bye": True}
                    conn.sendall((json.dumps(res) + "\n").encode())
                    break
                else:
                    res = {"error": "unknown action %r" % action}
                conn.sendall((json.dumps(res, default=str) + "\n").encode())
            except Exception as e:
                try:
                    conn.sendall((json.dumps({"error": str(e)[:500]}) + "\n").encode())
                except Exception:
                    pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    finally:
        try:
            browser.close()
            pw.stop()
        except Exception:
            pass
        try:
            os.unlink(BROWSER_SOCKET)
        except OSError:
            pass
        _browser_log("DAEMON EXIT last_action=%s" % last_action)

def _browser_ensure_daemon():
    """Start the daemon if the socket is not answering."""
    import socket as _socket
    if os.path.exists(BROWSER_SOCKET):
        try:
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(BROWSER_SOCKET)
            s.close()
            return True
        except OSError:
            try:
                os.unlink(BROWSER_SOCKET)
            except OSError:
                pass
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    logf = open(os.devnull, "w")
    subprocess.Popen([sys.executable, _entry_script(), "browser-daemon"],
                     env=env, stdout=logf, stderr=logf,
                     start_new_session=True)
    for _ in range(40):  # up to 20s for playwright to boot
        time.sleep(0.5)
        if os.path.exists(BROWSER_SOCKET):
            try:
                s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect(BROWSER_SOCKET)
                s.close()
                return True
            except OSError:
                continue
    return False

def _browser_command(action, **kw):
    """Send one command to the browser daemon. ALWAYS returns a dict
    (with an 'error' key on any failure) — callers never see a string."""
    import socket as _socket
    if not _browser_ensure_daemon():
        return {"error": "could not start the browser daemon (playwright + chromium required: `playwright install chromium`)"}
    req = {"action": action}
    req.update(kw)
    s = None
    try:
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        s.settimeout(120)
        s.connect(BROWSER_SOCKET)
        s.sendall((json.dumps(req) + "\n").encode())
        data = b""
        while b"\n" not in data:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        _browser_log("CMD %s %s" % (action, json.dumps({k: v for k, v in kw.items() if k != 'expr'})[:200]))
        res = json.loads(data.decode("utf-8", "replace").strip() or "{}")
        if not isinstance(res, dict):
            res = {"error": "malformed daemon response"}
    except Exception as e:
        res = {"error": str(e)[:300]}
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
    return res

class _BrowserError(Exception):
    """Raised when a browser daemon command fails; browser() turns it into
    a model-visible 'Error: ...' string."""

def _browser_checked(action, **kw):
    """Send a command, return its result dict, raise _BrowserError on failure."""
    r = _browser_command(action, **kw)
    if r.get("error"):
        raise _BrowserError("browser %s failed: %s" % (action, r["error"]))
    return r

def browser(action, url=None, selector=None, value=None, key=None, expr=None,
            wait_ms=None, max_chars=None, path=None):
    """Drive a persistent headless Chromium browser. Actions:
      goto(url)          — navigate (state persists across calls)
      content()          — visible text of the current page (for JS-rendered pages)
      html()             — raw HTML of the current page
      links()            — up to 200 {text, href} of the current page
      click(selector)    — click an element (CSS selector)
      fill(selector, value) — type into an input/textarea
      select(selector, value) — pick a dropdown option
      press(key)         — press a key (Enter, Tab, Escape, ...)
      js(expr)           — evaluate a JS expression, return the result
      wait(selector?)    — wait for a selector or networkidle
      back()             — go back
      screenshot(path?)  — save a PNG of the page
      status()           — current url/title/idle time
      shutdown           — stop the daemon
    Use for JS-only sites, forms, and anything fetch_webpage can't read.
    For a plain page, fetch_webpage is faster — use browser for interactivity."""
    try:
        if action == "goto":
            if not url:
                return "Error: goto needs a url."
            r = _browser_checked("goto", url=url, timeout_ms=wait_ms or 30000)
            return "Navigated to %s\nTitle: %s" % (r.get("url"), r.get("title"))
        if action == "content":
            r = _browser_checked("content", max_chars=max_chars or 20000)
            return ("[page: %s]\n%s" % (r.get("title"), r.get("text", "")))
        if action == "html":
            r = _browser_checked("html", max_chars=max_chars or 100000)
            return r.get("html", "")
        if action == "links":
            r = _browser_checked("links")
            if not r.get("links"):
                return "No links found on the page."
            return "\n".join("%s -> %s" % (l.get("text") or "(no text)", l.get("href"))
                             for l in r["links"][:150])
        if action == "click":
            if not selector:
                return "Error: click needs a selector."
            r = _browser_checked("click", selector=selector, timeout_ms=wait_ms or 15000)
            return "Clicked %s (now on %s)" % (selector, r.get("url"))
        if action == "fill":
            if not selector:
                return "Error: fill needs a selector."
            _browser_checked("fill", selector=selector, value=value or "")
            return "Filled %s" % selector
        if action == "select":
            if not selector:
                return "Error: select needs a selector."
            _browser_checked("select", selector=selector, value=value or "")
            return "Selected %s in %s" % (value, selector)
        if action == "press":
            _browser_checked("press", key=key or "Enter")
            return "Pressed %s" % (key or "Enter")
        if action == "js":
            if not expr:
                return "Error: js needs an expr."
            r = _browser_checked("js", expr=expr)
            return json.dumps(r.get("result"), default=str)
        if action == "wait":
            r = _browser_checked("wait", selector=selector, timeout_ms=wait_ms or 30000)
            return "Waited (now on %s)" % r.get("url")
        if action == "back":
            r = _browser_checked("back")
            return "Back (now on %s)" % r.get("url")
        if action == "screenshot":
            r = _browser_checked("screenshot", path=path)
            return "[IMAGE_DATA_SUCCESS:%s] Screenshot saved." % r.get("path")
        if action == "status":
            r = _browser_checked("status")
            return "url: %s\ntitle: %s\nidle: %ss" % (r.get("url"), r.get("title"), r.get("idle_seconds"))
        if action == "shutdown":
            _browser_checked("shutdown")
            return "Browser daemon stopped."
        return "Error: unknown browser action '%s'. Use: goto, content, html, links, click, fill, select, press, js, wait, back, screenshot, status, shutdown." % action
    except _BrowserError as e:
        return "Error: %s" % e
    except Exception as e:
        return "Error in browser: %s" % e
