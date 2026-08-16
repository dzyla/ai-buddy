#!/usr/bin/env python3
import subprocess
import json

def call_mcp(proc, method, params, req_id):
    req = {'jsonrpc': '2.0', 'id': req_id, 'method': method, 'params': params}
    proc.stdin.write(json.dumps(req) + '\n')
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            return None
        try:
            data = json.loads(line)
            if data.get('id') == req_id:
                return data
        except:
            pass

proc = subprocess.Popen(
    ['npx', '-y', 'mcp-remote', 'https://agent.robinhood.com/mcp/trading'],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    bufsize=1
)

# Init
call_mcp(proc, 'initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'ai-buddy', 'version': '1.0'}}, 1)
proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
proc.stdin.flush()

acc = '837546068'
port = call_mcp(proc, 'tools/call', {'name': 'get_portfolio', 'arguments': {'account_number': acc}}, 10)
positions = call_mcp(proc, 'tools/call', {'name': 'get_equity_positions', 'arguments': {'account_number': acc}}, 11)

pos_data = positions.get('result', {}).get('structuredContent', {}).get('data', {}).get('positions', [])
symbols = [p['symbol'] for p in pos_data if float(p.get('quantity', 0)) > 0]

# Batch fetch quotes
quotes_map = {}
batch_size = 30
for i in range(0, len(symbols), batch_size):
    batch = symbols[i:i+batch_size]
    quotes_resp = call_mcp(proc, 'tools/call', {'name': 'get_equity_quotes', 'arguments': {'symbols': batch}}, 12 + i)
    results = quotes_resp.get('result', {}).get('structuredContent', {}).get('data', {}).get('results', [])
    for r in results:
        q = r.get('quote', {})
        sym = q.get('symbol')
        if sym:
            quotes_map[sym] = q

port_data = port.get('result', {}).get('structuredContent', {}).get('data', {})

print('=== ROBINHOOD LIVE PORTFOLIO SUMMARY ===')
tot_val = float(port_data.get('total_value', 0.0))
eq_val = float(port_data.get('equity_value', 0.0))
cash_val = float(port_data.get('cash', 0.0))
bp_val = float(port_data.get('buying_power', {}).get('buying_power', 0.0))

print(f"Total Portfolio Value : ${tot_val:,.2f}")
print(f"Equity Holdings Value : ${eq_val:,.2f}")
print(f"Cash Balance          : ${cash_val:,.2f}")
print(f"Buying Power          : ${bp_val:,.2f}")
print(f"Total Active Holdings : {len(symbols)} positions\n")

summary = []
total_cost_basis = 0.0
total_current_val = 0.0

for p in pos_data:
    sym = p['symbol']
    qty = float(p.get('quantity', 0.0))
    if qty <= 0:
        continue
    cost = float(p.get('average_buy_price', 0.0) or 0.0)
    q = quotes_map.get(sym, {})
    price = float(q.get('last_trade_price') or q.get('last_non_reg_trade_price') or q.get('adjusted_previous_close') or cost)
    
    val = qty * price
    invested = qty * cost
    pnl = val - invested
    pnl_pct = (pnl / invested * 100.0) if invested > 0 else 0.0
    
    total_cost_basis += invested
    total_current_val += val
    
    summary.append({
        'symbol': sym,
        'quantity': qty,
        'cost': cost,
        'price': price,
        'value': round(val, 2),
        'invested': round(invested, 2),
        'pnl': round(pnl, 2),
        'pnl_pct': round(pnl_pct, 2),
        'weight_pct': round((val / eq_val * 100.0) if eq_val > 0 else 0.0, 2)
    })

summary.sort(key=lambda x: x['value'], reverse=True)

print(f"{'Symbol':<7} | {'Qty':<8} | {'Avg Cost':<9} | {'Cur Price':<9} | {'Value':<9} | {'Weight':<6} | {'Unrealized P&L'}")
print("-" * 80)
for s in summary:
    print(f"{s['symbol']:<7} | {s['quantity']:<8.3f} | ${s['cost']:<8.2f} | ${s['price']:<8.2f} | ${s['value']:<8.2f} | {s['weight_pct']:>5.1f}% | ${s['pnl']:>+7.2f} ({s['pnl_pct']:>+6.2f}%)")

print("\n" + "=" * 80)
total_pnl = total_current_val - total_cost_basis
total_pnl_pct = (total_pnl / total_cost_basis * 100.0) if total_cost_basis > 0 else 0.0
print(f"Total Invested (Cost Basis) : ${total_cost_basis:,.2f}")
print(f"Total Current Equity Value  : ${total_current_val:,.2f}")
print(f"Net Unrealized Profit/Loss  : ${total_pnl:>+,.2f} ({total_pnl_pct:>+6.2f}%)")
print("=" * 80)

proc.terminate()
