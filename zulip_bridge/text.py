"""Reply text cleanup: ANSI stripping and soft-wrap normalisation."""
import re

def normalize_text(text):
    """Normalize soft-wrapped prose paragraphs while strictly preserving code blocks."""
    if not text:
        return ""

    # Split text into code blocks and non-code blocks
    parts = re.split(r'(```[\s\S]*?```)', text)
    result = []

    for part in parts:
        if part.startswith('```') and part.endswith('```'):
            # Code block - preserve exactly as is
            result.append(part)
        else:
            # Prose blocks
            paragraphs = re.split(r'\n{2,}', part)
            norm_paras = []
            for p in paragraphs:
                p_clean = p.strip()
                if not p_clean:
                    continue
                # If block is code with 4-space or tab indentation, preserve
                lines = p_clean.split('\n')
                if any(line.startswith('    ') or line.startswith('\t') for line in lines):
                    norm_paras.append(p_clean)
                else:
                    # Join lines with spaces and collapse multiple spaces
                    joined = ' '.join(line.strip() for line in lines if line.strip())
                    joined = re.sub(r' {2,}', ' ', joined)
                    if joined:
                        norm_paras.append(joined)
            result.append('\n\n'.join(norm_paras))

    return ''.join(result)


def clean_response(text):
    if not text:
        return ""
    # Strip all ANSI escape codes (colour, dim, bold, cursor movement, etc.)
    ansi_escape = re.compile(r'\x1b(?:\[[0-9;]*[a-zA-Z]|\]\d*;[^\x07]*\x07|[@-Z\\-_])')
    clean = ansi_escape.sub('', text)
    # Remove carriage returns
    clean = clean.replace('\r', '')
    # Replace long unicode horizontal lines with standard markdown horizontal rules
    clean = clean.replace("────────────────────────────────────────────", "---")
    # Collapse runs of blank lines to at most two
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    # Join wrapped lines in prose while preserving code & tables
    clean = normalize_text(clean)
    return clean.strip()


_ANSI = re.compile(r'\x1b(?:\[[0-9;?]*[a-zA-Z]|\]\d*;[^\x07]*\x07|[@-Z\\-_])')
# stderr chatter of `ai` that means nothing in a chat reply
_STDERR_NOISE = ("session ended \u00b7 resume", "session ended · resume", "[thinking]...")


def clean_stderr(text):
    """`ai` stderr for a Zulip message: no ANSI escapes / carriage returns, no
    "session ended · resume: ai -r ..." or "[thinking]..." lines."""
    if not text:
        return ""
    clean = _ANSI.sub("", text).replace("\r", "\n")
    lines = [ln.rstrip() for ln in clean.splitlines()]
    lines = [ln for ln in lines if ln.strip() and not any(n in ln for n in _STDERR_NOISE)]
    return "\n".join(lines).strip()
