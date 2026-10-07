"""Environment file, zuliprc and `ai` binary location."""
import os
import logging

import zulip_bridge


logger = logging.getLogger("ZulipBridge")


def load_env_file():
    """Load environment variables from ~/.local/share/ai/env."""
    env_file = os.path.expanduser("~/.local/share/ai/env")
    loaded = {}
    if os.path.exists(env_file):
        try:
            with open(env_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("export "):
                        line = line[7:].strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        loaded[k] = v
        except Exception as e:
            logger.warning(f"Could not load env file {env_file}: {e}")
    return loaded


def apply_env_file_defaults():
    """Fill os.environ from ~/.local/share/ai/env without overriding.

    The env file is written by ai-backend as `export KEY="value"` lines, which
    systemd's EnvironmentFile= silently ignores, so the bridge loads it itself
    (like the `ai` binary does). Variables already set in the process
    environment win. Returns the list of keys that were applied.
    """
    applied = []
    for k, v in load_env_file().items():
        if k and k not in os.environ:
            os.environ[k] = v
            applied.append(k)
    return applied


def _entry_script():
    """The zulip_ai_bridge.py the shim was started from (its directory holds a checkout's `ai`)."""
    return zulip_bridge.ENTRY_SCRIPT or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "zulip_ai_bridge.py")


def resolve_ai_bin():
    """Locate the ai binary: $INFER_BIN_PATH, ./ai next to this script,
    ~/.local/bin/ai, else plain "ai" (resolved through PATH at run time)."""
    if "INFER_BIN_PATH" in os.environ and os.path.isfile(os.environ["INFER_BIN_PATH"]):
        return os.environ["INFER_BIN_PATH"]
    repo_bin = os.path.join(os.path.dirname(_entry_script()), "ai")
    if os.path.isfile(repo_bin) and os.access(repo_bin, os.X_OK):
        return repo_bin
    local_bin = os.path.expanduser("~/.local/bin/ai")
    if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
        return local_bin
    return "ai"


def zuliprc_path():
    """The config file the zulip library reads: $ZULIP_CONFIG, else ~/.zuliprc."""
    cfg = os.environ.get("ZULIP_CONFIG")
    if cfg:
        return os.path.abspath(os.path.expanduser(cfg))
    home = os.environ.get("HOME")
    return os.path.join(home, ".zuliprc") if home else None
