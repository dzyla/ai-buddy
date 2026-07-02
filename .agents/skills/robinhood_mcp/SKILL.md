---
name: robinhood-mcp
description: CRITICAL — when the user asks to buy or sell stocks, rebalance a portfolio, or write stock trading agent loops: Guidelines for using Robinhood's official Model Context Protocol (MCP) servers, sandbox accounts, and strict financial safety limits.
---

# Robinhood Stock Trading & Portfolio Management

Use this skill when the user requests to trade equities, query portfolio holdings, fetch real-time market quotes, or implement algorithmic trading loops.

---

## 1. Official Robinhood Agentic Accounts (2026 Support)

Robinhood supports official Model Context Protocol (MCP) integrations for automated agentic trading.
*   **Security Principle**: All trading actions must run against dedicated **Agentic Sandbox Accounts** that are completely separate from the user's primary portfolio.
*   **Safety Limits**: Never execute trades that exceed the explicitly allocated sandbox budget.

---

## 2. Typical API / MCP Tool Usage Patterns

Below is a template for calculating targets and generating execute payloads in Python:

```python
import json

def calculate_rebalance(portfolio, target_allocations):
    total_val = portfolio["cash"]
    for ticker, info in portfolio["holdings"].items():
        total_val += info["shares"] * info["price"]
        
    trades = []
    for ticker, pct in target_allocations.items():
        target_val = total_val * pct
        current_val = portfolio["holdings"].get(ticker, {}).get("shares", 0) * portfolio["holdings"].get(ticker, {}).get("price", 0)
        diff = target_val - current_val
        price = portfolio["holdings"].get(ticker, {}).get("price", 1.0)
        shares = int(round(diff / price))
        if shares != 0:
            action = "BUY" if shares > 0 else "SELL"
            trades.append({"ticker": ticker, "action": action, "shares": abs(shares)})
            
    return trades

# Example portfolio
portfolio = {
    "cash": 1000.0,
    "holdings": {
        "AAPL": {"shares": 10, "price": 200.0},
        "MSFT": {"shares": 5, "price": 400.0}
    }
}
# Target: 50% AAPL, 50% MSFT
print(calculate_rebalance(portfolio, {"AAPL": 0.5, "MSFT": 0.5}))
```

---

## 3. Strict Safety & Risk Control Rules

> [!CAUTION]
> Trading involves real financial risk. You must adhere to the following rules:
>
> 1. **Verification**: Always print the computed trades to the screen (ticker, action, quantity, estimated cost, and final portfolio weight) and wait for the user's explicit confirmation before calling execute tools.
> 2. **Slippage & Limit Orders**: Prefer Limit Orders over Market Orders to prevent execution at unfavorable prices due to sudden price spikes or low liquidity.
> 3. **Risk Management**: Always implement strict stop-loss rules (e.g. automatically queueing a stop-sell order at -5% from entry price) when completing stock purchases.
