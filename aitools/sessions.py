"""Message trimming (trim-messages), resume transcripts (session-transcript) and the sessions
index (state db `sessions` table)."""
import json
import os
import re
import time

from . import state


# monotonic time of the last failed write (falsy: index up). While it is recent, writes are skipped;
# after SESSION_INDEX_RETRY_S they are retried, so a long-lived process (py engine, tool server)
# does not stay without a sessions index for its whole life after one transient lock.
_session_index_down = [0.0]
SESSION_INDEX_RETRY_S = 60.0


def record_session(session_id, path=None, ended=None, summary=None, started=None):
    """Upsert one row of the sessions index (id, started, ended, path, summary). Called when the
    history indexer (re)indexes a saved session file; never raises. After one failure (e.g. the
    db is locked past the caller's lock budget) further calls in this process are skipped, so a
    sync over many files cannot multiply the lock wait (rows are refreshed on a later sync).
    Writes are retried SESSION_INDEX_RETRY_S after the failure."""
    if not session_id or session_id == "last":
        return False
    down = _session_index_down[0]
    if down and time.monotonic() - down < SESSION_INDEX_RETRY_S:
        return False
    try:
        state.session_upsert(session_id, started=started, ended=ended, path=path,
                             summary=(summary or "")[:300] or None)
        _session_index_down[0] = 0.0
        return True
    except Exception:
        _session_index_down[0] = time.monotonic() or 1e-9
        return False

# ── Context trimming (called by ai.c when messages_json exceeds the budget) ──
# Internal steering messages both engines inject as role=user; never the real task. The single
# source for trimming and for reasoning retention (RETENTION_INTERNAL_PREFIXES below).
_TRIM_INTERNAL_USER_PREFIXES = (
    "Please call task_complete", "Your last response was empty", "[TIMEOUT]",
    "Your last response was cut off", "[SYSTEM]", "[SYSTEM ", "[HARD STOP]",
    "[STALL]", "[INTERRUPTED", "[CONTEXT",
    "Your previous reply got stuck", "Your previous reply ran past",   # agent/loop.py runaway guard
    "[GENERATION ABORTED]",
)
_TRIM_NOTE = "\n\n[CONTEXT NOTE: older conversation messages were trimmed to fit the model's context window.]"


# ── Reasoning retention (INFER_KEEP_REASONING / INFER_KEEP_REASONING_BLOCK) ──
# A turn boundary is a real user message; the harness nudges above and these two (a mid-task
# :btw note, the read_file image message) are not. ai.c mirrors this tuple in
# reasoning_internal_prefixes[] (tests/test_reasoning_retention.py asserts set equality).
RETENTION_INTERNAL_PREFIXES = _TRIM_INTERNAL_USER_PREFIXES + (
    "[User note mid-task", "Here is the image file",
)
KEEP_REASONING_DEFAULT = 2
KEEP_REASONING_BLOCK_DEFAULT = 4
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)


def parse_keep_reasoning(value):
    """INFER_KEEP_REASONING: "all" or a negative number -> -1 (keep everything), N >= 0 -> N;
    unset, empty or unparsable -> None (caller's default)."""
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    if v == "all":
        return -1
    try:
        n = int(v)
    except ValueError:
        return None
    return -1 if n < 0 else n


def keep_reasoning_from_env(preserve_thinking=False, environ=None):
    """The effective setting: an explicit INFER_KEEP_REASONING wins; else --preserve-thinking /
    INFER_PRESERVE_THINKING (the user asked the template to keep past thinking) means all;
    else KEEP_REASONING_DEFAULT."""
    env = os.environ if environ is None else environ
    n = parse_keep_reasoning(env.get("INFER_KEEP_REASONING"))
    if n is not None:
        return n
    return -1 if preserve_thinking else KEEP_REASONING_DEFAULT


def keep_block_from_env(environ=None):
    """INFER_KEEP_REASONING_BLOCK: the hysteresis block B (>= 0; 0 = strict sliding window);
    unset or unparsable -> KEEP_REASONING_BLOCK_DEFAULT."""
    env = os.environ if environ is None else environ
    try:
        b = int(str(env.get("INFER_KEEP_REASONING_BLOCK")).strip())
    except (TypeError, ValueError):
        return KEEP_REASONING_BLOCK_DEFAULT
    return b if b >= 0 else KEEP_REASONING_BLOCK_DEFAULT


def strip_think(text):
    """Drop inline thinking from assistant text: every <think>...</think> block, then anything up
    to a remaining unmatched </think> (templates that open the block in the prompt). Leading
    whitespace left behind is removed. ai.c strip_think_inline() is the same algorithm."""
    if not isinstance(text, str) or "think>" not in text:
        return text
    out = _THINK_BLOCK.sub("", text)
    k = out.rfind("</think>")
    if k >= 0:
        out = out[k + len("</think>"):]
    if out == text:
        return text
    return out.lstrip(" \t\r\n")


def _is_turn_boundary(m):
    return (m.get("role") == "user"
            and not _trim_text(m).lstrip().startswith(RETENTION_INTERNAL_PREFIXES))


def stripped_in_turn(m, keep, block):
    """How many of the current turn's first assistant messages lose their reasoning when the turn
    has `m` of them. keep 0: all; block 0: a strict sliding window (m - keep); otherwise block
    hysteresis: the kept count grows from keep to keep+block-1, then drops back to keep in one
    step, so the stripped prefix (and the server's prompt cache) changes once every `block` steps.
    Monotonic in m: a message once stripped stays stripped."""
    if keep <= 0:
        return m
    if m <= keep:
        return 0
    if block <= 0:
        return m - keep
    return (m - keep) // block * block


def _reasoning_keep_set(messages, keep, block):
    """Indices of the assistant messages whose reasoning is kept: those of the current turn (after
    the latest real user message) past the first stripped_in_turn() of them."""
    last_user = -1
    for i, m in enumerate(messages):
        if isinstance(m, dict) and _is_turn_boundary(m):
            last_user = i
    turn = [i for i in range(last_user + 1, len(messages))
            if isinstance(messages[i], dict) and messages[i].get("role") == "assistant"]
    return set(turn[stripped_in_turn(len(turn), keep, block):])


def _strip_reasoning_msg(m):
    """The message without reasoning_content and inline <think> blocks; the same object when
    there is nothing to strip (so unchanged messages serialise identically)."""
    c = m.get("content")
    c2 = strip_think(c)
    if "reasoning_content" not in m and c2 is c:
        return m
    out = {k: v for k, v in m.items() if k != "reasoning_content"}
    if c2 is not c:
        out["content"] = c2
    if out.get("content") is None and not out.get("tool_calls"):
        out["content"] = ""      # a reasoning-only reply: never send an empty null-content message
    return out


def request_view(messages, keep, block=None):
    """The messages to put on the wire: reasoning kept only on the recent assistant messages of
    the current turn -- `keep`, growing to keep+block-1 before the oldest `block` are stripped at
    once (stripped_in_turn; block None: INFER_KEEP_REASONING_BLOCK). keep < 0: everything, returns
    `messages` itself. The input list and its dicts are never modified -- the history and the
    saved session keep all reasoning. Stripping is a pure function of the history and monotonic,
    so a message once stripped stays stripped: the request prefix only changes once per block."""
    if keep is None or keep < 0 or not isinstance(messages, list):
        return messages
    if block is None:
        block = keep_block_from_env()
    kept = _reasoning_keep_set(messages, keep, block)
    out = []
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "assistant" and i not in kept:
            m = _strip_reasoning_msg(m)
        out.append(m)
    return out


def _trim_msg_size(m):
    return len(json.dumps(m, ensure_ascii=False))


def _trim_text(m):
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


def trim_messages(messages, max_bytes=60000, keep_recent_full=4, tool_stub_chars=800, keep_reasoning=None):
    """Shrink an OpenAI messages array to <= max_bytes (JSON chars) WITHOUT
    breaking the protocol or losing the task:
      - leading system message(s) are always kept;
      - the first user turn and the LATEST real user turn are always kept
        (dropping the latest turn made the model resume an old task);
      - an assistant message with tool_calls and its tool replies are kept or
        dropped as one unit, so no tool message is ever orphaned (API 400);
      - older tool outputs are shortened before whole units are dropped;
      - the newest unit is always kept;
      - old reasoning goes first: reasoning_content / inline <think> of every assistant message
        but the last `keep_reasoning` of the current turn is stripped before any tool output is
        shortened (keep_reasoning None: INFER_KEEP_REASONING / default; < 0 ("all"): never).
    Below the budget the array is returned unchanged (keeps the server's KV /
    prompt cache prefix stable)."""
    if not isinstance(messages, list) or not messages:
        return messages
    total = sum(_trim_msg_size(m) for m in messages)
    if total <= max_bytes:
        return messages
    if keep_reasoning is None:
        keep_reasoning = keep_reasoning_from_env()

    # Stage 0: strip old reasoning down to the strict window (no hysteresis block: trimming
    # breaks the prompt cache anyway, and the history is changed for good).
    if keep_reasoning >= 0:
        messages = request_view(messages, keep_reasoning, block=0)
        if sum(_trim_msg_size(m) for m in messages) <= max_bytes:
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
    "Your previous reply got stuck", "Your previous reply ran past", "[GENERATION ABORTED]",
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
