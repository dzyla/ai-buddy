#!/usr/bin/env python3
"""zulip_ai_bridge.py — entry point for the Zulip <-> local `ai` bridge (shim over `zulip_bridge/`).

Keeps `python3 zulip_ai_bridge.py [--check [--online]]` (also the systemd ExecStart) and
`import zulip_ai_bridge` working unchanged. Importing this module returns `zulip_bridge.compat`, a
flat namespace re-exporting every name the old single-file module had; rebinding a name on it
(monkeypatch / mock.patch) reaches the submodules.

Package lookup order: the directory holding this file's realpath (a repo checkout), then the
installed copy in ~/.local/share/ai/lib (install.sh copies this file to ~/.local/bin, where no
package sits next to it).
"""
import os
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
_LIB = os.path.expanduser("~/.local/share/ai/lib")
if sys.path[:1] != [_HERE]:
    sys.path[:] = [p for p in sys.path if p != _HERE]
    sys.path.insert(0, _HERE)
# The installed lib goes right after this file's dir: appended, a same-named distribution in
# site-packages (PyPI has an `aitools`) would shadow it.
if _LIB != _HERE:
    if _LIB in sys.path:
        sys.path.remove(_LIB)
    sys.path.insert(sys.path.index(_HERE) + 1, _LIB)

import zulip_bridge  # noqa: E402

zulip_bridge.ENTRY_SCRIPT = os.path.abspath(__file__)

if __name__ == "__main__":
    from zulip_bridge.cli import main
    sys.exit(main())
else:
    import zulip_bridge.compat as _compat
    sys.modules[__name__] = _compat
