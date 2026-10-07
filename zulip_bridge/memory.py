"""Durable audit trail of Zulip conversations."""

import os
import json
import time
import logging

import requests

from .config import load_env_file


class _LazyState:
    """aitools.state, imported on first use (aitools is installed beside zulip_bridge in
    ~/.local/share/ai/lib; importing the bridge package alone must not require it)."""

    def __getattr__(self, name):
        from aitools import state as _state
        return getattr(_state, name)


state = _LazyState()

logger = logging.getLogger("ZulipBridge")



class ZulipMemoryManager:
    """
    Manages a durable audit trail and memory of Zulip conversations.
    Each exchange's summary record goes to the state db's `chat_audit` table (aitools/state.py,
    90-day retention); the full per-chat record (context, stdout/stderr, fallbacks, reply) is
    also written to <storage_dir>/<session_id>.json. Provides querying and diagnostic tools.
    """
    OK_STATUSES = ("success", "stdout", "fallback_reasoning", "fallback_tools")

    def __init__(self, storage_dir=None):
        if storage_dir is None:
            self.storage_dir = os.path.expanduser("~/.local/share/ai/zulip_chats")
        else:
            self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)

    def record_chat(self, chat_data):
        """Write the full session json and append a summary row to the chat audit."""
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
        try:
            state.chat_audit_add("zulip", summary_record["sender_email"], summary_record["query"],
                                 (chat_data.get("response_delivered") or "")[:4000],
                                 session_id=session_id, status=summary_record["status"],
                                 meta=summary_record)
        except Exception as e:
            logger.warning(f"Failed to record zulip chat audit row: {e}")
        return session_id

    @staticmethod
    def _summary(row):
        rec = dict(row.get("meta") or {})
        rec.setdefault("session_id", row.get("session_id"))
        rec.setdefault("query", row.get("prompt"))
        rec.setdefault("sender_email", row.get("sender"))
        rec.setdefault("status", row.get("status"))
        rec.setdefault("reply_snippet", (row.get("reply") or "")[:200])
        rec.setdefault("timestamp", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row.get("ts") or 0)))
        return rec

    def get_recent(self, limit=10, error_only=False):
        """Most recent chat summary records, newest first."""
        try:
            rows = state.chat_audit_recent(limit=limit, source="zulip",
                                           exclude_status=self.OK_STATUSES if error_only else ())
        except Exception as e:
            logger.error(f"Failed to read zulip chat history: {e}")
            return []
        return [self._summary(r) for r in rows]

    def get_chat(self, session_id):
        """Retrieve the full record for a specific chat."""
        session_file = os.path.join(self.storage_dir, f"{session_id}.json")
        if os.path.exists(session_file):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        try:
            rows = state.chat_audit_recent(limit=1, session_id=session_id, source="zulip")
        except Exception:
            rows = []
        return self._summary(rows[0]) if rows else None

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
