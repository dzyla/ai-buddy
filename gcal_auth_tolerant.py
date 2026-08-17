#!/usr/bin/env python3
"""Tolerant Google Calendar auth flow.

Unlike gcal.py's use of flow.run_local_server (which dies on ANY stray or
mismatched callback), this server keeps serving:

- a callback with the right state  -> token is fetched and saved
- a callback from an old/wrong tab -> page explains, callback is logged,
  and the server keeps waiting for the correct tab

Run: python3 -u gcal_auth_tolerant.py
Then approve in the MOST RECENTLY opened browser tab.
"""
import os
import sys
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CRED = "/home/dzyla/.config/ai/gcal_credentials.json"
TOKEN = "/home/dzyla/.config/ai/gcal_token.json"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path
        q = parse_qs(path.split("?", 1)[1] if "?" in path else "")
        got = {k: v[0] for k, v in q.items()}
        ctx = self.server.ctx
        if got.get("state") == ctx["state"] and "code" in got:
            ctx["result_path"] = path  # mirror _RedirectWSGIApp.last_request_uri
            body = (b"Authentication complete - close this tab and return to "
                     b"the AI session.\n")
        elif "error" in got:
            ctx["got_error"] = got.get("error", "?")
            body = ("Google returned error: %s\n" % got.get("error", "?")).encode()
        else:
            sys.stderr.write(
                "stray callback: got state=%r expected=%r\n" % (got.get("state"), ctx["state"])
            )
            body = (b"This tab has an expired/wrong state (from an earlier "
                     b"attempt). Open the most recently opened browser tab "
                     b"and approve there.\n")
        self.send_response(200)
        self.wfile.write(body)

    def log_message(self, *args):  # silence per-request logging
        pass


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CRED, SCOPES)

    # Bind first so we know the real port to use as redirect_uri.
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.socket.getsockname()[1]

    flow.redirect_uri = "http://localhost:%d/" % port
    auth_url, state = flow.authorization_url()

    srv.ctx = {"state": state, "port": port}

    sys.stderr.write("port: %d\n" % port)
    sys.stderr.write("state: %s\n" % state)
    sys.stderr.write("url: %s\n" % auth_url)
    sys.stderr.flush()

    webbrowser.get(None).open(auth_url, new=1, autoraise=True)

    t = threading.Thread(target=srv.serve_forever, args=(0.5,), daemon=True)
    t.start()

    deadline = time.time() + 600
    while time.time() < deadline:
        if srv.ctx.get("result_path") or srv.ctx.get("got_error"):
            break
        time.sleep(0.3)

    srv.shutdown()
    t.join(timeout=5)

    if srv.ctx.get("got_error"):
        print("Google returned error: %s" % srv.ctx["got_error"], file=sys.stderr)
        sys.exit(1)

    if not srv.ctx.get("result_path"):
        print("Timed out waiting for authorization.", file=sys.stderr)
        sys.exit(1)

    # Mirror run_local_server exactly (oauthlib insists on the https scheme).
    flow.fetch_token(
        authorization_response=srv.ctx["result_path"].replace("http", "https"),
        audience=None,
    )
    creds = flow.credentials
    os.makedirs(os.path.dirname(TOKEN), exist_ok=True)
    with open(TOKEN, "w") as token_file:
        token_file.write(creds.to_json())
    print("Token saved to %s" % TOKEN, file=sys.stderr)


if __name__ == "__main__":
    main()
