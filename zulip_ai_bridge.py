#!/usr/bin/env python3
"""
Zulip to local AI CLI bridge.
Listens for messages on Zulip, runs the local `ai` agent, and posts responses back.

Each message is dispatched to its own daemon thread so the Zulip listener loop
is never blocked — you can send new messages while a previous one is still being
processed by the agent.
"""

import subprocess
import threading
import zulip
import os
import sys
import re
import requests


def clean_response(text):
    # Strip ANSI escape codes (e.g. \033[2m, \033[0m)
    ansi_escape = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
    clean = ansi_escape.sub('', text)
    # Replace long unicode horizontal lines with standard markdown horizontal rules
    clean = clean.replace("────────────────────────────────────────────", "---")
    return clean.strip()


class ZulipAiBridge:
    def __init__(self):
        # Automatically loads credentials from ~/.zuliprc
        self.client = zulip.Client()
        self.bot_email = self.client.email
        print(f"Loaded credentials for: {self.bot_email} on {self.client.base_url}")
        self.detected_owner = self._detect_owner()

    def _detect_owner(self):
        """Try to detect the owner's Zulip email/username from past private messages."""
        try:
            payload = {
                "anchor": "newest",
                "num_before": 20,
                "num_after": 0,
                "narrow": [{"operator": "is", "operand": "private"}],
                "apply_markdown": False
            }
            res = self.client.get_messages(payload)
            if res.get("result") == "success":
                messages = res.get("messages", [])
                for msg in reversed(messages):
                    sender_email = msg.get("sender_email")
                    if sender_email and sender_email != self.bot_email:
                        print(f"Detected owner from past private messages: {sender_email}")
                        return sender_email
        except Exception as e:
            print(f"Error detecting owner from messages: {e}")
        return None

    def _send_reply(self, msg, content):
        """Send a reply to the same stream/topic or private thread."""
        if msg['type'] == 'private':
            self.client.send_message({
                "type": "private",
                "to": [msg['sender_email']],
                "content": content
            })
        else:
            self.client.send_message({
                "type": "stream",
                "to": msg['display_recipient'],
                "topic": msg['subject'],
                "content": content
            })

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

        context_lines = []
        context_lines.append("---")
        context_lines.append("Recent conversation context (for reference):")
        for m in context_messages:
            sender = m.get("sender_full_name", m.get("sender_email"))
            if m.get("sender_email") == self.bot_email:
                sender = "AI (You)"
            else:
                sender = f"User ({sender})"
            body = m.get("content", "").strip()
            if "\n" in body:
                body = "\n".join("  " + line for line in body.splitlines())
            context_lines.append(f"- {sender}: {body}")
        context_lines.append("---")
        context_lines.append("Latest query/message:")
        context_lines.append(content)

        return "\n".join(context_lines)

    def _process_message(self, msg, content):
        """Run the ai agent and send the result back. Runs in a background thread."""
        tid = threading.get_ident()
        print(f"[thread-{tid}] Processing: {content[:80]}")

        # Fetch context messages and construct the prompt
        context_messages = self._get_context_messages(msg)
        prompt = self._construct_prompt_with_context(msg, content, context_messages)

        # Build a clean environment for the subprocess.
        # Inherit everything from the bridge's own env (which has the full PATH
        # and conda setup from the service file / interactive launch), then force
        # INFER_AUTO_APPROVE so execute_command never pauses for Y/n confirmation.
        run_env = os.environ.copy()
        run_env["INFER_AUTO_APPROVE"] = "1"
        run_env["INFER_RAW_OUTPUT"] = "1"

        # Run the local `ai` CLI in quiet + auto-approve mode.
        # With schedule_task properly used, the agent returns immediately for timed work.
        try:
            result = subprocess.run(
                ["ai", "-y", "-q", prompt],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=run_env,
                text=True,
                timeout=600
            )
            response_text = result.stdout
            if result.returncode != 0 and result.stderr:
                response_text += f"\n\n*Stderr:*\n```\n{result.stderr}\n```"

            response_text = clean_response(response_text)
            if not response_text:
                response_text = "*(agent returned no output)*"

        except subprocess.TimeoutExpired:
            response_text = (
                "⏱️ The agent timed out after 10 minutes. "
                "For long-running tasks, ask me to **schedule** them so they run in "
                "the background and notify you when done."
            )
        except Exception as e:
            response_text = f"⚠️ Failed to run local `ai` CLI: {str(e)}"

        self._send_reply(msg, response_text)
        print(f"[thread-{tid}] Done.")

    def handle_message(self, msg):
        sender_email = msg['sender_email']

        # Don't respond to our own messages
        if sender_email == self.bot_email:
            return

        # Restrict to the owner/configured user to protect privacy
        import getpass
        allowed_user = os.environ.get("ZULIP_USER") or self.detected_owner or getpass.getuser()
        
        # Normalize values for case-insensitive comparison
        allowed_user_clean = allowed_user.strip().lower()
        sender_email_clean = sender_email.strip().lower()
        sender_full_name_clean = msg.get('sender_full_name', '').strip().lower()
        sender_username_clean = sender_email_clean.split('@')[0] if '@' in sender_email_clean else sender_email_clean

        is_allowed = (
            allowed_user_clean == sender_email_clean or
            allowed_user_clean == sender_full_name_clean or
            allowed_user_clean == sender_username_clean or
            (allowed_user_clean.isdigit() and sender_username_clean == f"user{allowed_user_clean}") or
            (sender_username_clean.startswith("user") and sender_username_clean[4:] == allowed_user_clean)
        )

        print(f"Privacy validation - allowed: '{allowed_user_clean}', sender: '{sender_email_clean}', name: '{sender_full_name_clean}', user: '{sender_username_clean}' -> ALLOWED={is_allowed}")

        if not is_allowed:
            # Quietly ignore messages from others to protect privacy
            return

        content = msg['content'].strip()

        # If the bot is mentioned in a stream, strip the mention syntax (e.g. @**AI Bot**)
        if msg['type'] != 'private' and content.startswith('@**'):
            mention_end = content.find('**')
            if mention_end != -1:
                mention_end_close = content.find('**', mention_end + 2)
                if mention_end_close != -1:
                    content = content[mention_end_close + 2:].strip()

        # Check for Zulip uploaded files and download them locally using Basic Auth
        upload_pattern = re.compile(r'(/user_uploads/\d+/[a-zA-Z0-9]+/([^/\s)]+))')
        site_url = self.client.base_url.replace('/api/', '').replace('/api', '')
        tmp_dir = "/tmp/zulip_uploads"
        os.makedirs(tmp_dir, exist_ok=True)

        for match in upload_pattern.finditer(content):
            rel_url = match.group(1)
            filename = match.group(2)
            local_path = os.path.join(tmp_dir, filename)
            download_url = site_url + rel_url

            print(f"Downloading upload: {download_url} to {local_path}")
            try:
                r = requests.get(download_url, auth=(self.bot_email, self.client.api_key), timeout=30)
                if r.status_code == 200:
                    with open(local_path, 'wb') as f:
                        f.write(r.content)
                    content = content.replace(rel_url, local_path)
                    print(f"Successfully downloaded and mapped to local path: {local_path}")
                else:
                    print(f"Failed to download upload, HTTP status: {r.status_code}")
            except Exception as e:
                print(f"Error downloading upload: {e}")

        print(f"Received query from {sender_email}: {content}")

        # Dispatch to a daemon thread — the Zulip event loop returns immediately
        # and is ready to receive the next message while this one is being processed.
        t = threading.Thread(
            target=self._process_message,
            args=(msg, content),
            daemon=True
        )
        t.start()

    def run(self):
        print("🚀 Starting Zulip AI Bridge listener (threaded — concurrent messages supported)...")
        self.client.call_on_each_message(self.handle_message)


if __name__ == "__main__":
    try:
        bridge = ZulipAiBridge()
        bridge.run()
    except Exception as e:
        print(f"Error starting bridge: {e}")
        sys.exit(1)
