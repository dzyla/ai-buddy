"""ZulipAiBridge: the Zulip listener that dispatches messages to the local `ai` agent.

Each message is dispatched to its own daemon thread so the Zulip listener loop is never blocked.
"""

import os
import re
import sys
import time
import logging
import threading
import subprocess

import requests

from . import zulip
from .config import load_env_file, resolve_ai_bin
from .context import ContextWindowManager
from .files import FileParser
from .handler import MessageHandlerMixin
from .memory import ZulipMemoryManager
from .owner import detect_owner

logger = logging.getLogger("ZulipBridge")

# Zulip rejects/truncates messages over 10000 chars; leave headroom for the
# "(Part i/n)" header and re-opened code fences.
ZULIP_MAX_CHUNK = 8500



class ZulipAiBridge(MessageHandlerMixin):
    def __init__(self, client=None):
        if getattr(zulip, "Client", None) is None and client is None:
            raise RuntimeError(
                "The 'zulip' Python package is required. Install it for this interpreter: "
                f"{sys.executable} -m pip install zulip")
        # Allow dependency injection for testing / alternative backends
        self.client = client if client is not None else zulip.Client()
        self.bot_email = getattr(self.client, 'email', None)
        base_url = getattr(self.client, 'base_url', '') or ''
        print(f"Loaded credentials for: {self.bot_email} on {base_url}")

        # Resolve bot profile (name and user ID for mention checking)
        self.bot_profile = {}
        if hasattr(self.client, "get_profile"):
            try:
                res = self.client.get_profile()
                if isinstance(res, dict) and res.get("result") == "success":
                    self.bot_profile = res
                elif isinstance(res, dict):
                    print(f"⚠️ get_profile failed: {res.get('msg')} (check the API key/site in your zuliprc)")
            except Exception as e:
                logger.debug(f"Could not retrieve bot profile: {e}")
        self.bot_user_id = self.bot_profile.get("user_id")
        self.bot_name = self.bot_profile.get("full_name") or (self.bot_email.split('@')[0] if self.bot_email else "AI bot")

        self.owner_source = None
        self.detected_owner = self._detect_owner()
        owner, source = self.effective_owner()
        if owner:
            print(f"Allowed user: {owner} (from {source})")
        else:
            print("⚠️ No owner configured or detected: ALL messages will be ignored. "
                  "Set ZULIP_USER (see docs/zulip_setup.md).")
        # Initialize the file parser for processing uploaded documents
        self._file_parser = FileParser(self.client, base_url)
        print("File parser initialized — will extract content from uploaded documents.")
        # Initialize persistent Zulip chat memory
        self.memory = ZulipMemoryManager()
        print("Zulip chat memory manager initialized.")
        # Live worker threads (pruned as they finish) so state does not grow.
        self._workers = set()
        self._workers_lock = threading.Lock()

    def _add_reaction(self, msg, emoji_name):
        """Add an emoji reaction to the message."""
        try:
            if hasattr(self.client, "add_reaction"):
                self.client.add_reaction({
                    "message_id": msg.get("id"),
                    "emoji_name": emoji_name
                })
        except Exception:
            pass

    def _remove_reaction(self, msg, emoji_name):
        """Remove an emoji reaction from the message."""
        try:
            if hasattr(self.client, "remove_reaction"):
                self.client.remove_reaction({
                    "message_id": msg.get("id"),
                    "emoji_name": emoji_name
                })
        except Exception:
            pass

    def _set_typing(self, msg, op="start"):
        """Send typing status to Zulip."""
        try:
            if hasattr(self.client, "set_typing_status"):
                payload = {"op": op}
                if msg.get("type") == "private":
                    payload["to"] = [msg.get("sender_email")]
                else:
                    stream_id = msg.get("stream_id")
                    if stream_id:
                        payload["to"] = [stream_id]
                    else:
                        payload["to"] = [msg.get("display_recipient")]
                    payload["topic"] = msg.get("subject")
                self.client.set_typing_status(payload)
        except Exception:
            pass

    def _is_bot_mentioned(self, msg, content):
        """Check if this message explicitly mentions the bot."""
        flags = msg.get("flags", [])
        if "mentioned" in flags:
            return True

        # Check content for mention patterns:
        # e.g. @**AI bot**, @_**AI bot**, @**AI bot|4242**, @_**AI bot|4242**
        # or @**ai-bot**, @_**ai-bot**
        names = []
        if self.bot_name:
            names.append(self.bot_name)
        if self.bot_email:
            names.append(self.bot_email)
            names.append(self.bot_email.split('@')[0])

        for n in names:
            if not n:
                continue
            pat = rf'@_?\*\*{re.escape(n)}(?:\|[0-9]+)?\*\*'
            if re.search(pat, content, re.IGNORECASE):
                return True

        if self.bot_user_id:
            pat = rf'@_?\*\*[^*]+\|{self.bot_user_id}\*\*'
            if re.search(pat, content):
                return True

        return False

    def _strip_bot_mentions(self, content):
        """Strip bot mention syntax from message content."""
        names = []
        if self.bot_name:
            names.append(self.bot_name)
        if self.bot_email:
            names.append(self.bot_email)
            names.append(self.bot_email.split('@')[0])

        for n in names:
            if not n:
                continue
            pat = rf'@_?\*\*{re.escape(n)}(?:\|[0-9]+)?\*\*'
            content = re.sub(pat, '', content, flags=re.IGNORECASE)

        if self.bot_user_id:
            pat = rf'@_?\*\*[^*]+\|{self.bot_user_id}\*\*'
            content = re.sub(pat, '', content)

        content = content.strip()
        if content.startswith(':') or content.startswith(','):
            content = content[1:].strip()
        return content

    def _ensure_inference_server(self, msg=None):
        """
        Verify that the inference server is reachable and ready.
        If local and down or loading, attempt to wake the service and wait until ready.
        """
        env = load_env_file()
        base_url = env.get("INFER_BASE_URL", os.environ.get("INFER_BASE_URL", "http://localhost:8080/v1/"))

        # Non-local endpoints don't need socket management
        is_local = any(h in base_url for h in ("127.0.0.1", "localhost", "0.0.0.0"))
        if not is_local:
            return True, "remote"

        health_url = base_url.rstrip("/")
        if health_url.endswith("/v1"):
            health_url = health_url[:-3]
        health_url = f"{health_url}/health"
        health_url = health_url.replace("://localhost:", "://127.0.0.1:")

        def probe():
            try:
                r = requests.get(health_url, timeout=2.0)
                if r.status_code == 200:
                    return "ok"
                if r.status_code == 503:
                    return "loading"
            except requests.exceptions.RequestException:
                pass
            except Exception:
                pass
            return "down"

        state = probe()
        if state == "ok":
            return True, "ok"

        # Server is not ready (down or loading). Attempt auto-wake and wait up to 45s for model load.
        logger.info(f"Local inference server is not ready (state: {state}). Attempting auto-wake...")
        if msg:
            self._add_reaction(msg, "hourglass_flowing_sand")

        try:
            subprocess.run(["systemctl", "--user", "reset-failed", "llama-server.socket", "llama-server.service"], capture_output=True, timeout=30)
            res = subprocess.run(["systemctl", "--user", "start", "llama-server.service"], capture_output=True, timeout=60)
            if res.returncode != 0:
                subprocess.run(["systemctl", "--user", "start", "llama-server.socket"], capture_output=True, timeout=30)
                try:
                    requests.get(health_url, timeout=0.5)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to auto-wake inference server: {e}")

        # Poll for up to 45s while the server starts and loads model into VRAM (typically 8-15s)
        for _ in range(30):
            time.sleep(1.5)
            state = probe()
            if state == "ok":
                if msg:
                    self._remove_reaction(msg, "hourglass_flowing_sand")
                return True, "ok"

            try:
                chk = subprocess.run(["systemctl", "--user", "is-failed", "llama-server.service"], capture_output=True, text=True, timeout=10)
                if chk.stdout.strip() == "failed":
                    logger.error("llama-server.service entered failed state during startup")
                    break
            except Exception:
                pass

        if msg:
            self._remove_reaction(msg, "hourglass_flowing_sand")

        return False, state

    def _is_trusted_url(self, url):
        """Check if a URL is from the trusted Zulip domain."""
        return self._file_parser._is_trusted_url(url)

    def _is_trusted_redirect(self, final_url, original_url):
        """Check if a redirect final URL is trusted."""
        return self._file_parser._is_trusted_redirect(final_url, original_url)

    def _download_file(self, url, dest_path):
        """Download a file, delegating to the file parser."""
        return self._file_parser._download_file(url, dest_path)

    def _detect_owner(self):
        """Detect the owner's Zulip email (see module-level detect_owner)."""
        owner, source = detect_owner(self.client, self.bot_email, self.bot_user_id)
        self.owner_source = source
        return owner

    def effective_owner(self):
        """Return (allowed_user, source) exactly as handle_message applies it.

        ZULIP_USER wins; otherwise the auto-detected owner. ("", None) means
        nobody is allowed (fail closed)."""
        env_user = (os.environ.get("ZULIP_USER") or "").strip()
        if env_user:
            return env_user, "ZULIP_USER"
        if self.detected_owner:
            return self.detected_owner, getattr(self, "owner_source", None) or "auto-detected"
        return "", None

    def _send_reply(self, msg, content):
        """Send a reply to the same stream/topic or private thread.

        Retries on transient errors (rate limiting / connection drops) with
        exponential backoff, since Zulip may temporarily refuse messages.
        Note the Zulip client reports most API failures as a returned
        {"result": "error"} dict rather than raising.
        """
        max_retries = 3
        backoff = 2
        message = {
            "type": "private" if msg.get('type') == 'private' else "stream",
        }
        if msg.get('type') == 'private':
            recipients = msg.get('display_recipient')
            emails = []
            if isinstance(recipients, list):
                # Group DM: answer the whole group, not just the sender.
                emails = [r.get('email') for r in recipients
                          if isinstance(r, dict) and r.get('email') and r.get('email') != self.bot_email]
            message["to"] = emails or [msg.get('sender_email')]
        else:
            message["to"] = msg.get('display_recipient')
            message["topic"] = msg.get('subject')
        message["content"] = content

        last_error = None
        for attempt in range(max_retries):
            retryable = True
            delay = backoff
            try:
                res = self.client.send_message(message)
                if not isinstance(res, dict) or res.get("result") == "success":
                    return
                last_error = res.get("msg") or res.get("code") or "unknown error"
                if res.get("code") == "RATE_LIMIT_HIT":
                    try:
                        delay = max(float(res.get("retry-after", backoff)), 0)
                    except (TypeError, ValueError):
                        delay = backoff
                else:
                    # Permanent API errors (bad stream, invalid content) will
                    # not succeed on retry.
                    retryable = False
            except Exception as e:
                last_error = e
            if retryable and attempt < max_retries - 1:
                print(f"⚠️ send_message failed (attempt {attempt+1}/{max_retries}): {last_error}")
                time.sleep(min(delay, 60))
                backoff = min(backoff * 2, 30)
            else:
                print(f"❌ send_message failed after {attempt+1} attempt(s): {last_error}")
                break

        # Append an error notice to the original message thread as a last resort.
        message["content"] = f"⚠️ Could not deliver reply: {last_error}"
        try:
            self.client.send_message(message)
        except Exception:
            pass

    def _get_context_messages(self, msg, limit=5):
        """Fetch up to `limit` previous messages in the same thread/conversation."""
        try:
            if msg['type'] == 'stream':
                narrow = [
                    {"operator": "stream", "operand": msg['display_recipient']},
                    {"operator": "topic", "operand": msg['subject']}
                ]
            else:  # private
                if isinstance(msg['display_recipient'], list):
                    emails = [r['email'] for r in msg['display_recipient']]
                    narrow = [{"operator": "pm-with", "operand": ",".join(emails)}]
                else:
                    narrow = [{"operator": "pm-with", "operand": msg['sender_email']}]

            payload = {
                "anchor": msg['id'],
                "num_before": limit,
                "num_after": 0,
                "narrow": narrow,
                "apply_markdown": False
            }
            res = self.client.get_messages(payload)
            if res.get("result") == "success":
                messages = res.get("messages", [])
                context_messages = [m for m in messages if m['id'] != msg['id']]
                return context_messages
            else:
                print(f"Zulip API error fetching context: {res.get('msg')}")
        except Exception as e:
            print(f"Error fetching context messages: {e}")
        return []

    def _construct_prompt_with_context(self, msg, content, context_messages):
        """Build a prompt that includes the context messages organically."""
        if not context_messages:
            return content

        context_window = ContextWindowManager()
        context_text = context_window.format_context(context_messages, self.bot_email)
        prompt = f"{context_text}\n\n{content}"

        return prompt

    def _ai_mode(self):
        """Resolve the AI permission mode for subprocess `ai` invocations.

        Defaults to "auto" so the bridge actually executes work instead of
        halting to ask for confirmation on every state-changing action, which
        is useless over Zulip (no interactive approve prompt). Override per
        deployment by setting the BRIDGE_AI_MODE env var (auto/plan/manual).
        """
        return os.environ.get("BRIDGE_AI_MODE", "auto").strip().lower()

    def _resolve_ai_bin(self):
        """Locate the ai binary on the host system."""
        return resolve_ai_bin()

    def _truncate_reply(self, text, max_chars=9000):
        """Truncate a reply to fit Zulip's per-message size limit."""
        if len(text) <= max_chars:
            return text
        head = text[:max_chars].rstrip()
        return f"{head}\n\n*…[reply truncated — {len(text) - max_chars} chars omitted]*"

    def _send_full_reply(self, msg, response_text):
        """Deliver replies cleanly, splitting across sequential messages if exceeding Zulip's single-message limit."""
        if not response_text:
            self._send_reply(msg, "*(agent returned no output)*")
            return

        max_chunk = ZULIP_MAX_CHUNK
        if len(response_text) <= max_chunk:
            self._send_reply(msg, response_text)
            return

        # Split into readable chunks (up to 4 parts)
        chunks = []
        remaining = response_text
        while remaining and len(chunks) < 4:
            if len(remaining) <= max_chunk:
                chunks.append(remaining)
                break
            # Find clean split point (paragraph or newline)
            split_idx = remaining.rfind('\n\n', 0, max_chunk)
            if split_idx == -1:
                split_idx = remaining.rfind('\n', 0, max_chunk)
            if split_idx == -1 or split_idx < 2000:
                split_idx = max_chunk
            chunk = remaining[:split_idx].rstrip()
            remaining = remaining[split_idx:].lstrip()
            # Don't leave a code block open across a message boundary: close
            # it here and re-open it (with its language tag) in the next part.
            fences = re.findall(r'^[ \t]*```(\S*)', chunk, re.MULTILINE)
            if len(fences) % 2 == 1:
                chunk += "\n```"
                remaining = f"```{fences[-1]}\n" + remaining
            chunks.append(chunk)

        if remaining:
            chunks[-1] += f"\n\n*…[output truncated at {len(response_text)} chars]*"

        for i, chunk in enumerate(chunks):
            header = f"*(Part {i+1}/{len(chunks)})*\n\n" if len(chunks) > 1 else ""
            self._send_reply(msg, header + chunk)
            if i < len(chunks) - 1:
                time.sleep(0.5)

    def _is_long_job(self, content):
        """Detect if a request is a long-running, deep research, or complex task."""
        c = content.strip().lower()
        if c.startswith(('/long', '/deep', '/job', '--long', '--deep', ':long')):
            return True
        long_keywords = [
            "deep research", "full refactor", "run benchmark", "long task",
            "take your time", "train model", "design binder", "boltzgen run",
            "investigate thoroughly", "extensive audit", "long job"
        ]
        return any(kw in c for kw in long_keywords)

    def _strip_long_prefix(self, content):
        """Strip command prefixes like /long, /deep from user prompt."""
        c = content.strip()
        for prefix in ('/long', '/deep', '/job', '--long', '--deep', ':long'):
            if c.lower().startswith(prefix):
                return c[len(prefix):].strip()
        return c

    def _dispatch(self, msg, content):
        """Start a tracked daemon worker thread for one message."""
        t = threading.Thread(target=self._worker, args=(msg, content), daemon=True)
        with self._workers_lock:
            self._workers.add(t)
        t.start()
        return t

    def _worker(self, msg, content):
        """Worker-thread body: fetch uploads, run the agent, never raise."""
        try:
            # Replace Zulip upload URLs with extracted file content
            content, _processed = self._file_parser.process_message_urls(content)
            if content.strip():
                self._process_message(msg, content)
        except Exception as e:
            logger.exception("Worker failed while processing message")
            self._remove_reaction(msg, "thought_balloon")
            self._remove_reaction(msg, "hourglass_flowing_sand")
            self._set_typing(msg, "stop")
            self._add_reaction(msg, "warning")
            try:
                self._send_reply(msg, f"⚠️ Bridge error while processing your message: `{e}`")
            except Exception:
                pass
        finally:
            with self._workers_lock:
                self._workers.discard(threading.current_thread())

    def _safe_handle_message(self, msg):
        """Listener callback: an exception here would tear down
        call_on_each_message (and its event queue), so contain it."""
        try:
            self.handle_message(msg)
        except Exception:
            logger.exception(f"Error handling Zulip message id={msg.get('id') if isinstance(msg, dict) else None}")

    def _manage_context_window(self, context_messages, current_content):
        """Apply context window management to prevent overflow."""
        # Exclude the bot's own messages from context — the agent already
        # has its own conversation history; including Zulip-sent bot messages
        # would duplicate and confuse context.
        filtered = [
            m for m in context_messages
            if m.get("sender_email", "") != self.bot_email
        ]
        if len(filtered) != len(context_messages):
            logger.info(
                f"Filtered out {len(context_messages) - len(filtered)} bot messages "
                f"from context ({len(context_messages)} → {len(filtered)})"
            )

        window_manager = ContextWindowManager()
        truncated_messages, truncated = window_manager.truncate_context(
            filtered, current_content
        )
        if truncated:
            logger.info(f"Context truncated from {len(filtered)} to {len(truncated_messages)} messages")
        return truncated_messages

    def run(self):
        """Start the Zulip AI Bridge with automatic reconnection."""
        print("🚀 Starting Zulip AI Bridge listener (threaded — concurrent messages supported)...")
        
        max_backoff = 60  # Maximum backoff in seconds
        current_backoff = 1
        
        while True:
            started = time.time()
            try:
                self.client.call_on_each_message(self._safe_handle_message)
            except (ConnectionError, requests.exceptions.RequestException) as e:
                if time.time() - started > 300:
                    current_backoff = 1  # previous session was healthy
                print(f"⚠️ Connection error: {e}")
                print(f"⏳ Reconnecting in {current_backoff}s...")
                time.sleep(current_backoff)
                current_backoff = min(current_backoff * 2, max_backoff)
            except Exception as e:
                if time.time() - started > 300:
                    current_backoff = 1
                print(f"❌ Unexpected error: {e}")
                print(f"⏳ Restarting in {current_backoff}s...")
                time.sleep(current_backoff)
                current_backoff = min(current_backoff * 2, max_backoff)
