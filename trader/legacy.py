"""Flat namespace of the trader package: what `import robinhood_trader` returns.

Every name of every submodule (private helpers included) is re-exported here, so code and tests written
against the old single-file module keep working. Rebinding a name on this module (e.g.
`monkeypatch.setattr(robinhood_trader, "RISK_MONITOR_FLAGS", tmp)`) is forwarded to every submodule
that holds the same object under that name, so the code paths in the submodules see the new value,
exactly as they did when everything lived in one module's globals.
"""
import sys
import types
# The old single-file module exposed these at top level; keep them reachable (e.g. robinhood_trader.time).
import os, json, time, math, subprocess, re, datetime, csv, shutil  # noqa: E401,F401
import urllib.request, urllib.parse, urllib.error  # noqa: E401,F401
from typing import List, Dict, Any, Optional, Tuple  # noqa: F401

from . import (config, journal, market_hours, data, indicators, advisor, strategy, broker, risk,
               portfolio, daemon, shadow, summary, cli)

SUBMODULES = (config, journal, market_hours, data, indicators, advisor, strategy, broker, risk,
              portfolio, daemon, shadow, summary, cli)

for _mod in SUBMODULES:
    for _name, _value in vars(_mod).items():
        if not _name.startswith("__"):
            globals().setdefault(_name, _value)
del _mod, _name, _value

_MISSING = object()


class _LegacyModule(types.ModuleType):
    def _forward(self, name, value, delete=False):
        current = self.__dict__.get(name, _MISSING)
        if current is _MISSING or name in ("SUBMODULES",):
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
