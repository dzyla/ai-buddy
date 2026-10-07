#!/usr/bin/env python3
"""ai_mcp.py — tool backend entry point for the `ai` binary (shim over the `aitools/` package).

Keeps `python3 ai_mcp.py <subcommand> ...` (list-tools, call-tool, render-markdown, trim-messages,
session-transcript, run-scheduler, ...) and `import ai_mcp` working unchanged. Importing this module
returns `aitools.compat`, a flat namespace re-exporting every name the old single-file module had;
rebinding a name on it (monkeypatch) reaches the submodules.

Package lookup order: the directory holding this file's realpath (a repo checkout, also when this
file is symlinked elsewhere), then the installed copy in ~/.local/share/ai/lib (install.sh copies
this file to ~/.local/bin, where no package sits next to it).
"""
import os
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
_LIB = os.path.expanduser("~/.local/share/ai/lib")
if sys.path[:1] != [_HERE]:
    sys.path[:] = [p for p in sys.path if p != _HERE]
    sys.path.insert(0, _HERE)
if _LIB not in sys.path:
    sys.path.append(_LIB)

import aitools  # noqa: E402

aitools.ENTRY_SCRIPT = os.path.abspath(__file__)

if __name__ == "__main__":
    from aitools.cli import main
    sys.exit(main())
else:
    import aitools.compat as _compat
    sys.modules[__name__] = _compat
