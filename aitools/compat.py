"""Flat legacy namespace: what `import ai_mcp` returns.

Every name of every aitools submodule (private helpers included) is re-exported here, so code and
tests written against the old single-file ai_mcp.py keep working (`ai_mcp.fetch_webpage`,
`ai_mcp.MEMORY_PATH`, `ai_mcp._lesson_blocks`, `ai_mcp.main`, ...). Rebinding a name on this module
(e.g. `monkeypatch.setattr(ai_mcp, "MEMORY_PATH", p)`) is forwarded to every submodule that holds
the same object under that name, so the moved code sees the new value, exactly as it did when
everything lived in one module's globals. Same mechanism as trader/legacy.py.
"""
import importlib
import sys
import types
# The old single-file module exposed these at top level; keep them reachable (e.g. ai_mcp.subprocess).
import os, signal, json, subprocess, re, time, io, sqlite3  # noqa: E401,F401
import urllib.request, urllib.parse, urllib.error  # noqa: E401,F401
import xml.etree.ElementTree as ET  # noqa: F401

_NAMES = ("mcp_client", "metrics", "memory", "shell", "misc", "research", "web", "history", "files",
          "browser", "render", "scheduler", "selfimprove", "sessions", "schemas_part1", "schemas_part2",
          "handlers", "calendar_tools", "registry", "cli")
# Bound as a tuple only: binding the modules by name would shadow same-named functions (browser).
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
