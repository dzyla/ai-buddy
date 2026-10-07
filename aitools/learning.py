"""Human-reviewed learning queue for the agent's self-improvement lessons.

Lifecycle (stored in the state db `lessons.status`):
    proposed -> approved | rejected;   approved -> expired once now > expires.
Only `approved` (and unexpired) lessons are injected into the system prompt; the read path
(state.lessons_list) also filters on `expires`, so it never needs a write to stay correct.
`expire()` is a write: run it from `ai lessons prune` / the scheduler, never at startup.
Set INFER_LEARNING_AUTO_APPROVE=1 for the old behaviour (new lessons stored approved).
"""
import os
import subprocess
import sys
import tempfile
import time

from . import state

DAY = 86400.0
DEFAULT_TTL_DAYS = 60
REJECTED_RETENTION_DAYS = 30
STATUSES = ("proposed", "approved", "rejected", "expired")


def auto_policy():
    """True when INFER_LEARNING_AUTO_APPROVE=1 (lessons skip human review). Read at call time."""
    return os.environ.get("INFER_LEARNING_AUTO_APPROVE", "0").strip().lower() in ("1", "true", "yes", "on")


def _norm(kind, tool, signature):
    return (str(kind or "NOTE").strip().upper(), str(tool or "").strip(), str(signature or "").strip())


def propose(kind, tool, signature, body, now=None, ttl_days=DEFAULT_TTL_DAYS):
    """Record a lesson as `proposed` (or `approved` under auto_policy). A repeat of the same
    kind+tool+signature stores no new row: it bumps hits/last_seen, pushes out the expiry of an
    approved lesson, and puts an expired one back in the review queue. Returns the lesson id."""
    kind, tool, signature = _norm(kind, tool, signature)
    ts = time.time() if now is None else float(now)
    with state._conn() as c, state._tx(c):
        row = c.execute("SELECT id, status, expires FROM lessons WHERE kind=? AND tool=? AND signature=?",
                        (kind, tool, signature)).fetchone()
        if row:
            c.execute("UPDATE lessons SET hits=hits+1, last_seen=max(coalesce(last_seen, 0), ?) WHERE id=?",
                      (ts, row["id"]))
            if row["status"] == "approved" and row["expires"] is not None and row["expires"] > ts:
                c.execute("UPDATE lessons SET expires=max(expires, ?) WHERE id=?",
                          (ts + ttl_days * DAY, row["id"]))
            elif row["status"] == "expired":
                c.execute("UPDATE lessons SET status='proposed', expires=NULL WHERE id=?", (row["id"],))
            return row["id"]
        cur = c.execute("INSERT INTO lessons(kind, tool, signature, body, status, created, last_seen, hits, "
                        "expires) VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL)",
                        (kind, tool, signature, str(body or ""),
                         "approved" if auto_policy() else "proposed", ts, ts))
        return cur.lastrowid


def _set_status(lesson_id, status, expires=None):
    with state._conn() as c, state._tx(c):
        cur = c.execute("UPDATE lessons SET status=?, expires=? WHERE id=?", (status, expires, int(lesson_id)))
        return cur.rowcount > 0


def approve(lesson_id, ttl_days=DEFAULT_TTL_DAYS, now=None):
    """Approve a lesson; it is injected until now + ttl_days (reinforcement extends it)."""
    ts = time.time() if now is None else float(now)
    return _set_status(lesson_id, "approved", ts + ttl_days * DAY)


def reject(lesson_id):
    return _set_status(lesson_id, "rejected", None)


def expire(now=None):
    """Mark approved lessons past their expiry as `expired`. A write: not for the startup path."""
    ts = time.time() if now is None else float(now)
    with state._conn() as c, state._tx(c):
        cur = c.execute("UPDATE lessons SET status='expired' WHERE status='approved' AND expires IS NOT NULL "
                        "AND expires < ?", (ts,))
        return cur.rowcount


def prune(now=None):
    """expire() plus deletion of rejected lessons not seen for REJECTED_RETENTION_DAYS."""
    ts = time.time() if now is None else float(now)
    n_exp = expire(ts)
    with state._conn() as c, state._tx(c):
        cur = c.execute("DELETE FROM lessons WHERE status='rejected' AND coalesce(last_seen, created, 0) < ?",
                        (ts - REJECTED_RETENTION_DAYS * DAY,))
        return {"expired": n_exp, "deleted": cur.rowcount}


def _set_body(lesson_id, body):
    with state._conn() as c, state._tx(c):
        c.execute("UPDATE lessons SET body=? WHERE id=?", (body, int(lesson_id)))


def edit_in_editor(body):
    """Open `body` in $EDITOR (default vi); returns the edited text (unchanged on failure)."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
        f.write(body)
        path = f.name
    try:
        subprocess.call(editor.split() + [path])
        with open(path) as f:
            return f.read().strip() or body
    except Exception:
        return body
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def format_lesson(r):
    return ("#%d  %s  tool=%s  evidence=%d hit(s)\n  %s"
            % (r["id"], r["kind"], r["tool"] or "-", r["hits"] or 1, (r["body"] or "").replace("\n", "\n  ")))


def review(interactive=True, decide=None, input_fn=input, out=sys.stdout, ttl_days=DEFAULT_TTL_DAYS):
    """Walk the proposed lessons. `decide(lesson_dict)` returns 'a'/'r'/'s'/'q', or ('e', new_body)
    to edit then approve; with decide=None and interactive=True the user is prompted via input_fn.
    Returns {"approved": n, "rejected": n, "skipped": n, "pending": n}."""
    res = {"approved": 0, "rejected": 0, "skipped": 0, "pending": 0}
    queue = state.lessons_list(status="proposed", include_expired=True)
    for i, r in enumerate(queue):
        if decide is not None:
            choice = decide(r)
        elif interactive:
            print(format_lesson(r), file=out)
            try:
                choice = input_fn("[a]pprove [r]eject [s]kip [e]dit [q]uit > ")
            except EOFError:
                choice = "q"
        else:
            choice = "s"
        new_body = None
        if isinstance(choice, (tuple, list)):
            choice, new_body = choice[0], choice[1]
        choice = (choice or "s").strip().lower()[:1]
        if choice == "e":
            new_body = new_body if new_body is not None else edit_in_editor(r["body"] or "")
            choice = "a"
        if choice == "a":
            if new_body is not None:
                _set_body(r["id"], new_body)
            approve(r["id"], ttl_days)
            res["approved"] += 1
        elif choice == "r":
            reject(r["id"])
            res["rejected"] += 1
        elif choice == "q":
            res["pending"] += len(queue) - i
            return res
        else:
            res["skipped"] += 1
    res["pending"] += res["skipped"]
    return res


def format_table(rows, now=None):
    ts = time.time() if now is None else now
    lines = ["%-4s %-9s %-8s %-5s %-12s %s" % ("ID", "STATUS", "KIND", "HITS", "EXPIRES", "TOOL / BODY")]
    for r in rows:
        exp = "-" if r["expires"] is None else "%dd" % int((r["expires"] - ts) // DAY)
        body = (r["body"] or "").replace("\n", " ")[:70]
        lines.append("%-4d %-9s %-8s %-5d %-12s %s: %s" % (r["id"], r["status"], r["kind"], r["hits"] or 1,
                                                          exp, r["tool"] or "-", body))
    return "\n".join(lines)


def cli(args, input_fn=input, out=sys.stdout):
    """`ai lessons <list|review|approve|reject|prune>`; returns an exit code."""
    sub = args[0] if args else "list"
    rest = args[1:]
    if sub == "list":
        status = None
        if "--status" in rest:
            i = rest.index("--status")
            status = rest[i + 1] if i + 1 < len(rest) else None
            if status not in STATUSES:
                print("--status must be one of: " + ", ".join(STATUSES), file=sys.stderr)
                return 2
        rows = state.lessons_list(status=status, include_expired=True)
        print(format_table(rows) if rows else "No lessons.", file=out)
        return 0
    if sub == "review":
        res = review(interactive=True, input_fn=input_fn, out=out)
        print("approved=%(approved)d rejected=%(rejected)d pending=%(pending)d" % res, file=out)
        return 0
    if sub in ("approve", "reject"):
        if not rest or not all(a.isdigit() for a in rest):
            print("Usage: ai lessons %s ID..." % sub, file=sys.stderr)
            return 2
        rc = 0
        for a in rest:
            ok = approve(int(a)) if sub == "approve" else reject(int(a))
            print("%s #%s" % (sub + "d" if ok else "no such lesson:", a), file=out)
            rc = rc or (0 if ok else 1)
        return rc
    if sub == "prune":
        res = prune()
        print("expired=%(expired)d deleted_rejected=%(deleted)d" % res, file=out)
        return 0
    print("Usage: ai lessons [list [--status S] | review | approve ID... | reject ID... | prune]", file=sys.stderr)
    return 2
