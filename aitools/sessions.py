"""Message trimming (trim-messages), resume transcripts (session-transcript) and the sessions
index (state db `sessions` table)."""
import json

from . import state


_session_index_down = [False]   # set after the first failed write: skip the rest this process


def record_session(session_id, path=None, ended=None, summary=None, started=None):
    """Upsert one row of the sessions index (id, started, ended, path, summary). Called when the
    history indexer (re)indexes a saved session file; never raises. After one failure (e.g. the
    db is locked past the caller's lock budget) further calls in this process are skipped, so a
    sync over many files cannot multiply the lock wait (rows are refreshed on a later sync)."""
    if not session_id or session_id == "last" or _session_index_down[0]:
        return False
    try:
        state.session_upsert(session_id, started=started, ended=ended, path=path,
                             summary=(summary or "")[:300] or None)
        return True
    except Exception:
        _session_index_down[0] = True
        return False

# ── Context trimming (called by ai.c when messages_json exceeds the budget) ──
# Internal steering messages ai.c injects as role=user; never the real task.
_TRIM_INTERNAL_USER_PREFIXES = (
    "Please call task_complete", "Your last response was empty", "[TIMEOUT]",
    "Your last response was cut off", "[SYSTEM]", "[SYSTEM ", "[HARD STOP]",
    "[STALL]", "[INTERRUPTED", "[CONTEXT",
)
_TRIM_NOTE = "\n\n[CONTEXT NOTE: older conversation messages were trimmed to fit the model's context window.]"


def _trim_msg_size(m):
    return len(json.dumps(m, ensure_ascii=False))


def _trim_text(m):
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


def trim_messages(messages, max_bytes=60000, keep_recent_full=4, tool_stub_chars=800):
    """Shrink an OpenAI messages array to <= max_bytes (JSON chars) WITHOUT
    breaking the protocol or losing the task:
      - leading system message(s) are always kept;
      - the first user turn and the LATEST real user turn are always kept
        (dropping the latest turn made the model resume an old task);
      - an assistant message with tool_calls and its tool replies are kept or
        dropped as one unit, so no tool message is ever orphaned (API 400);
      - older tool outputs are shortened before whole units are dropped;
      - the newest unit is always kept.
    Below the budget the array is returned unchanged (keeps the server's KV /
    prompt cache prefix stable)."""
    if not isinstance(messages, list) or not messages:
        return messages
    total = sum(_trim_msg_size(m) for m in messages)
    if total <= max_bytes:
        return messages

    head = []
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        head.append(dict(messages[i]))
        i += 1
    rest = messages[i:]

    # Group into units: assistant(tool_calls) + its matching tool replies.
    units = []
    for m in rest:
        role = m.get("role")
        if role == "tool":
            if units and units[-1][0].get("role") == "assistant" and units[-1][0].get("tool_calls"):
                ids = {c.get("id") for c in units[-1][0]["tool_calls"]}
                if m.get("tool_call_id") in ids:
                    units[-1].append(m)
                    continue
            continue  # orphan tool message: drop it
        units.append([m])

    # Shorten 'think' reasoning everywhere (cheap, keeps the call itself).
    for u in units:
        a = u[0]
        if a.get("role") == "assistant" and a.get("tool_calls"):
            new_calls = []
            for call in a["tool_calls"]:
                fn = call.get("function", {})
                if fn.get("name") == "think":
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                        r = args.get("reasoning", "")
                        if len(r) > 120:
                            args["reasoning"] = r[:120] + "…"
                            call = dict(call)
                            call["function"] = dict(fn, arguments=json.dumps(args))
                    except Exception:
                        pass
                new_calls.append(call)
            u[0] = dict(a, tool_calls=new_calls)

    user_idx = [k for k, u in enumerate(units) if u[0].get("role") == "user"]
    real_user = [k for k in user_idx
                 if not _trim_text(units[k][0]).lstrip().startswith(_TRIM_INTERNAL_USER_PREFIXES)]
    anchors = set()
    if user_idx:
        anchors.add(user_idx[0])
    if real_user:
        anchors.add(real_user[-1])
    elif user_idx:
        anchors.add(user_idx[-1])
    if units:
        anchors.add(len(units) - 1)

    def _size():
        return (sum(_trim_msg_size(m) for m in head) +
                sum(_trim_msg_size(m) for u in units if u for m in u))

    # Stage 1: stub older tool outputs (all but the newest keep_recent_full units).
    cutoff = max(0, len(units) - keep_recent_full)
    for k in range(cutoff):
        if _size() <= max_bytes:
            break
        u = units[k]
        for j, m in enumerate(u):
            c = m.get("content")
            if m.get("role") == "tool" and isinstance(c, str) and len(c) > tool_stub_chars:
                u[j] = dict(m, content=c[:tool_stub_chars] +
                            "\n... [older tool output trimmed to save context]")

    # Stage 2: drop oldest non-anchor units.
    dropped = 0
    for k in range(len(units)):
        if _size() <= max_bytes:
            break
        if k in anchors or not units[k]:
            continue
        dropped += len(units[k])
        units[k] = []

    # Stage 3: still too big — shorten any oversized content in what is left
    # (e.g. a huge piped stdin in the first user turn, or a giant last tool result).
    if _size() > max_bytes:
        budget_each = max(2000, max_bytes // 4)
        for u in units:
            for j, m in enumerate(u):
                c = m.get("content")
                if isinstance(c, str) and len(c) > budget_each:
                    half = budget_each // 2
                    u[j] = dict(m, content=c[:half] + "\n... [trimmed to fit context] ...\n" + c[-half:])

    if dropped and head and isinstance(head[0].get("content"), str) \
            and _TRIM_NOTE not in head[0]["content"]:
        head[0]["content"] = head[0]["content"] + _TRIM_NOTE
    return head + [m for u in units for m in u]


# User-role messages that ai.c injects internally to steer a stalling
# model — never real user turns, so drop them from a resumed transcript.
_INTERNAL_NUDGES = (
    "Please call task_complete",
    "Your last response was empty",
    "[TIMEOUT]",
)


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multimodal: keep text parts only
        parts = [p.get("text", "") for p in content
                 if isinstance(p, dict) and p.get("type") == "text"]
        return "\n".join(t for t in parts if t)
    return ""


def session_transcript(messages):
    """Convert a saved raw messages array (from a previous run) into a clean
    user/assistant transcript suitable for resuming a conversation (the
    `session-transcript` subcommand prints one JSON object per line). Dropping the
    old system message, intermediate tool churn, and dangling tool_calls avoids the
    "assistant tool_call without tool response" API errors on the next turn."""
    out = []
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            txt = _text_of(msg.get("content"))
            if txt.strip() and not any(txt.lstrip().startswith(n) for n in _INTERNAL_NUDGES):
                out.append({"role": "user", "content": txt})
        elif role == "assistant":
            # Prefer a task_complete summary (the model's real final answer),
            # otherwise any assistant text. Ignore other tool_calls.
            summary = None
            for call in (msg.get("tool_calls") or []):
                fn = call.get("function", {})
                if fn.get("name") == "task_complete":
                    try:
                        summary = json.loads(fn.get("arguments", "{}")).get("summary")
                    except Exception:
                        summary = None
            txt = summary or _text_of(msg.get("content"))
            if txt and txt.strip():
                out.append({"role": "assistant", "content": txt})
        # system / tool roles are dropped

    # Collapse consecutive same-role messages defensively and cap length so a
    # huge prior run cannot blow up the next request's context.
    MAX_TRANSCRIPT = 40000
    total = 0
    emitted = []
    for m in out:
        total += len(m["content"])
        if total > MAX_TRANSCRIPT:
            break
        emitted.append(m)
    # Keep the most recent turns if we had to truncate from the front
    if len(emitted) < len(out):
        emitted = out[-len(emitted):] if emitted else out[-1:]
    return emitted
