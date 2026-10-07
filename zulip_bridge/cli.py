"""`--check` / `doctor` validation and the program entry point."""

import os
import sys
import shutil
import logging

from . import zulip
from .bridge import ZulipAiBridge
from .config import apply_env_file_defaults, resolve_ai_bin, zuliprc_path
from .owner import describe_owner_value, detect_owner, lookup_bot_owner

logger = logging.getLogger("ZulipBridge")



def run_check(online=False, client_factory=None, env_applied=None, out=print):
    """Validate the bridge configuration without starting the listener.

    Offline by default: nothing is sent to the Zulip server. With
    online=True it also calls get_profile and resolves the owner exactly as
    the running bridge would. Returns a process exit code (0 = no problems).
    """
    problems = 0
    env_applied = set(env_applied or ())

    def ok(m):
        out(f"[ OK ] {m}")

    def info(m):
        out(f"[INFO] {m}")

    def warn(m):
        out(f"[WARN] {m}")

    def fail(m):
        nonlocal problems
        problems += 1
        out(f"[FAIL] {m}")

    info(f"Python interpreter: {sys.executable} ({sys.version.split()[0]})")

    env_file = os.path.expanduser("~/.local/share/ai/env")
    if os.path.isfile(env_file):
        ok(f"Env file found: {env_file}")
    else:
        info(f"No env file at {env_file} (optional)")

    # 1. zulip package
    pkg_ok = getattr(zulip, "Client", None) is not None
    if pkg_ok:
        ok(f"zulip package importable (version {getattr(zulip, '__version__', 'unknown')})")
    else:
        fail(f"zulip package not importable by this interpreter. Install it with: "
             f"{sys.executable} -m pip install zulip")

    # 2. credentials: same lookup order as zulip.Client()
    env_creds = [k for k in ("ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_SITE") if os.environ.get(k)]
    cfg = zuliprc_path()
    cfg_ok = False
    if cfg and os.path.isfile(cfg):
        if not os.access(cfg, os.R_OK):
            fail(f"{cfg} exists but is not readable by this user")
        else:
            mode = os.stat(cfg).st_mode & 0o777
            if mode & 0o077:
                warn(f"{cfg} has permissions {oct(mode)}; it holds an API key. Run: chmod 600 {cfg}")
            else:
                ok(f"zuliprc found: {cfg} (permissions {oct(mode)})")
            import configparser
            parser = configparser.ConfigParser()
            try:
                with open(cfg) as f:
                    parser.read_file(f)
                missing = [k for k in ("email", "key", "site")
                           if not (parser.has_option("api", k) and parser.get("api", k).strip())
                           and f"ZULIP_{'API_KEY' if k == 'key' else k.upper()}" not in env_creds]
                if missing:
                    fail(f"{cfg}: missing {', '.join(missing)} in the [api] section")
                elif "REPLACE_WITH" in parser.get("api", "key", fallback="") \
                        and "ZULIP_API_KEY" not in env_creds:
                    fail(f"Zulip not configured: {cfg} still has the template placeholder key "
                         f"(REPLACE_WITH_...). Download the bot's zuliprc and save it there.")
                else:
                    cfg_ok = True
                    email = os.environ.get("ZULIP_EMAIL") or parser.get("api", "email", fallback="")
                    site = os.environ.get("ZULIP_SITE") or parser.get("api", "site", fallback="")
                    ok(f"Credentials: bot email {email} on {site}")
            except Exception as e:
                fail(f"Could not parse {cfg}: {e}")
    elif "ZULIP_EMAIL" in env_creds and "ZULIP_API_KEY" in env_creds:
        if "ZULIP_SITE" in env_creds:
            cfg_ok = True
            ok(f"Credentials from environment: {os.environ['ZULIP_EMAIL']} on {os.environ['ZULIP_SITE']}")
        else:
            fail("ZULIP_EMAIL/ZULIP_API_KEY are set but ZULIP_SITE is missing")
    else:
        fail(f"No Zulip credentials: {cfg or '~/.zuliprc'} does not exist. Download the bot's "
             f"zuliprc and save it there (chmod 600), or set ZULIP_CONFIG=/path/to/zuliprc.")
        alt = os.path.expanduser("~/.config/zulip/zuliprc")
        if os.path.isfile(alt):
            info(f"Found {alt}: only the MCP server/reminders look there. For the bridge, "
                 f"set ZULIP_CONFIG={alt} or copy it to ~/.zuliprc.")
    if "ZULIP_API_KEY" in env_creds:
        warn("ZULIP_API_KEY is set in the environment; it gets copied into scheduled-task files "
             "(the scheduled_tasks table of ~/.local/share/ai/state.db). Prefer keeping the key only in the zuliprc file.")

    # 3. construct the client (no network traffic)
    client = None
    if pkg_ok and cfg_ok:
        try:
            client = (client_factory or zulip.Client)()
            ok(f"Client constructed: {getattr(client, 'email', '?')} -> {getattr(client, 'base_url', '?')}")
        except Exception as e:
            fail(f"Could not construct zulip.Client(): {e}")

    # 4. owner
    zuser = (os.environ.get("ZULIP_USER") or "").strip()
    if zuser:
        where = " (from ~/.local/share/ai/env)" if "ZULIP_USER" in env_applied else ""
        ok(f"ZULIP_USER={zuser}{where}: {describe_owner_value(zuser)}")
    else:
        info("ZULIP_USER not set: the owner is auto-detected at startup "
             "(bot_owner_id, else a single unique DM partner)")

    if not online:
        if zuser:
            ok(f"Effective owner: {zuser} (from ZULIP_USER)")
        else:
            info("Effective owner: resolved at startup; run with --online to see it")
        info("Offline check: the server was not contacted. Add --online to verify "
             "the API key and resolve the owner.")
    elif client is not None:
        try:
            prof = client.get_profile()
        except Exception as e:
            prof = {"result": "error", "msg": str(e)}
        if not isinstance(prof, dict) or prof.get("result") != "success":
            msg = prof.get("msg") if isinstance(prof, dict) else prof
            fail(f"get_profile failed: {msg} (wrong API key/site, deactivated bot, or no network)")
        else:
            bot_uid = prof.get("user_id")
            bot_email = prof.get("email") or getattr(client, "email", None)
            ok(f"Authenticated as {prof.get('full_name')} <{bot_email}> (user_id {bot_uid}, "
               f"is_bot={prof.get('is_bot')})")
            if prof.get("is_bot") is False:
                warn("This is a regular user account, not a bot: bot_owner_id is unavailable, and "
                     "messages you send from this same account are ignored. Use a separate bot.")
            owner_id = owner_email = None
            try:
                owner_id, owner_email = lookup_bot_owner(client, bot_uid)
            except Exception as e:
                warn(f"Could not look up bot_owner_id: {e}")
            if owner_id:
                ok(f"bot_owner_id={owner_id}, owner email as the bot sees it: {owner_email}")
            if zuser:
                eff, src = zuser, "ZULIP_USER"
            else:
                eff, src = detect_owner(client, bot_email, bot_uid, log=lambda m: None)
            if eff:
                ok(f"Effective owner: {eff} (from {src})")
                zl = zuser.lower()
                if zuser and owner_id and zl.isdigit() and zl != str(owner_id):
                    warn(f"ZULIP_USER ({zuser}) is not the bot owner's id ({owner_id}); ZULIP_USER wins")
                elif zuser and owner_email and "@" in zl and zl != owner_email.lower():
                    warn(f"ZULIP_USER ({zuser}) differs from the bot owner's email as the bot sees it "
                         f"({owner_email}); ZULIP_USER wins. If you meant yourself, use "
                         f"ZULIP_USER={owner_id or 'your numeric user id'}.")
            else:
                fail("No owner could be resolved: the bridge will ignore EVERY message. "
                     "Set ZULIP_USER to your numeric user id or email.")
    else:
        info("Skipping online checks: no client could be constructed")

    # 5. ai binary
    ai_bin = resolve_ai_bin()
    if ai_bin == "ai":
        found = shutil.which("ai")
        if found:
            ok(f"ai binary: {found} (via PATH)")
        else:
            fail("ai binary not found (checked $INFER_BIN_PATH, ./ai, ~/.local/bin/ai, PATH). "
                 "Run ./install.sh or set INFER_BIN_PATH.")
    else:
        ok(f"ai binary: {ai_bin}")

    out("")
    if problems:
        out(f"{problems} problem(s) found.")
        return 1
    out("All checks passed." + ("" if online else " (offline; add --online to test the server)"))
    return 0


def main(argv=None):
    import argparse
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("doctor", "check"):
        argv[0] = "--check"
    parser = argparse.ArgumentParser(
        description="Zulip <-> local `ai` agent bridge. Without options, starts the listener.")
    parser.add_argument("--check", "--doctor", action="store_true",
                        help="validate the configuration and exit (does not start the listener)")
    parser.add_argument("--online", action="store_true",
                        help="with --check: also contact the Zulip server (get_profile, owner lookup)")
    args = parser.parse_args(argv)

    applied = apply_env_file_defaults()
    if args.check or args.online:
        return run_check(online=args.online, env_applied=applied)
    try:
        bridge = ZulipAiBridge()
        bridge.run()
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    except Exception as e:
        print(f"Error starting bridge: {e}")
        return 1
    return 0
