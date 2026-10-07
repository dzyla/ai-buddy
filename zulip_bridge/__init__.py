"""zulip_bridge — the Zulip <-> local `ai` agent bridge behind `zulip_ai_bridge.py`.

Module map (see CLAUDE.md):
  config   load_env_file / apply_env_file_defaults / zuliprc_path / resolve_ai_bin
  owner    lookup_bot_owner / detect_owner / describe_owner_value
  files    FileParser (upload download + text extraction), _safe_upload_name
  text     clean_response / normalize_text
  runner   run_ai_subprocess / _kill_process_group / extract_session_fallback
  memory   ZulipMemoryManager (audit trail)
  context  ContextWindowManager
  handler  MessageHandlerMixin (_process_message, handle_message)
  bridge   ZulipAiBridge (listener, replies, dispatch)
  cli      run_check / main
  compat   flat legacy namespace returned by `import zulip_ai_bridge`
"""
import logging
import sys
import types

try:
    import zulip
except ImportError:
    zulip = types.ModuleType("zulip")
    zulip.Client = None
    sys.modules["zulip"] = zulip

# Configure logging for truncation events
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Absolute path of the zulip_ai_bridge.py entry script; set by the shim. resolve_ai_bin looks
# for the `ai` binary next to it.
ENTRY_SCRIPT = None
