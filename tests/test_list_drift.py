"""Drift guard for security-relevant lists duplicated between the C binary and Python:

- the approval gate's MUTATING / READONLY tool lists: ai.c tool_is_mutating()/tool_is_readonly()
  vs agent/native.py MUTATING/READONLY (the two engines must gate the same tools);
- the tool server's "nothing ran, safe to fall back" refusals: ai_toolclient.c tc_refusal() vs
  aitools/server.py REFUSALS (a refusal only one side knows would make the client re-run a call
  that already ran, or never fall back).
"""
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _c_string_array(path, func, var):
    src = open(os.path.join(REPO, path), encoding="utf-8").read()
    m = re.search(r"\b%s\s*\([^)]*\)\s*\{" % re.escape(func), src)
    assert m, f"{func}() not found in {path}"
    body = src[m.end():]
    a = re.search(r"static\s+const\s+char\s*\*\s*%s\s*\[\]\s*=\s*\{(.*?)\};" % re.escape(var), body, re.S)
    assert a, f"array {var}[] not found in {func}() of {path}"
    items = re.findall(r'"((?:[^"\\]|\\.)*)"', re.sub(r"/\*.*?\*/", "", a.group(1), flags=re.S))
    assert items, f"{path}:{func}:{var} parsed empty"
    return items


def _diff(c_items, py_items, c_name, py_name):
    c, p = set(c_items), set(py_items)
    lines = []
    if c - p:
        lines.append(f"only in {c_name}: {sorted(c - p)}")
    if p - c:
        lines.append(f"only in {py_name}: {sorted(p - c)}")
    dups = sorted({x for x in c_items if c_items.count(x) > 1})
    if dups:
        lines.append(f"duplicates in {c_name}: {dups}")
    return "\n".join(lines)


def test_mutating_list_matches():
    from agent import native
    c = _c_string_array("ai.c", "tool_is_mutating", "mut")
    d = _diff(c, native.MUTATING, "ai.c tool_is_mutating", "agent/native.py MUTATING")
    assert not d, d


def test_readonly_list_matches():
    from agent import native
    c = _c_string_array("ai.c", "tool_is_readonly", "ro")
    d = _diff(c, native.READONLY, "ai.c tool_is_readonly", "agent/native.py READONLY")
    assert not d, d


def test_lists_are_disjoint():
    from agent import native
    assert not (native.MUTATING & native.READONLY)


def test_tool_server_refusals_match():
    from aitools import server
    c = _c_string_array("ai_toolclient.c", "tc_refusal", "refusals")
    d = _diff(c, server.REFUSALS, "ai_toolclient.c tc_refusal", "aitools/server.py REFUSALS")
    assert not d, d


def test_parser_detects_drift():
    """The parser must actually see the array contents (so a drift can't pass vacuously)."""
    c = _c_string_array("ai.c", "tool_is_mutating", "mut")
    assert "execute_command" in c and "write_file" in c and len(c) > 10
    assert _diff(c + ["new_tool"], c, "C", "Py") == "only in C: ['new_tool']"
