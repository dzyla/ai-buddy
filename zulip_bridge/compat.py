"""Flat legacy namespace: what `import zulip_ai_bridge` returns.

Every name of every zulip_bridge submodule is re-exported here, so code and tests written against
the old single-file zulip_ai_bridge.py keep working (`zulip_ai_bridge.run_ai_subprocess`,
`.FileParser`, `.main`, `.requests`, ...). Rebinding a name on this module (e.g.
`patch("zulip_ai_bridge.run_ai_subprocess", ...)` or `monkeypatch.setattr(zab, "MAX_FILE_SIZE", n)`)
is forwarded to every submodule that holds the same object under that name, so the moved code sees
the new value, exactly as it did when everything lived in one module's globals. Same mechanism as
aitools/compat.py.
"""
import importlib
import sys
import types
# The old single-file module exposed these at top level (patched as e.g. zulip_ai_bridge.time).
import os, re, csv, io, json, time, signal, shutil, logging, tempfile, threading, subprocess  # noqa: E401,F401
import requests  # noqa: F401
from urllib.parse import unquote, quote  # noqa: F401

_NAMES = ("text", "config", "owner", "files", "runner", "memory", "context", "handler", "bridge", "cli")
# Bound as a tuple only: binding the modules by name would shadow same-named attributes.
SUBMODULES = tuple(importlib.import_module(f"{__package__}.{n}") for n in _NAMES)

for _mod in SUBMODULES:
    for _name, _value in vars(_mod).items():
        if not _name.startswith("__"):
            globals().setdefault(_name, _value)
del _mod, _name, _value

_MISSING = object()


class _LegacyModule(types.ModuleType):
    def _forward(self, name, value, delete=False):
        current = self.__dict__.get(name, _MISSING)
        if current is _MISSING or name in ("SUBMODULES", "_NAMES"):
            return
        for mod in self.__dict__["SUBMODULES"]:
            if mod.__dict__.get(name, _MISSING) is current:
                if delete:
                    delattr(mod, name)
                else:
                    setattr(mod, name, value)

    def __setattr__(self, name, value):
        self._forward(name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name):
        self._forward(name, None, delete=True)
        super().__delattr__(name)


sys.modules[__name__].__class__ = _LegacyModule
