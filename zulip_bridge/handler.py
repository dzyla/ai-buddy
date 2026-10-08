"""Message pipeline mixin: run the agent for one message, and the Zulip event handler."""

import os
import time
import logging
import threading
import subprocess

from .config import load_env_file
from .runner import run_ai_subprocess, extract_session_fallback
from .text import clean_response, clean_stderr

logger = logging.getLogger("ZulipBridge")

# Linux caps a single argv string at 128 KiB (MAX_ARG_STRLEN); larger prompts
# are piped to `ai` on stdin instead of failing with E2BIG.
MAX_ARGV_PROMPT_BYTES = 100000



class MessageHandlerMixin:
    """_process_message / handle_message of ZulipAiBridge (split out for module size)."""

    def _process_message(self, msg, content):
        """Run the ai agent and send the result back. Runs in a background thread."""
        tid = threading.get_ident()
        start_time = time.time()
        chat_id = f"zulip_{int(start_time)}_{msg.get('id', 0)}"
        print(f"[thread-{tid}] [{chat_id}] Processing: {content[:80]}")

        is_long = self._is_long_job(content)
        clean_content = self._strip_long_prefix(content)
        sender_email = msg.get("sender_email")
        ai_mode = self._ai_mode()

        # Send visual feedback immediately (reaction and typing indicator)
        self._add_reaction(msg, "thought_balloon")
        self._set_typing(msg, "start")

        # Verify inference server health and attempt auto-wake if down
        is_real_zulip = (
            type(self.client).__name__ == "Client" and
            getattr(type(self.client), "__module__", "") == "zulip"
        )
        if is_real_zulip and os.environ.get("BRIDGE_SKIP_SERVER_PREFLIGHT") != "1":
            server_ok, server_state = self._ensure_inference_server(msg)
            if not server_ok:
                self._remove_reaction(msg, "thought_balloon")
                self._add_reaction(msg, "warning")
                self._set_typing(msg, "stop")
                offline_msg = (
                    "⚠️ **Inference Server is Offline**\n\n"
                    "The local AI backend is not responding and auto-wake did not succeed.\n\n"
                    "**How to start it:**\n"
                    "- Run `ai-backend serve` on the host, or\n"
                    "- Send `/wake` here in Zulip to retry starting the server."
                )
                self._send_full_reply(msg, offline_msg)
                try:
                    self.memory.record_chat({
                        "session_id": chat_id,
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                        "sender_email": sender_email,
                        "sender_full_name": msg.get("sender_full_name", ""),
                        "type": msg.get("type", "private"),
                        "stream": msg.get("display_recipient") if msg.get("type") != "private" else None,
                        "topic": msg.get("subject") if msg.get("type") != "private" else None,
                        "message_id": msg.get("id"),
                        "query": content,
                        "prompt_sent": content,
                        "context_count": 0,
                        "ai_mode": ai_mode,
                        "duration_s": round(time.time() - start_time, 2),
                        "exit_code": -1,
                        "status": "server_offline",
                        "stdout": "",
                        "stderr": f"Server offline (state: {server_state})",
                        "response_delivered": offline_msg,
                        "extracted_reasoning": None,
                        "fallback_used": None
                    })
                except Exception as mex:
                    logger.warning(f"Failed to record Zulip chat memory: {mex}")
                print(f"[thread-{tid}] [{chat_id}] Aborted: server offline.")
                return

        # Timeouts: default 1800s (30 mins), long jobs 7200s (2 hours)
        default_timeout = int(os.environ.get("BRIDGE_TASK_TIMEOUT", os.environ.get("INFER_TASK_TIMEOUT", 1800)))
        long_timeout = int(os.environ.get("BRIDGE_LONG_TASK_TIMEOUT", os.environ.get("INFER_LONG_TASK_TIMEOUT", 7200)))
        task_timeout = long_timeout if is_long else default_timeout

        # If it's a long job, send an immediate confirmation on Zulip and add a reaction
        if is_long:
            try:
                if hasattr(self.client, "add_reaction"):
                    self.client.add_reaction({
                        "message_id": msg.get("id"),
                        "emoji_name": "hourglass_flowing_sand"
                    })
            except Exception:
                pass
            
            self._send_reply(
                msg,
                f"⏳ **Task confirmed in extended execution mode** (Timeout: {task_timeout}s | Unbounded Steps).\n"
                f"I am executing this task now and will post the full results here when completed."
            )

        # Fetch context messages and manage context window
        context_messages = self._get_context_messages(msg)
        context_messages = self._manage_context_window(context_messages, clean_content)
        prompt = self._construct_prompt_with_context(msg, clean_content, context_messages)

        # Build environment for the subprocess, incorporating ~/.local/share/ai/env
        ai_mode = self._ai_mode()
        run_env = os.environ.copy()
        run_env.update(load_env_file())

        if ai_mode == "auto":
            run_env["INFER_AUTO_APPROVE"] = "1"
        else:
            run_env.pop("INFER_AUTO_APPROVE", None)

        run_env["INFER_RAW_OUTPUT"] = "1"

        sender_email = msg.get("sender_email")
        if sender_email:
            run_env["AI_REMINDER_ZULIP_TO"] = sender_email

        mode_flags = []
        if ai_mode == "auto":
            mode_flags = ["--auto"]
        elif ai_mode == "manual":
            mode_flags = ["--manual"]
        else:
            mode_flags = ["--plan"]

        # The bridge runs non-interactively in background threads, so it must always
        # run with -c to complete tasks without pausing to ask interactive confirmation on /dev/tty
        mode_flags.append("-c")

        ai_bin = self._resolve_ai_bin()
        stdin_input = None
        if len(prompt.encode("utf-8", errors="replace")) > MAX_ARGV_PROMPT_BYTES:
            # Too big for a single argv string (E2BIG); pipe it on stdin.
            stdin_input = prompt
            prompt_arg = "Respond to the Zulip message provided in the context below."
        else:
            prompt_arg = prompt
        ai_cmd = [ai_bin, "-q"] + mode_flags + [prompt_arg]

        raw_stdout = ""
        raw_stderr = ""
        exit_code = 0
        status = "success"
        fallback_used = None
        extracted_reasoning = None

        try:
            result = run_ai_subprocess(
                ai_cmd,
                env=run_env,
                timeout=task_timeout,
                input=stdin_input,
            )
            raw_stdout = result.stdout or ""
            raw_stderr = result.stderr or ""
            exit_code = result.returncode

            if exit_code == 0:
                response_text = clean_response(raw_stdout)
            else:
                if raw_stdout.strip():
                    response_text = clean_response(raw_stdout)
                    shown_err = clean_stderr(raw_stderr)
                    if shown_err:
                        response_text += f"\n\n*Stderr:*\n```\n{shown_err}\n```"
                else:
                    shown_err = clean_stderr(raw_stderr)
                    response_text = f"⚠️ Agent returned error (exit code {exit_code}):\n```\n{shown_err}\n```" if shown_err else ""

            if not response_text:
                fallback_text, fallback_status, reasoning_snip = extract_session_fallback(
                    raw_stdout=raw_stdout,
                    raw_stderr=raw_stderr,
                    min_mtime=start_time - 1
                )
                response_text = fallback_text
                status = fallback_status
                fallback_used = fallback_status
                extracted_reasoning = reasoning_snip
            else:
                status = "success" if exit_code == 0 else "error"

        except subprocess.TimeoutExpired:
            status = "timeout"
            exit_code = -1
            response_text = (
                f"⏱️ The agent timed out after {task_timeout}s. "
                "For very long-running jobs, use `/long <prompt>` to run in extended execution mode "
                "or ask me to schedule a background task."
            )
        except Exception as e:
            status = "error"
            exit_code = -1
            raw_stderr = str(e)
            response_text = f"⚠️ Failed to run local `ai` CLI: {str(e)}"

        duration_s = round(time.time() - start_time, 2)

        # Clear temporary thinking reaction and stop typing
        self._remove_reaction(msg, "thought_balloon")
        self._remove_reaction(msg, "hourglass_flowing_sand")
        self._set_typing(msg, "stop")

        if status in ("success", "stdout", "fallback_reasoning", "fallback_tools", "fallback_content"):
            self._add_reaction(msg, "white_check_mark")
        else:
            self._add_reaction(msg, "warning")

        # Record this interaction to durable memory
        try:
            self.memory.record_chat({
                "session_id": chat_id,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "sender_email": sender_email,
                "sender_full_name": msg.get("sender_full_name", ""),
                "type": msg.get("type", "private"),
                "stream": msg.get("display_recipient") if msg.get("type") != "private" else None,
                "topic": msg.get("subject") if msg.get("type") != "private" else None,
                "message_id": msg.get("id"),
                "query": content,
                "prompt_sent": prompt,
                "context_count": len(context_messages),
                "ai_mode": ai_mode,
                "duration_s": duration_s,
                "exit_code": exit_code,
                "status": status,
                "stdout": raw_stdout,
                "stderr": raw_stderr,
                "response_delivered": response_text,
                "extracted_reasoning": extracted_reasoning,
                "fallback_used": fallback_used
            })
        except Exception as mex:
            logger.warning(f"Failed to record Zulip chat memory: {mex}")

        self._send_full_reply(msg, response_text)
        print(f"[thread-{tid}] [{chat_id}] Done in {duration_s}s (status: {status}).")

    def handle_message(self, msg):
        """Handle a Zulip message."""
        sender_email = msg['sender_email']

        # Don't respond to our own messages
        if sender_email == self.bot_email:
            return

        # Restrict to the owner/configured user to protect privacy.
        # ZULIP_USER may be an email, a bare username (local part), or a
        # numeric Zulip user id. Display names are never trusted: any user can
        # set theirs to the owner's name or email. With no owner configured or
        # detected, nobody is allowed (fail closed).
        allowed_user, _source = self.effective_owner()

        # Normalize values for case-insensitive comparison
        allowed_user_clean = allowed_user.strip().lower()
        sender_email_clean = sender_email.strip().lower()
        sender_username_clean = sender_email_clean.split('@')[0] if '@' in sender_email_clean else sender_email_clean

        if not allowed_user_clean:
            is_allowed = False
        elif allowed_user_clean.isdigit():
            is_allowed = (
                str(msg.get('sender_id', '')) == allowed_user_clean or
                sender_username_clean == f"user{allowed_user_clean}"
            )
        elif '@' in allowed_user_clean:
            is_allowed = allowed_user_clean == sender_email_clean
        else:
            is_allowed = allowed_user_clean == sender_username_clean

        print(f"Privacy validation - allowed: '{allowed_user_clean}', sender: '{sender_email_clean}' -> ALLOWED={is_allowed}")

        if not is_allowed:
            # Quietly ignore messages from others to protect privacy
            return

        content = msg['content'].strip()
        is_private = msg.get('type') == 'private'

        # If message is from a shared stream/channel, ONLY respond if the bot is explicitly mentioned!
        if not is_private:
            require_mention = os.environ.get("BRIDGE_REQUIRE_STREAM_MENTIONS", "1").lower() not in ("0", "false", "no")
            if require_mention:
                if not self._is_bot_mentioned(msg, content):
                    stream_name = msg.get('display_recipient', 'unknown')
                    topic_name = msg.get('subject', 'unknown')
                    logger.debug(f"Ignoring message in stream '{stream_name}' > '{topic_name}' because bot was not mentioned")
                    return
                # Strip mention so prompt/commands are clean
                content = self._strip_bot_mentions(content)

        # Built-in commands (cheap, instant, no agent round-trip)
        if content.startswith(('/help', ':help')):
            mode = self._ai_mode()
            to = os.environ.get("BRIDGE_TASK_TIMEOUT", os.environ.get("INFER_TASK_TIMEOUT", "1800"))
            self._send_reply(
                msg,
                "### 🤖 Zulip AI Bridge Commands\n\n"
                "- **`/help`** — Show this command reference\n"
                "- **`/ping`** — Quick bridge & model status check\n"
                "- **`/debug`** (or `/diag`) — System diagnostics (server health, slot state, recent error logs)\n"
                "- **`/history`** (or `/chats`) — View recent Zulip conversation audit trail\n"
                f"- **`/mode <auto|plan|manual>`** — Switch bridge permission mode (current: `{mode}`)\n"
                f"- **`/timeout <seconds>`** — Set standard task timeout (current: `{to}s`)\n"
                "- **`/wake`** (or `/restart-server`) — Wake up or restart the local LLM inference server\n"
                "- **`/long <query>`** — Execute deep research / long-running job with extended 2h timeout\n\n"
                f"*Note: In shared channels/streams, mention `@{self.bot_name}` to talk to me.*"
            )
            return

        if content.startswith(('/wake', ':wake', '/restart-server', ':restart-server')):
            self._send_reply(msg, "🔄 *Attempting to wake/restart the local inference server...*")
            ok, st = self._ensure_inference_server(msg)
            if ok:
                self._send_reply(msg, "🟢 **Inference server is ONLINE and ready!**")
            else:
                self._send_reply(
                    msg,
                    f"⚠️ **Could not wake inference server** (status: `{st}`).\n"
                    "Check host status with `ai-backend status` or `journalctl --user -u llama-server`."
                )
            return

        if content.startswith(('/ping', ':ping')):
            env = load_env_file()
            model = env.get("INFER_MODEL", "local")
            mode = self._ai_mode()
            to = os.environ.get("BRIDGE_TASK_TIMEOUT", os.environ.get("INFER_TASK_TIMEOUT", "1800"))
            self._send_reply(
                msg,
                f"🟢 **Zulip AI Bridge is active**\n"
                f"- **Permission Mode:** `{mode}`\n"
                f"- **Configured Model:** `{model}`\n"
                f"- **Task Timeout:** `{to}s`\n"
                f"- **Memory Log:** `chat_audit` table in `~/.local/share/ai/state.db`\n"
                f"*(Use `/debug` for server health or `/history` for recent chat logs)*"
            )
            return

        if content.startswith(('/history', ':history', '/chats', ':chats')):
            recent = self.memory.get_recent(limit=6)
            if not recent:
                self._send_reply(msg, "📋 *No Zulip chat history found in memory yet.*")
                return
            lines = [
                "### 📋 Recent Zulip Chat Memory",
                "| Time | Status | Dur | User Query |",
                "|------|--------|-----|------------|"
            ]
            for r in recent:
                raw_ts = r.get("timestamp", "")
                ts = raw_ts.split()[1] if " " in raw_ts else raw_ts
                st = r.get("status", "ok")
                if st in ("success", "stdout"):
                    st_badge = "🟢 ok"
                elif "fallback" in st:
                    st_badge = "🟡 fallback"
                else:
                    st_badge = f"🔴 {st}"
                dur = f"{r.get('duration_s', 0)}s"
                q = (r.get("query") or "")[:40].replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {ts} | {st_badge} | {dur} | {q} |")
            lines.append("\n*Use `/debug` for system health and detailed error traces.*")
            self._send_reply(msg, "\n".join(lines))
            return

        if content.startswith(('/debug', ':debug', '/diag', ':diag')):
            diag = self.memory.get_diagnostics()
            status_emoji = "🟢" if diag["server_alive"] else "🔴"
            lines = [
                "### 🔍 Zulip AI Bridge Diagnostics",
                f"- **Inference Server:** {status_emoji} `{diag['base_url']}` ({'ONLINE' if diag['server_alive'] else 'OFFLINE'})",
                f"- **Active Model:** `{diag['active_model']}`",
                f"- **Server Slot Status:** `{diag['slot_status']}`",
                f"- **Bridge Permission Mode:** `{self._ai_mode()}`",
                f"- **Recent Zulip Chats:** {diag['recent_count']} total ({diag['error_count']} errors, {diag['fallback_count']} fallbacks)",
            ]
            if diag["recent_errors"]:
                lines.append("\n**Recent Issues / Errors in Memory:**")
                for err in diag["recent_errors"]:
                    lines.append(
                        f"- **[{err.get('timestamp')}]** `{err.get('query', '')[:45]}`\n"
                        f"  *Status:* `{err.get('status')}` | *Duration:* `{err.get('duration_s')}s` | *Error:* `{err.get('stderr_snippet', 'None')[:100]}`"
                    )
            else:
                lines.append("\n✅ *No recent errors recorded in Zulip chat memory.*")
            self._send_reply(msg, "\n".join(lines))
            return

        if content.startswith('/mode'):
            parts = content.split()
            if len(parts) > 1:
                new_mode = parts[1].strip().lower()
                if new_mode in ("auto", "plan", "manual"):
                    os.environ["BRIDGE_AI_MODE"] = new_mode
                    self._send_reply(msg, f"✅ Bridge AI mode set to: **{new_mode}**")
                    return
            self._send_reply(
                msg,
                f"AI mode: **{self._ai_mode()}** "
                "(use `/mode auto`, `/mode plan`, or `/mode manual` to switch, or `/long <task>` for extended jobs)",
            )
            return
        if content.startswith('/timeout'):
            parts = content.split()
            if len(parts) > 1 and parts[1].isdigit():
                os.environ["BRIDGE_TASK_TIMEOUT"] = parts[1]
                self._send_reply(msg, f"✅ Default task timeout set to: **{parts[1]}s**")
                return
            default_to = os.environ.get("BRIDGE_TASK_TIMEOUT", os.environ.get("INFER_TASK_TIMEOUT", "1800"))
            long_to = os.environ.get("BRIDGE_LONG_TASK_TIMEOUT", os.environ.get("INFER_LONG_TASK_TIMEOUT", "7200"))
            self._send_reply(msg, f"⏱️ Timeouts: Standard: **{default_to}s**, Long Jobs: **{long_to}s** (use `/timeout <seconds>` to update)")
            return

        if not content.strip():
            return

        print(f"Received query from {sender_email}: {content[:200]}")

        # Dispatch to a daemon thread — the Zulip event loop returns immediately
        # and is ready to receive the next message while this one is being processed.
        # Upload downloads/parsing also happen in the worker (they can take
        # tens of seconds and must not stall the listener).
        self._dispatch(msg, content)
