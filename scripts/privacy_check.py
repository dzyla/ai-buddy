#!/usr/bin/env python3
"""Block personal data from being committed.

Scans staged changes (default) or every tracked file (--all) for:
  * generic leaks: private keys, cloud/API tokens, hardcoded secrets,
    public IPv4 addresses, real-looking e-mail addresses, /home/<user> paths,
    and data/office files (.xlsx, .docx, ...) that usually hold lab data;
  * personal patterns listed one regex per line in
    ~/.config/ai/private_patterns.txt (kept OUTSIDE the repo so the
    denylist itself is never published). Lines starting with # are ignored.

A line containing the marker  privacy-ok  is exempt.
Exit status 1 when anything is found. Used by .githooks/pre-commit.
"""
import ipaddress
import os
import re
import subprocess
import sys

PATTERNS_FILE = os.path.expanduser(
    os.environ.get("AI_PRIVATE_PATTERNS", "~/.config/ai/private_patterns.txt"))

BLOCKED_EXT = (".xlsx", ".xls", ".docx", ".doc", ".pptx", ".csv", ".sqlite",
               ".db", ".pem", ".key", ".p12", ".env")
BLOCKED_NAMES = ("gcal_token.json", "gcal_credentials.json", "zuliprc",
                 "memory.txt", "history.jsonl", "trader.json")

GENERIC = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}")),
    ("API key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}")),
    ("Google key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("hardcoded secret", re.compile(
        r"""(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*["'][A-Za-z0-9_\-/+=]{16,}["']""")),
    ("home path", re.compile(r"/home/(?!user\b|you\b|runner\b|<)[a-z][a-z0-9_-]*/")),
]
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
EMAIL_OK = re.compile(r"(?i)@(example\.(com|org|net)|([a-z0-9-]+\.)*example\.(com|org)|"
                      r"anthropic\.com|users\.noreply\.github\.com|localhost)\b|noreply@|"
                      r"^(your[-_.]?(email|name)|you|user|someone|name)@|"
                      r"@([a-z0-9-]+\.)*(example|invalid|test)(\.[a-z0-9-]+)+$|@[a-z]\.(com|org|net)$|@evil\.[a-z]+$|"
                      r"^git@github\.com$")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def load_personal():
    pats = []
    try:
        with open(PATTERNS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    try:
                        pats.append(("personal pattern", re.compile(line, re.I)))
                    except re.error as e:
                        print(f"privacy_check: bad regex {line!r}: {e}", file=sys.stderr)
    except FileNotFoundError:
        pass
    return pats


def _public_ip(s):
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return False
    if ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_reserved \
            or ip.is_multicast or ip.is_link_local:
        return False
    # version-like strings (124.0.0.0 user-agent strings, 1.2.3.4 placeholders)
    if s.endswith(".0.0.0") or s.endswith(".0.0") or s in ("1.2.3.4", "8.8.8.8", "1.1.1.1"):
        return False
    return True


def scan_line(line, personal):
    if "privacy-ok" in line:
        return []
    hits = [name for name, rx in GENERIC + personal if rx.search(line)]
    for m in EMAIL.finditer(line):
        if not EMAIL_OK.search(m.group(0)):
            hits.append(f"e-mail {m.group(0)}")
    for m in IPV4.finditer(line):
        if _public_ip(m.group(0)):
            hits.append(f"public IP {m.group(0)}")
    return hits


def check_name(path):
    low = path.lower()
    if low.endswith(BLOCKED_EXT) or os.path.basename(low) in BLOCKED_NAMES:
        return ["data/credential file"]
    return []


def staged():
    """Yield (path, lineno, text) for added lines plus (path, 0, None) per file."""
    names = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                           capture_output=True, text=True, check=True).stdout.split("\n")
    for n in filter(None, names):
        yield n, 0, None
    diff = subprocess.run(["git", "diff", "--cached", "-U0", "--no-color", "--diff-filter=ACMR"],
                          capture_output=True, text=True, errors="replace", check=True).stdout
    path, ln = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            ln = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            yield path, ln, line[1:]
            ln += 1


def tracked():
    names = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                           check=True).stdout.split("\n")
    for n in filter(None, names):
        yield n, 0, None
        try:
            with open(n, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    yield n, i, line.rstrip("\n")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue


def main(argv):
    personal = load_personal()
    source = tracked() if "--all" in argv else staged()
    problems = []
    for path, ln, text in source:
        if path == "scripts/privacy_check.py":
            continue
        hits = check_name(path) if text is None else scan_line(text, personal)
        for h in hits:
            problems.append(f"{path}:{ln}: {h}")
    if problems:
        print("privacy_check: possible personal data — commit blocked:", file=sys.stderr)
        for p in problems[:200]:
            print("  " + p, file=sys.stderr)
        print("Fix it, move the value to env/config, or append 'privacy-ok' to an\n"
              "intentional line. Bypass once with: git commit --no-verify", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
