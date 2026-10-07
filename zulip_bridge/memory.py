"""Durable audit trail of Zulip conversations."""

import os
import json
import time
import logging
import threading

import requests

from .config import load_env_file


logger = logging.getLogger("ZulipBridge")



class ZulipMemoryManager:
    """
    Manages a durable audit trail and memory of Zulip conversations.
    Records every incoming message, context, subprocess execution, stdout/stderr,
    fallbacks used, delivered reply, duration, and status.
    Provides querying and diagnostic tools.
    """
    def __init__(self, storage_dir=None):
        if storage_dir is None:
            self.storage_dir = os.path.expanduser("~/.local/share/ai/zulip_chats")
        else:
            self.storage_dir = storage_dir
        self.jsonl_path = os.path.join(os.path.dirname(self.storage_dir), "zulip_chats.jsonl")
        self.cache_jsonl_path = os.path.expanduser("~/.cache/ai/zulip_chats.jsonl")
        os.makedirs(self.storage_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.cache_jsonl_path), exist_ok=True)
        self._lock = threading.Lock()

    def record_chat(self, chat_data):
        """Append a chat record atomically to memory files and write full session json."""
        session_id = chat_data.get("session_id")
        if not session_id:
            session_id = f"zulip_{int(time.time())}_{os.getpid()}"
            chat_data["session_id"] = session_id

        # Write detailed session file
        try:
            session_file = os.path.join(self.storage_dir, f"{session_id}.json")
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(chat_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to write detailed zulip chat session file: {e}")

        # Write summary record to JSONL logs
        summary_record = {
            "session_id": session_id,
            "timestamp": chat_data.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S %Z")),
            "sender_email": chat_data.get("sender_email"),
            "sender_full_name": chat_data.get("sender_full_name"),
            "type": chat_data.get("type"),
            "stream": chat_data.get("stream"),
            "topic": chat_data.get("topic"),
            "message_id": chat_data.get("message_id"),
            "query": chat_data.get("query"),
            "context_count": chat_data.get("context_count", 0),
            "ai_mode": chat_data.get("ai_mode", "auto"),
            "duration_s": chat_data.get("duration_s", 0),
            "exit_code": chat_data.get("exit_code"),
            "status": chat_data.get("status", "unknown"),
            "reply_snippet": (chat_data.get("response_delivered") or "")[:200],
            "stderr_snippet": (chat_data.get("stderr") or "")[:200],
            "extracted_reasoning": bool(chat_data.get("extracted_reasoning")),
            "fallback_used": chat_data.get("fallback_used")
        }

        line = json.dumps(summary_record, ensure_ascii=False) + "\n"
        with self._lock:
            for p in (self.jsonl_path, self.cache_jsonl_path):
                try:
                    with open(p, "a", encoding="utf-8") as f:
                        f.write(line)
                except Exception as e:
                    logger.warning(f"Failed to append to {p}: {e}")

        return session_id

    def get_recent(self, limit=10, error_only=False):
        """Get recent chat records from the JSONL log."""
        records = []
        path = self.jsonl_path if os.path.exists(self.jsonl_path) else self.cache_jsonl_path
        if not os.path.exists(path):
            return records
        try:
            # The log grows forever; only read its tail (grow the window if
            # it does not yet contain enough records).
            lines = []
            window = 256 * 1024
            size = os.path.getsize(path)
            while True:
                with open(path, "rb") as f:
                    f.seek(max(0, size - window))
                    data = f.read()
                lines = data.decode("utf-8", errors="replace").splitlines()
                if size > window:
                    lines = lines[1:]  # first line is probably partial
                if size <= window or len(lines) >= limit * 4:
                    break
                window *= 4
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if error_only and data.get("status") in ("success", "stdout", "fallback_reasoning", "fallback_tools"):
                        continue
                    records.append(data)
                    if len(records) >= limit:
                        break
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Failed to read zulip chat history: {e}")
        return records

    def get_chat(self, session_id):
        """Retrieve the full record for a specific chat."""
        session_file = os.path.join(self.storage_dir, f"{session_id}.json")
        if os.path.exists(session_file):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        for r in self.get_recent(limit=100):
            if r.get("session_id") == session_id:
                return r
        return None

    def get_diagnostics(self):
        """Compute diagnostic status of the bridge and recent chat health."""
        recent = self.get_recent(limit=20)
        total = len(recent)
        errors = [r for r in recent if r.get("status") in ("error", "empty_output", "timeout")]
        fallbacks = [r for r in recent if "fallback" in r.get("status", "")]
        
        env = load_env_file()
        base_url = env.get("INFER_BASE_URL", "http://localhost:8080/v1/")
        server_alive = False
        active_model = env.get("INFER_MODEL", "unknown")
        slot_status = "unknown"
        try:
            r = requests.get(base_url.rstrip("/") + "/models", timeout=3)
            if r.status_code == 200:
                server_alive = True
                mdata = r.json()
                if "data" in mdata and mdata["data"]:
                    active_model = mdata["data"][0].get("id", active_model)
        except Exception:
            server_alive = False

        try:
            sr = requests.get(base_url.rstrip("/") + "/../slots", timeout=2)
            if sr.status_code == 200:
                sjson = sr.json()
                if isinstance(sjson, list) and sjson:
                    is_busy = sjson[0].get("is_processing", False)
                    slot_status = "BUSY (processing)" if is_busy else "IDLE"
        except Exception:
            pass

        return {
            "server_alive": server_alive,
            "base_url": base_url,
            "active_model": active_model,
            "slot_status": slot_status,
            "recent_count": total,
            "error_count": len(errors),
            "fallback_count": len(fallbacks),
            "recent_errors": errors[:3]
        }
