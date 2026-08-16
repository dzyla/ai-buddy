#!/usr/bin/env python3
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import subprocess
import json
from robinhood_trader import NewsSentimentEngine

env = os.environ.copy()
env['BROWSER'] = 'none'

proc = subprocess.Popen(
    ['npx', '-y', 'mcp-remote', 'https://agent.robinhood.com/mcp/trading', '--silent'],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    bufsize=1,
    env=env
)

def call_mcp(method, params, req_id):
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

call_mcp('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'ai-buddy', 'version': '1.0'}}, 1)
proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
proc.stdin.flush()

universe = [
    'NVDA', 'AVGO', 'MSFT', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AAPL',
    'CRWD', 'PANW', 'ARM', 'SMH', 'QQQ', 'VTI', 'PLTR', 'AMD', 'LLY', 'NFLX'
]

q_resp = call_mcp('tools/call', {'name': 'get_equity_quotes', 'arguments': {'symbols': universe}}, 10)
results = q_resp.get('result', {}).get('structuredContent', {}).get('data', {}).get('results', [])

quotes = {}
for r in results:
    q = r.get('quote', {})
    sym = q.get('symbol')
    if sym:
        quotes[sym] = q

fund_resp = call_mcp('tools/call', {'name': 'get_equity_fundamentals', 'arguments': {'symbols': universe[:10]}}, 11)
funds = fund_resp.get('result', {}).get('structuredContent', {}).get('data', {}).get('fundamentals', [])
fund_map = {f.get('symbol'): f for f in funds if isinstance(f, dict)}

analysis_list = []
for sym in universe:
    q = quotes.get(sym, {})
    price = float(q.get('last_trade_price') or q.get('last_non_reg_trade_price') or 0.0)
    prev = float(q.get('adjusted_previous_close') or price)
    chg_1d = ((price - prev) / prev * 100.0) if prev > 0 else 0.0
    
    f = fund_map.get(sym, {})
    pe = f.get('pe_ratio') or 'N/A'
    
    news = NewsSentimentEngine.analyze_news_sentiment(sym)
    
    score = 50.0
    if news['sentiment_score'] > 0.15:
        score += 18
    elif news['sentiment_score'] > 0.05:
        score += 10
    elif news['sentiment_score'] < -0.15:
        score -= 15
        
    if -6.0 <= chg_1d <= -2.0:
        score += 14  # High quality pullback discount
    elif 0.0 <= chg_1d <= 2.5:
        score += 8   # Trend momentum
        
    analysis_list.append({
        'symbol': sym,
        'price': price,
        'change_1d': chg_1d,
        'pe': pe,
        'sentiment': news['sentiment_label'],
        'sentiment_score': news['sentiment_score'],
        'headline': news['headlines'][0] if news['headlines'] else 'No recent news',
        'score': round(score, 1)
    })

analysis_list.sort(key=lambda x: x['score'], reverse=True)

print('=== MARKET EXCHANGE & NEWS INTELLIGENCE REPORT ===\n')
for a in analysis_list:
    print(f"{a['symbol']:<6} | Price: ${a['price']:<8.2f} | 1D: {a['change_1d']:>+5.2f}% | Score: {a['score']:<4.1f} | News: {a['sentiment']} ({a['sentiment_score']:+.2f})")
    print(f"       Catalyst: {a['headline']}")

proc.terminate()
