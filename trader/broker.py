"""Robinhood Agentic MCP bridge: account resolution, reads and the order mutator."""

import os
import sys
import json
import time
import subprocess
import datetime
import shutil
from typing import List, Dict, Any, Optional

from .config import load_trader_config, trader_config_path


# ==============================================================================
# 6. Robinhood MCP Integration & Authorization Helper
# ==============================================================================

def authenticate_robinhood_mcp():
    """Verifies stored credentials and only runs browser flow if no cached session exists."""
    print("=" * 70)
    print("  ROBINHOOD AGENTIC TRADING - MCP CREDENTIAL STATUS")
    print("=" * 70)
    
    # First, test existing stored credentials silently
    try:
        accounts = RobinhoodExecutor.get_accounts()
        if accounts and len(accounts) > 0:
            print("✓ Stored credentials are ACTIVE and verified.")
            for acc in accounts:
                print(f"  - Account: {acc.get('account_number')} ({acc.get('account_type', 'standard')})")
            print("No re-authentication or browser login needed.\n")
            return
    except Exception as e:
        print(f"Stored credential probe: {e}")

    # If not interactive, do not pop up browser
    if not sys.stdin.isatty() or os.environ.get("BROWSER") == "none":
        print("Non-interactive mode: skipping browser authentication.\n")
        return

    print("Starting Robinhood Agentic MCP bridge...\n")
    print("A browser window will open to authenticate with Robinhood.")
    print("Once authorized, credentials will be cached in your local MCP store.\n")
    
    cmd = "mcp-remote https://agent.robinhood.com/mcp/trading" if shutil.which("mcp-remote") else "npx -y mcp-remote@0.2.5 https://agent.robinhood.com/mcp/trading"
    print(f"Executing: {cmd}\n")
    os.system(cmd)


class AccountResolutionError(RuntimeError):
    """The account to act on could not be determined; callers must not fall back to another account."""


class OrderNotConfirmed(RuntimeError):
    """An order request was sent but no response arrived: it may or may not have been placed."""

class RobinhoodExecutor:
    """Direct, high-speed execution bridge for Robinhood Agentic MCP (sub-second order placement)."""

    @classmethod
    def _extract_data(cls, res: Dict[str, Any]) -> Dict[str, Any]:
        """Extracts structured JSON data safely from MCP response dictionary."""
        if not res:
            return {}
        if isinstance(res.get("structuredContent"), dict) and "data" in res["structuredContent"]:
            return res["structuredContent"]["data"]
        if "content" in res and isinstance(res["content"], list) and len(res["content"]) > 0:
            item = res["content"][0]
            if isinstance(item, dict) and "text" in item:
                try:
                    parsed = json.loads(item["text"])
                    if isinstance(parsed, dict) and "data" in parsed:
                        return parsed["data"]
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    pass
        return res

    @classmethod
    def _sync_mcp_auth_tokens(cls):
        """Sync stored OAuth tokens across different mcp-remote package versions in ~/.mcp-auth/."""
        auth_dir = os.path.expanduser("~/.mcp-auth")
        if not os.path.isdir(auth_dir):
            return
        try:
            remote_dirs = [os.path.join(auth_dir, d) for d in os.listdir(auth_dir) if d.startswith("mcp-remote-") and os.path.isdir(os.path.join(auth_dir, d))]
            if not remote_dirs:
                return
            
            tokens_map = {}
            for rd in remote_dirs:
                for f in os.listdir(rd):
                    if f.endswith("_tokens.json") or f.endswith("_client_info.json"):
                        fpath = os.path.join(rd, f)
                        try:
                            mtime = os.path.getmtime(fpath)
                            with open(fpath, "r", encoding="utf-8") as tf:
                                td = json.load(tf)
                            if td:
                                if f not in tokens_map or mtime > tokens_map[f][0]:
                                    tokens_map[f] = (mtime, fpath, td)
                        except Exception:
                            pass
            
            for f, (_, src_path, td) in tokens_map.items():
                for rd in remote_dirs:
                    dest_path = os.path.join(rd, f)
                    if not os.path.exists(dest_path) or os.path.getsize(dest_path) == 0:
                        try:
                            with open(dest_path, "w", encoding="utf-8") as df:
                                json.dump(td, df, indent=2)
                            os.chmod(dest_path, 0o600)
                        except Exception:
                            pass

            for rd in remote_dirs:
                for f in os.listdir(rd):
                    if f.endswith("_lock.json") or "_code_verifier" in f:
                        lock_file = os.path.join(rd, f)
                        try:
                            if time.time() - os.path.getmtime(lock_file) > 30:
                                os.remove(lock_file)
                        except Exception:
                            pass
        except Exception:
            pass

    # Tools that change broker state: never re-sent after the request went out (a lost reply
    # does not mean the order was not placed).
    MUTATING_TOOLS = frozenset({"place_equity_order", "cancel_equity_order", "replace_equity_order"})

    @staticmethod
    def _read_jsonrpc_response(fd: int, want_id: int, timeout: float, buf: bytearray) -> Optional[Dict[str, Any]]:
        """Read newline-delimited JSON-RPC messages from a raw fd until one has id == want_id.

        Uses select + os.read on the fd (not file.readline, which blocks forever on a partial
        line), so the whole wait is bounded by `timeout`. Returns None on timeout or EOF."""
        import select
        deadline = time.time() + timeout
        while True:
            while b"\n" in buf:
                idx = buf.index(b"\n")
                line = bytes(buf[:idx])
                del buf[:idx + 1]
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == want_id:
                    return msg
            rem = deadline - time.time()
            if rem <= 0:
                return None
            r, _, _ = select.select([fd], [], [], rem)
            if not r:
                return None
            chunk = os.read(fd, 65536)
            if not chunk:
                return None
            buf.extend(chunk)

    @classmethod
    def call_mcp_tool(cls, tool_name: str, arguments: Dict[str, Any], timeout: int = 15, max_retries: int = 2) -> Dict[str, Any]:
        """Calls any Robinhood MCP tool in sub-second time without browser prompts.

        Returns {} on failure. Read-only tools are retried; MUTATING_TOOLS are retried only if the
        tool request was never written (e.g. initialize failed), so an order is sent at most once."""
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("INFER_TEST_MODE"):
            return {}

        cls._sync_mcp_auth_tokens()

        env = os.environ.copy()
        env['BROWSER'] = ':'
        env['NO_BROWSER'] = '1'
        env['CI'] = '1'
        env['DISPLAY'] = ''
        env.pop('WSL_DISTRO_NAME', None)

        remote_cmd = ['mcp-remote', 'https://agent.robinhood.com/mcp/trading'] if shutil.which('mcp-remote') else ['npx', '-y', 'mcp-remote@0.2.5', 'https://agent.robinhood.com/mcp/trading']

        for attempt in range(max_retries + 1):
            proc = None
            request_sent = False
            try:
                proc = subprocess.Popen(
                    remote_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                    env=env,
                    start_new_session=True
                )
                req_init = {
                    'jsonrpc': '2.0',
                    'id': 1,
                    'method': 'initialize',
                    'params': {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'ai-buddy', 'version': '1.0'}}
                }
                proc.stdin.write(json.dumps(req_init) + '\n')
                proc.stdin.flush()

                fd = proc.stdout.fileno()
                buf = bytearray()
                if cls._read_jsonrpc_response(fd, 1, timeout, buf) is None:
                    if attempt < max_retries:
                        time.sleep(0.5)
                        continue
                    return {}

                proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
                proc.stdin.flush()

                req_tool = {
                    'jsonrpc': '2.0',
                    'id': 2,
                    'method': 'tools/call',
                    'params': {'name': tool_name, 'arguments': arguments}
                }
                request_sent = True
                proc.stdin.write(json.dumps(req_tool) + '\n')
                proc.stdin.flush()

                data = cls._read_jsonrpc_response(fd, 2, timeout, buf)
                if data is not None:
                    return data.get('result', {}) or {}
            except Exception:
                pass
            finally:
                if proc:
                    try:
                        import signal
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        proc.wait(timeout=0.5)
                    except Exception:
                        try:
                            proc.kill()
                            proc.wait(timeout=0.5)
                        except Exception:
                            pass
            if request_sent and tool_name in cls.MUTATING_TOOLS:
                return {}  # outcome unknown: never re-send a state-changing request
            if attempt < max_retries:
                time.sleep(0.5)
        return {}

    @classmethod
    def get_accounts(cls) -> List[Dict[str, Any]]:
        """Fetch all available accounts under the authorized credentials."""
        res = cls.call_mcp_tool('get_accounts', {})
        data = cls._extract_data(res)
        if isinstance(data, dict):
            return data.get('accounts', [])
        return []

    @classmethod
    def configured_account(cls, role: str) -> Optional[str]:
        """Account number for a role ('agentic', 'primary', 'roth') from env or the trader config.

        Env var RH_<ROLE>_ACCOUNT wins; otherwise {"accounts": {role: number}} in
        trader_config_path(). Numbers are never hardcoded in this file (public repo)."""
        env_val = os.environ.get(f"RH_{role.upper()}_ACCOUNT", "").strip()
        if env_val:
            return env_val
        val = (load_trader_config().get("accounts") or {}).get(role)
        return str(val).strip() if val not in (None, "") else None

    @classmethod
    def get_agentic_account(cls) -> Optional[Dict[str, Any]]:
        """The account with agentic_allowed=True: the configured one if set, else the only one listed.

        Fails closed: returns None when the configured account is not agentic_allowed, when several
        accounts are agentic_allowed and none is configured, or when the account list is unavailable."""
        accounts = cls.get_accounts()
        configured = cls.configured_account("agentic")
        if configured:
            for a in accounts:
                if str(a.get("account_number")) == configured:
                    return a if a.get("agentic_allowed") is True else None
            return None
        agentic = [a for a in accounts if a.get("agentic_allowed") is True]
        return agentic[0] if len(agentic) == 1 else None

    @classmethod
    def get_agentic_account_number(cls) -> str:
        """Account number automated orders go to. Raises AccountResolutionError if it cannot be determined
        (never falls back to another account); execute_market_order re-verifies agentic_allowed per order."""
        configured = cls.configured_account("agentic")
        if configured:
            return configured
        acc = cls.get_agentic_account()
        if acc and acc.get("account_number"):
            return str(acc["account_number"])
        raise AccountResolutionError(
            "agentic account unknown: set RH_AGENTIC_ACCOUNT or accounts.agentic in "
            f"{trader_config_path()}, or ensure exactly one account has agentic_allowed=true")

    @classmethod
    def is_agentic_account(cls, account_number: str) -> bool:
        """True only if the live account list marks this account agentic_allowed (False on any failure)."""
        try:
            for a in cls.get_accounts():
                if str(a.get("account_number")) == str(account_number):
                    return a.get("agentic_allowed") is True
        except Exception:
            pass
        return False

    @classmethod
    def get_default_account_number(cls) -> str:
        """Configured primary account, else the broker's default, else the first listed account."""
        configured = cls.configured_account("primary")
        if configured:
            return configured
        accounts = cls.get_accounts()
        for a in accounts:
            if a.get("is_default") and a.get("account_number"):
                return str(a["account_number"])
        if accounts and accounts[0].get("account_number"):
            return str(accounts[0]["account_number"])
        raise AccountResolutionError(
            "default account unknown: set RH_PRIMARY_ACCOUNT or accounts.primary in "
            f"{trader_config_path()}, or check MCP authentication")

    @classmethod
    def resolve_account(cls, account_input: Optional[str] = None) -> str:
        """Resolves an account alias or number (e.g. 'agentic' -> the configured agentic account)."""
        if not account_input:
            return cls.get_default_account_number()
        inp = str(account_input).strip().lower()
        if inp in ("agentic", "agent", "sandbox", "agentic_account"):
            return cls.get_agentic_account_number()
        if inp in ("default", "primary", "manual"):
            return cls.get_default_account_number()
        if inp in ("roth", "ira", "roth_ira"):
            roth = cls.configured_account("roth")
            if roth:
                return roth
            raise AccountResolutionError(f"roth account unknown: set RH_ROTH_ACCOUNT or accounts.roth in {trader_config_path()}")
        return str(account_input).strip()

    @classmethod
    def get_live_portfolio(cls, account_number: Optional[str] = None) -> Dict[str, Any]:
        """Fetch real-time portfolio balance, equity value, cash, and buying power."""
        acc = cls.resolve_account(account_number)
        res = cls.call_mcp_tool('get_portfolio', {'account_number': acc})
        data = cls._extract_data(res)
        return data if isinstance(data, dict) else {}

    @classmethod
    def get_equity_positions(cls, account_number: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch active equity holdings and quantities."""
        acc = cls.resolve_account(account_number)
        res = cls.call_mcp_tool('get_equity_positions', {'account_number': acc})
        data = cls._extract_data(res)
        if isinstance(data, dict):
            return data.get('positions', [])
        return []

    @classmethod
    def get_orders(cls, account_number: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch equity order history."""
        acc = cls.resolve_account(account_number)
        res = cls.call_mcp_tool('get_equity_orders', {'account_number': acc})
        data = cls._extract_data(res)
        if isinstance(data, dict):
            return data.get('orders', [])
        return []

    @classmethod
    def get_realized_pnl(cls, account_number: Optional[str] = None) -> Dict[str, Any]:
        """Fetch realized profit and loss metrics."""
        acc = cls.resolve_account(account_number)
        res = cls.call_mcp_tool('get_realized_pnl', {'account_number': acc})
        data = cls._extract_data(res)
        return data if isinstance(data, dict) else {}

    @classmethod
    def get_pnl_trade_history(cls, account_number: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch realized P&L trade-by-trade history."""
        acc = cls.resolve_account(account_number)
        res = cls.call_mcp_tool('get_pnl_trade_history', {'account_number': acc})
        data = cls._extract_data(res)
        if isinstance(data, dict):
            return data.get('trades', [])
        return []

    @classmethod
    def get_equity_quotes(cls, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """Batch fetch live equity quotes."""
        quotes_map = {}
        batch_size = 30
        for i in range(0, len(symbols), batch_size):
            batch = [s.upper().strip() for s in symbols[i:i + batch_size]]
            res = cls.call_mcp_tool('get_equity_quotes', {'symbols': batch})
            data = cls._extract_data(res)
            results = data.get('results', []) if isinstance(data, dict) else []
            for r in results:
                q = r.get('quote', {})
                sym = q.get('symbol')
                if sym:
                    quotes_map[sym] = q
        return quotes_map

    @classmethod
    def get_equity_historicals(cls, symbols: List[str], interval: str = "day", span_days: int = 90) -> Dict[str, List[Dict[str, Any]]]:
        """Fetches OHLCV historical bars via Robinhood MCP."""
        start_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=span_days)).strftime("%Y-%m-%dT00:00:00Z")
        out = {}
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = [s.upper().strip() for s in symbols[i:i + batch_size]]
            res = cls.call_mcp_tool('get_equity_historicals', {
                'symbols': batch,
                'interval': interval,
                'start_time': start_time
            })
            data = cls._extract_data(res)
            results = data.get('results', []) if isinstance(data, dict) else []
            for r in results:
                sym = r.get('symbol')
                bars = r.get('bars', [])
                if sym:
                    out[sym] = bars
        return out

    @classmethod
    def get_equity_technical_indicators(cls, symbol: str, indicator_type: str = "rsi", interval: str = "day", span_days: int = 90) -> List[Dict[str, Any]]:
        """Computes technical indicator series via Robinhood MCP."""
        start_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=span_days)).strftime("%Y-%m-%dT00:00:00Z")
        res = cls.call_mcp_tool('get_equity_technical_indicators', {
            'symbol': symbol.upper().strip(),
            'type': indicator_type.lower().strip(),
            'interval': interval,
            'start_time': start_time
        })
        data = cls._extract_data(res)
        results = data.get('results', []) if isinstance(data, dict) else []
        if results and 'indicators' in results[0]:
            return results[0]['indicators']
        return []

    @classmethod
    def execute_market_order(cls, account_number: str, symbol: str, side: str,
                             dollar_amount: Optional[str] = None,
                             quantity: Optional[str] = None) -> Dict[str, Any]:
        """Places an equity market order. Refuses (PermissionError) unless the live account list marks
        `account_number` agentic_allowed. Raises OrderNotConfirmed when no reply arrived (the order
        may have been placed; do not blindly retry a buy) and RuntimeError when the broker rejects it."""
        if not account_number or not cls.is_agentic_account(account_number):
            raise PermissionError(f"account {account_number or '<none>'} is not agentic_allowed (or the account list is unavailable); order refused")
        if not dollar_amount and not quantity:
            raise ValueError("market order needs dollar_amount or quantity")
        args = {
            'account_number': account_number,
            'symbol': symbol.upper().strip(),
            'side': side.lower(),
            'type': 'market'
        }
        if dollar_amount:
            args['dollar_amount'] = str(dollar_amount)
        elif quantity:
            args['quantity'] = str(quantity)

        res = cls.call_mcp_tool('place_equity_order', args)
        if not res:
            raise OrderNotConfirmed(f"no response to {side} {symbol} order; check open orders before retrying")
        if res.get("isError"):
            raise RuntimeError(f"{side} {symbol} order rejected: {str(res.get('content') or res)[:200]}")
        return res


# Alias for backward compatibility and uniform API reference
RobinhoodAPI = RobinhoodExecutor
