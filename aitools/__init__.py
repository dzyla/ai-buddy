"""aitools — the tool backend behind `ai_mcp.py` (native tools, MCP client, CLI subcommands).

Module map: see CLAUDE.md ("ai_mcp.py / aitools — the tool backend"). Entry points:
  aitools.cli.main(argv) -> int                     the `ai_mcp.py <subcommand>` CLI
  aitools.registry.TOOL_SCHEMAS                     native tool schemas (list-tools order)
  aitools.registry.dispatch(server, tool, args)     run one tool, return what call-tool prints
  aitools.compat                                    flat legacy namespace returned by `import ai_mcp`
"""
import os

# Absolute path of the ai_mcp.py entry script; set by the shim. Background helpers
# (run-scheduler, browser-daemon) re-spawn `python3 <entry script> <subcommand>`.
ENTRY_SCRIPT = None


def entry_script():
    if ENTRY_SCRIPT:
        return ENTRY_SCRIPT
    beside = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai_mcp.py")
    if os.path.exists(beside):
        return beside
    return os.path.expanduser("~/.local/bin/ai_mcp.py")
