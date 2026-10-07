"""Robinhood momentum trader, packaged so the live daemon can run from a versioned release.

Modules: config (paths, trader.json), market_hours, data, indicators, advisor, strategy, broker, risk,
journal, portfolio, daemon (live loops and pulses), shadow (read-only what-if pulse), summary (daily
report), cli (`robinhood_trader.py <command>`). `trader.legacy` is the flat namespace that
`import robinhood_trader` returns. Deploy with trader/deploy.sh (see docs/robinhood_trader.md).
"""
