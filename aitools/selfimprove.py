"""Self-improvement: skills editing, failure ledger, lessons and session recap."""
import os
import json
import time
import sqlite3
from . import learning, state
from .history import (  # noqa: F401
    HISTORY_DB,
    _ensure_history_schema,
    _sync_history_index,
)

# ── Continuous self-improvement: skill create/update/note ─────────────────────
# The agent persists what it learns into skills so future sessions inherit it.
# Skills are written to the project .agents/skills (checked into the repo) AND
# to ~/.config/ai/skills (global, persistent). Returns a machine-recognisable
# marker "[SKILL_CREATED:name]" / "[SKILL_UPDATED:name]" / "[SKILL:note:name]"
# that ai.c surfaces to the user as a notification.

def _skill_targets(skill_name):
    """Return [(path, base_dir)] write targets for a skill (each a <dir>/SKILL.md),
    project first so it's checked into the repo."""
    san = skill_name.replace("/", "_").replace("\\", "_").replace("..", "_").strip()
    if not san:
        san = "learned"
    targets = []
    proj = os.path.join(os.getcwd(), ".agents", "skills", san, "SKILL.md")
    glob = os.path.join(os.path.expanduser("~"), ".config", "ai", "skills", san, "SKILL.md")
    if proj != glob:
        targets.append((proj, "project"))
    targets.append((glob, "global"))
    return targets, san


def _learning_log_path():
    return os.path.join(os.path.expanduser("~"), ".config", "ai", "skills_learning_log.md")


def _append_learning_log(entry):
    try:
        path = _learning_log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n## {ts}\n{entry}\n")
    except Exception as e:
        return f"(log write failed: {e})"
    return ""


def skill_create(name, description, content):
    """Create (or overwrite) a skill. Returns a [SKILL_CREATED:name] marker."""
    targets, san = _skill_targets(name)
    if not description:
        description = "Learned during an ai session; auto-generated skill."
    body = (
        "---\n"
        f"name: {san}\n"
        f"description: {description}\n"
        "---\n\n"
        f"# {san}\n\n"
        f"{content}\n"
    )
    wrote = []
    for path, _where in targets:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            wrote.append(path)
        except Exception as e:
            return f"Error writing skill {name}: {e}"
    _append_learning_log(f"Created skill **{san}** (wrote to {len(wrote)} location(s)).\n\n```\n{description}\n```\n\n{content[:1000]}")
    joined = ", ".join(wrote)
    return f"[SKILL_CREATED:{san}]\nSkill '{san}' created/updated.\nSaved to: {joined}\n\nDescription: {description}"


def skill_update(name, note):
    """Update an existing skill with a note (good-to-know / discrepancy fix).
    Appends a 'Recent learning' section; returns a [SKILL_UPDATED:name] marker."""
    targets, san = _skill_targets(name)
    updated = 0
    for path, _where in targets:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            if "## Recent learning" not in content:
                content += "\n\n## Recent learning\n"
            content += f"\n- [{ts}] {note}\n"
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            updated += 1
        except Exception as e:
            return f"Error updating skill {name}: {e}"
    if updated == 0:
        # Skill doesn't exist yet anywhere; create it as a note.
        return skill_create(name, f"Auto-generated from learning note: {note[:140]}", note)
    _append_learning_log(f"Updated skill **{san}** ({updated} location(s)): {note}")
    return f"[SKILL_UPDATED:{san}]\nSkill '{san}' updated in {updated} location(s).\nRecorded learning: {note}"


def skill_note(name, note):
    """Attach a standalone learning note to a skill's learning log without touching its body."""
    _append_learning_log(f"Note for skill **{name or 'general'}**: {note}")
    return f"[SKILL:note:{name or 'general'}]\nNoted: {note}\n(Stored in {_learning_log_path()})"


def list_skills_dir():
    """Return human-readable index of all skills in project + global skill dirs."""
    bases = [
        os.path.join(os.getcwd(), ".agents", "skills"),
        os.path.join(os.path.expanduser("~"), ".config", "ai", "skills"),
    ]
    seen = set()
    lines = []
    for base in bases:
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            if entry in seen:
                continue
            if os.path.isfile(os.path.join(base, entry, "SKILL.md")):
                lines.append(entry)
                seen.add(entry)
    return "Available skills:\n" + ("\n".join(lines) if lines else "(none yet — use skill_create to save what you learn)")


# ── Automatic failure-learning ledger ────────────────────────────────────────
# The C harness records tool failures deterministically, without relying on the
# model choosing to persist anything. This solves "the agent makes the same
# mistake twice and doesn't remember making it." Every failure goes to the
# state-db `ledger` table; recurring signatures auto-promote to a `lessons` row
# (rendered as the old lessons.md markdown blocks); when a tool
# that previously failed later succeeds, the working approach is auto-learned
# as a FIX lesson. On a future error, `lessons_for` returns the stored lessons
# so the harness can surface the memory right where the model needs it.
import re as _re

def _si_dir():
    """Legacy location of ledger.jsonl/lessons.md (migrated into the state db; not written)."""
    return os.path.join(os.path.expanduser("~"), ".config", "ai", "self_improve")

def _si_recurrence_threshold():
    raw = os.environ.get("INFER_SELF_IMPROVE_RECURRENCE", "2")
    try:
        return max(1, int(raw))
    except Exception:
        return 2

def _err_signature(tool, error):
    """Normalise an error into a stable dedupe key: tool + stripped/normalised
    error tokens (numbers collapsed) so the SAME mistake is recognised across
    sessions even when paths/ids differ."""
    s = ("%s %s" % ((tool or ""), (error or ""))).lower()
    s = _re.sub(r"\d+", "#", s)
    s = _re.sub(r"[^a-z0-9#%]+", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s

def _ledger_signature_count(signature):
    try:
        return state.ledger_count(signature=signature)
    except Exception:
        return 0

def _block_tool(block):
    for line in block.splitlines():
        if line.lower().startswith("tool:"):
            return line.split(":", 1)[1].strip().lower()
    return ""

def _has_lesson(kind, tool, signature):
    """True if a lesson of this KIND already exists for this exact signature
    (or, with no signature, for this tool). Matched per block: the old
    whole-file check ('any ## KIND exists' AND 'tool: X appears anywhere')
    meant that after the first lesson for a tool, no further distinct
    lesson for that tool was ever written — learning silently saturated."""
    try:
        return state.lesson_exists(kind, tool=tool, signature=signature)
    except Exception:
        return True  # state db unusable: claim it exists so nothing tries to write a lesson

# Errors the HARNESS itself produced (loop guards, caches, budget/plan gates,
# context stubs). They describe the agent's control flow, not a tool's
# behaviour, so learning from them only poisons future sessions (e.g. a
# "FIX: list_directory stuck-in-a-loop -> succeeded with the same args").
_SI_HARNESS_ERROR_MARKERS = (
    "You are stuck in a loop", "[HARD STOP]",
    "You have already called the 'think' tool", "You have called 'think'",
    "Your 'think' reasoning was very long",
    "context limit reached", "result omitted to preserve",
    "[PLAN MODE]", "denied by user", "cancelled/denied by the user",
    "Model returned an empty tool name", "[TIMEOUT]", "[INTERRUPTED",
)

def _is_harness_error(error):
    e = error or ""
    return any(m.lower() in e.lower() for m in _SI_HARNESS_ERROR_MARKERS)

_SI_LEDGER_MAX_LINES = state.LEDGER_MAX_ROWS

def _ledger_append(rec):
    """Append one ledger record (dict in the old JSONL shape) to the state db; the store keeps
    the ledger bounded at _SI_LEDGER_MAX_LINES rows."""
    kind = rec.get("kind") or "failure"
    state.ledger_append(tool=rec.get("tool", ""), ok=(kind == "recovery"),
                        error=rec.get("error") or rec.get("prior_error") or "",
                        chain_id=rec.get("chain_id", ""),
                        args=rec.get("args") or rec.get("approach") or "",
                        kind=kind, signature=rec.get("signature", ""), phase=rec.get("phase", ""),
                        max_rows=_SI_LEDGER_MAX_LINES)

def _append_lesson(kind, tool, signature, body):
    """Best-effort lesson proposal (stored `proposed` until a human approves; see aitools/learning.py).
    A repeat reinforces the existing row (hits, expiry) instead of adding one. Returns True when a new
    row was created, False when an existing one was reinforced, None when the state db refused it."""
    try:
        return learning.propose_ex(kind, tool or "", signature or "", body)[1]
    except Exception:
        return None  # learning is best-effort; never break the tool call that triggered it

def _chain_id(tool, error):
    """Stable id for an ERROR CHAIN: the same mistake, however many times it
    recurs across sessions. This is the unit of the self-improvement loop:
    failure -> recovery -> (repeat) -> promotion to a permanent lesson."""
    return "chain_" + _err_signature(tool, error)[:80].replace(" ", "_")

def _read_ledger(chain_id=None):
    """Ledger rows (dicts: ts, kind, tool, chain_id, signature, error, ok, args_hash, phase)."""
    try:
        return state.ledger_rows(chain_id=chain_id)
    except Exception:
        return []

def record_failure(tool="", args="", error="", phase="execution", chain_id=None):
    """Record a failed tool call in the ledger, linked to its error chain.

    Returns (recurring_bool, lesson_text). If the SAME failure signature has
    now been seen >= INFER_SELF_IMPROVE_RECURRENCE times and isn't already
    captured, an auto-generated recurring-pitfall lesson is persisted and
    returned so the harness can surface it immediately."""
    if os.environ.get("INFER_PRIVATE_MODE") in ("1", "true"):
        return False, ""
    tool = (tool or "").strip()
    error = (error or "").strip()
    if not tool or not error or _is_harness_error(error):
        return False, ""
    sig = _err_signature(tool, error)
    cid = chain_id or _chain_id(tool, error)
    rec = {"kind": "failure", "tool": tool,
           "args": (args or "")[:200], "error": error[:300],
           "signature": sig, "chain_id": cid, "phase": phase}
    try:
        _ledger_append(rec)
    except Exception as e:
        return False, "Error writing failure ledger: %s" % e
    n = _ledger_signature_count(sig)
    thresh = _si_recurrence_threshold()
    if n >= thresh:
        body = ("Recurring pitfall (%dx and counting): `%s` failed with:\n"
                "> %s\n"
                "Before retrying the identical call, look up the '## FIX' "
                "lessons for this tool and change your approach."
                % (n, tool or "?", (error or "?")[:160]))
        if _append_lesson("PITFALL", tool, sig, body):  # new row only; repeats just reinforce it
            return True, "[RECURRING FAILURE] " + body
    return False, ""

def record_recovery(tool="", args="", prior_error="", phase="execution", chain_id=None,
                    failed_args=None):
    """Record that the model recovered from a prior failure of the SAME tool.

    Harness-driven (no model discipline required): the C loop notices a failed
    call to a tool, then later a succeeding call to the same tool, and persists
    the working approach as a FIX lesson. Each recovery is linked to the error
    chain; once a chain is reliably mastered (>= INFER_CHAIN_MASTERED, default
    2 recoveries, with no failure after the last recovery) it is promoted to a
    permanent MASTER lesson that future sessions see at startup."""
    if os.environ.get("INFER_PRIVATE_MODE") in ("1", "true"):
        return ""
    tool = (tool or "").strip()
    prior_error = (prior_error or "").strip()
    args = (args or "").strip()
    if not tool or not prior_error or _is_harness_error(prior_error):
        return ""
    if failed_args is not None and failed_args.strip() == args:
        # Same call failed then succeeded: transient or harness-made error,
        # not a change of approach — nothing worth teaching future sessions.
        return ""
    sig = _err_signature(tool, prior_error)
    cid = chain_id or _chain_id(tool, prior_error)
    try:
        _ledger_append({"kind": "recovery", "tool": tool,
                        "approach": args[:200],
                        "prior_error": prior_error[:300],
                        "signature": sig, "chain_id": cid,
                        "phase": phase})
    except Exception as e:
        return "Error writing recovery ledger: %s" % e
    lesson_text = ("`%s` failed (%s) then succeeded with approach: `%s`."
                   % (tool or "?", (prior_error or "?")[:160], args[:200]))
    _append_lesson("FIX", tool, sig, lesson_text)
    if _chain_is_mastered(cid):
        body = ("`%s` error chain resolved %dx and never broke again: %s. "
                "When this error appears, apply the recorded approach first."
                % (tool or "?", _chain_stats(cid)["recovered"],
                   (prior_error or "?")[:160]))
        if _append_lesson("MASTER", tool, cid, body):
            _master_skill_note(tool, cid, prior_error, args)
    return lesson_text

def _master_skill_note(tool, chain_id, prior_error, approach):
    """When a chain is promoted to MASTER, also drop a learning note into the
    skill system (the closed loop: error chain mastered -> persisted skill), so
    a human (or the model via skill_update) can fold the proven fix into a
    durable skill. Gated by INFER_MASTER_SKILL_NOTE (default on)."""
    if os.environ.get("INFER_MASTER_SKILL_NOTE", "1") in ("0", "false"):
        return
    try:
        san = _re.sub(r"[^a-z0-9_]+", "_", (tool or "learned").lower()).strip("_") or "learned"
        note = ("[MASTERED ERROR CHAIN %s] `%s` resolved this recurring error and the fix held: %s. "
                "Proven approach: `%s`. Consider skill_update('%s') to fold this into a durable skill."
                % (chain_id[:40], tool or "?", (prior_error or "?")[:120], (approach or "")[:120], san))
        _append_learning_log(note)
    except Exception:
        pass

def _chain_stats(chain_id):
    """Count failures/recoveries of one error chain from the ledger."""
    st = {"failures": 0, "recovered": 0, "last_event": ""}
    for rec in _read_ledger(chain_id=chain_id):
        if rec.get("kind") == "failure":
            st["failures"] += 1
            st["last_event"] = "failure"
        elif rec.get("kind") == "recovery":
            st["recovered"] += 1
            st["last_event"] = "recovery"
    return st

def _chain_is_mastered(chain_id):
    """A chain is mastered once it has >= INFER_CHAIN_MASTERED recoveries
    (default 2) and its most recent ledger event is a recovery (the fix held)."""
    raw = os.environ.get("INFER_CHAIN_MASTERED", "2")
    try:
        need = max(1, int(raw))
    except Exception:
        need = 2
    st = _chain_stats(chain_id)
    return st["recovered"] >= need and st["last_event"] == "recovery"

def _block_signature(block):
    """Extract the normalised 'signature:' line from a lesson block, if present."""
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("signature:"):
            return stripped.split(":", 1)[1].strip().lower()
    return ""

def _sig_error_tokens(sig, tool=""):
    """Return the set of tokens in a signature that are NOT just the tool name.

    This removes the tool-name words so that overlap is driven by the actual
    error content rather than the shared tool name."""
    tool_words = set((tool or "").lower().replace("_", " ").split())
    return set(sig.split()) - tool_words - {"error", "failed", "the", "a", "an"}

def _lesson_blocks():
    """Approved, unexpired lessons as markdown blocks ('## KIND ts / tool: / signature: / > body'),
    oldest first — the shape lessons_for/session_recap match on."""
    try:
        return [state.lesson_block(r) for r in state.lessons_list(status="approved")]
    except Exception:
        return []

def lessons_for(tool="", error=""):
    """Return persisted lessons relevant to a tool name / error signature.

    Matches lessons whose error signature overlaps the current error meaningfully.
    A lesson is surfaced only when:
      1. The exact current signature appears verbatim in the block (precise match), OR
      2. The block's stored `signature:` line shares enough error-specific tokens
         with the current error signature (Jaccard similarity >= 0.3 on tokens
         beyond the shared tool name).

    Pure tool-name-only matches are intentionally suppressed to prevent
    irrelevant lessons from previous sessions polluting the current session.
    Returns '' when nothing matches, else a compact markdown block (capped ~1600 chars)."""
    blocks = _lesson_blocks()
    if not blocks:
        return ""
    target_tool = (tool or "").strip().lower()
    sig = _err_signature(tool, error)
    # Pre-compute current error tokens (stripped of tool-name words)
    cur_err_tokens = _sig_error_tokens(sig, target_tool) if sig else set()
    hits, seen = [], set()
    for b in blocks:
        key = b.splitlines()[0]
        if key in seen:
            continue
        # Exact verbatim match of full current signature inside the block
        sig_match = bool(sig and sig in b.lower())
        # Token-overlap match: compare error-specific tokens of stored signature
        # against current error tokens (ignores tool-name-only overlap)
        overlap_match = False
        if not sig_match and target_tool and _block_tool(b) == target_tool:
            stored_sig = _block_signature(b)
            if stored_sig:
                stored_err_tokens = _sig_error_tokens(stored_sig, target_tool)
                if cur_err_tokens and stored_err_tokens:
                    intersection = cur_err_tokens & stored_err_tokens
                    union = cur_err_tokens | stored_err_tokens
                    jaccard = len(intersection) / len(union) if union else 0.0
                    overlap_match = jaccard >= 0.3
        if sig_match or overlap_match:
            seen.add(key)
            hits.append(b)
    if not hits:
        return ""
    joined = "\n".join(hits)
    if len(joined) > 1600:
        joined = joined[:1600] + "\n... (truncated)"
    return joined

def _recent_sessions_brief(limit=3):
    """One-line-per-recent-session summary from the history index (what was
    done lately). Tolerates a missing/empty index — returns ''."""
    try:
        import datetime as _dt
        conn = sqlite3.connect(HISTORY_DB)
        _ensure_history_schema(conn)
        _sync_history_index(conn)
        rows = conn.execute(
            "SELECT session_id, turns, mtime, substr(body,1,120) FROM history_sessions "
            "ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
    except Exception:
        return ""
    lines = []
    for sid, turns, mt, body in rows:
        when = _dt.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M") if mt else "?"
        p = (body or "").strip().replace("\n", " ")
        if len(p) > 90:
            p = p[:90] + "\u2026"
        lines.append("- %s (%s turns, %s): %s" % (sid, turns or 0, when, p or "(no summary)"))
    return "\n".join(lines)

def _tool_health_brief(limit=4):
    """Flakiest tools from ~/.cache/ai/metrics.jsonl (tool-health view of the
    self-improvement loop). Returns '' when nothing is flaky."""
    metrics_file = os.path.expanduser("~/.cache/ai/metrics.jsonl")
    if not os.path.isfile(metrics_file):
        return ""
    stats = {}
    try:
        with open(metrics_file, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                t = d.get("tool", "?")
                s = stats.setdefault(t, [0, 0])  # [fails, count]
                s[1] += 1
                if not d.get("success", True):
                    s[0] += 1
    except Exception:
        return ""
    flaky = []
    for t, (fails, count) in stats.items():
        if count >= 5 and fails / count >= 0.3:
            flaky.append((fails / count, t, fails, count))
    if not flaky:
        return ""
    lines = []
    for rate, t, fails, count in sorted(flaky, reverse=True)[:limit]:
        lines.append("- %s: %d/%d calls failed (%.0f%%)" % (t, fails, count, rate * 100))
    return "\n".join(lines)

def _metrics_lesson_sync(min_fails=3):
    """Close the metrics -> lessons loop (tool health).

    Scans ~/.cache/ai/metrics.jsonl and, for every tool+error signature seen
    >= min_fails times that has no lesson yet, writes a PITFALL lesson so the
    harness surfaces it on the next occurrence and in the session recap.
    This makes flaky tools that the model 'succeeds' at working around still
    feed the self-improvement loop (the follow-up the chains doc called for).
    Returns the list of newly created lesson signatures (empty when nothing
    new). Deterministic and cheap: one pass over the metrics file."""
    metrics_file = os.path.expanduser("~/.cache/ai/metrics.jsonl")
    if not os.path.isfile(metrics_file):
        return []
    counts = {}
    try:
        with open(metrics_file, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("success", True):
                    continue
                t = (d.get("tool") or "?").strip()
                err = str(d.get("error") or "").strip()
                if not err:
                    continue
                sig = _err_signature(t, err)
                counts[(t, sig, err[:160])] = counts.get((t, sig, err[:160]), 0) + 1
    except Exception:
        return []
    created = []
    for (t, sig, err), n in counts.items():
        if n < min_fails:
            continue
        body = ("Metric-detected recurring failure (%dx so far): `%s` failed with:\n"                "> %s\n"                "This tool is flaky for this error — check '## FIX' lessons and "\
                "show-metrics before retrying the same call." % (n, t or "?", err))
        res = _append_lesson("PITFALL", t, sig, body)
        if res is None:
            break  # db locked/unwritable: don't wait once per lesson (session-recap is on ai's startup path)
        if res:
            created.append(sig)
    return created

def metrics_lesson_sync(min_fails=3):
    """CLI/model-facing wrapper around _metrics_lesson_sync."""
    created = _metrics_lesson_sync(min_fails=min_fails)
    if created:
        return ("Recorded %d new metric-derived PITFALL lesson(s):\n%s"
                % (len(created), "\n".join("- %s" % c[:80] for c in created)))
    return "No new recurring metric errors to lesson-ify (already covered or below threshold)."

def session_recap(max_chars=1600):
    """Session-start recapitulation: the compact digest the harness injects
    into the system prompt before the first turn, so every session starts from
    what past sessions learned and did (Obsidian-style 'what was done, and
    what I must not forget').

    Sections (each omitted when empty; whole output capped at max_chars):
      - MASTERED error chains (promoted fixes — apply first when the error hits)
      - Recent lessons (newest FIX/PITFALL blocks)
      - Recent sessions (from the searchable history index)
      - Flaky tools (tool-health view from metrics)"""
    # Close the tool-health loop: turn recurring metric errors into PITFALL
    # lessons before building the recap, so the flaky-tools section is backed
    # by actionable lessons (not just a warning). Gated, cheap, deterministic.
    if os.environ.get("INFER_METRICS_LESSON_SYNC", "1") not in ("0", "false"):
        try:
            _metrics_lesson_sync()
        except Exception:
            pass
    sections = []
    masters = [b for b in _lesson_blocks() if b.startswith("## MASTER")]
    if masters:
        sections.append("MASTERED ERROR CHAINS (apply the recorded approach first):\n"
                        + "\n".join(masters[-2:])[:600])
    other = [b for b in _lesson_blocks() if b.startswith("## FIX") or b.startswith("## PITFALL")]
    if other:
        sections.append("RECENT LESSONS:\n" + "\n".join(other[-2:])[:450])
    brief = _recent_sessions_brief(3)
    if brief:
        sections.append("RECENT SESSIONS (full transcripts: search_history / get_session):\n" + brief)
    health = _tool_health_brief(3)
    if health:
        sections.append("TOOL HEALTH (flaky tools — verify before trusting):\n" + health)
    if not sections:
        return ""
    out = "[SESSION RECAP — what past sessions learned & did]\n" + "\n\n".join(sections)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n... (truncated)"
    return out

def self_improve_status():
    """Human-readable summary of the self-improvement ledger/lessons (debugging)."""
    recs = _read_ledger()
    nfail = sum(1 for r in recs if r.get("kind") == "failure")
    nrec = sum(1 for r in recs if r.get("kind") == "recovery")
    chains = {}
    for r in recs:
        cid = r.get("chain_id")
        if not cid:
            continue
        st = chains.setdefault(cid, {"failures": 0, "recovered": 0, "tool": r.get("tool", "?")})
        if r.get("kind") == "failure":
            st["failures"] += 1
        else:
            st["recovered"] += 1
    try:
        lessons = state.lessons_render(status="approved", limit=None)
    except Exception:
        lessons = ""
    lines = ["Self-improvement state (%s):" % state.db_path(),
             "- failures recorded : %d" % nfail,
             "- recoveries recorded: %d" % nrec,
             "- error chains       : %d" % len(chains),
             "- mastered chains    : %d" % sum(1 for c in chains if _chain_is_mastered(c)),
             "- lessons            : %d (state db `lessons` table)" % len(_lesson_blocks()),
             "- recurrence threshold: %d (INFER_SELF_IMPROVE_RECURRENCE)" % _si_recurrence_threshold(),
             "- mastered threshold  : %s (INFER_CHAIN_MASTERED)" % os.environ.get("INFER_CHAIN_MASTERED", "2")]
    for cid, st in sorted(chains.items()):
        flag = " [MASTERED]" if _chain_is_mastered(cid) else ""
        lines.append("  - %s %s: %d fail / %d recovered%s"
                     % (st["tool"], cid, st["failures"], st["recovered"], flag))
    if lessons.strip():
        lines.append("--- lessons ---")
        lines.append(lessons.strip())
    return "\n".join(lines)
