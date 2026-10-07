#!/usr/bin/env python3
"""
Robinhood Agentic Trading & Market Hours Analysis Engine
=========================================================
Backwards-compatible entry point. The code lives in the `trader/` package; this shim keeps
`python3 robinhood_trader.py <command> ...` and `import robinhood_trader` working.

Importing this module returns `trader.legacy`, a flat namespace re-exporting every name the old
single-file module had (MomentumStrategy, RobinhoodExecutor, MarketHours, load_trader_config,
_momentum_rebalance_pulse, ...). Rebinding a name on it reaches the submodules.

Package lookup order: the directory holding this file (a checkout or a deployed release under
~/.local/share/ai-trader/current), then the installed copy in ~/.local/share/ai/lib.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.expanduser("~/.local/share/ai/lib")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if _LIB not in sys.path:
    sys.path.append(_LIB)

if __name__ == "__main__":
    from trader.cli import main
    sys.exit(main())
else:
    import trader.legacy as _legacy
    sys.modules[__name__] = _legacy
