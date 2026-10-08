"""File tools: read/write/edit/patch, list_directory, search_files and PDF text extraction."""
import os
import subprocess
import re

def _clean_pdf_text(raw):
    # Repair soft-hyphenation at line breaks (word-\nrest -> wordrest)
    raw = re.sub(r'-\n(?=[a-z])', '', raw)
    # Remove control characters except newline/tab
    raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)
    # Collapse runs of blank lines to at most two
    raw = re.sub(r'\n{3,}', '\n\n', raw)
    # Collapse runs of spaces/tabs on a single line
    raw = re.sub(r'[ \t]{2,}', ' ', raw)
    return raw.strip()

def _extract_text_from_pdf_impl(path):
    source_header = f"[Source: {path}]\n\n"
    pages_text = []

    # 1. pdfplumber — best layout-awareness; also extracts tables
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            meta = pdf.metadata or {}
            meta_parts = []
            if meta.get("Title"):
                meta_parts.append(f"Title: {meta['Title']}")
            if meta.get("Author"):
                meta_parts.append(f"Author: {meta['Author']}")
            if meta_parts:
                pages_text.append("[PDF Metadata] " + " | ".join(meta_parts))

            for i, page in enumerate(pdf.pages, 1):
                parts = []
                body = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                if body.strip():
                    parts.append(body)
                # Append any tables found on the page as simple CSV-ish blocks
                for table in page.extract_tables() or []:
                    rows = []
                    for row in table:
                        rows.append(" | ".join(cell.strip() if cell else "" for cell in row))
                    if rows:
                        parts.append("[Table]\n" + "\n".join(rows))
                if parts:
                    pages_text.append(f"--- Page {i} ---\n" + "\n\n".join(parts))

        if pages_text:
            return source_header + _clean_pdf_text("\n\n".join(pages_text))
    except Exception:
        pass

    # 2. pypdf
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        meta = reader.metadata or {}
        meta_parts = []
        if getattr(meta, "title", None):
            meta_parts.append(f"Title: {meta.title}")
        if getattr(meta, "author", None):
            meta_parts.append(f"Author: {meta.author}")
        if meta_parts:
            pages_text.append("[PDF Metadata] " + " | ".join(meta_parts))

        for i, page in enumerate(reader.pages, 1):
            body = page.extract_text() or ""
            if body.strip():
                pages_text.append(f"--- Page {i} ---\n{body}")

        if pages_text:
            return source_header + _clean_pdf_text("\n\n".join(pages_text))
    except Exception:
        pass

    # 3. pdftotext (poppler-utils) — reliable fallback for scanned/complex PDFs
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return source_header + _clean_pdf_text(proc.stdout)
    except Exception:
        pass

    return source_header + "Error: Could not extract text from PDF using any backend."

def extract_text_from_pdf(path):
    import hashlib
    cache_dir = os.path.expanduser("~/.cache/ai/pdf_cache")
    os.makedirs(cache_dir, exist_ok=True)
    try:
        mtime = os.path.getmtime(path)
    except:
        mtime = 0
    cache_key = hashlib.md5(f"{path}_{mtime}".encode('utf-8')).hexdigest()
    cache_path = os.path.join(cache_dir, f"{cache_key}.txt")
    
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return f.read()
        except:
            pass
            
    content = _extract_text_from_pdf_impl(path)
    
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(content)
    except:
        pass
        
    return content

# ── Core investigation tools (ported from the Hermes toolset) ─────────────────
# search_files: content search (regex) + find-by-name (glob) with a pure-Python
# walker, so a model can locate a symbol without burning context on grep/rg/find
# via execute_command. todo: a session-scoped task list the model keeps fresh so
# a small model can see where it is in a multi-step task without holding it in
# memory (file-system-as-persistent-memory pattern — the list survives
# interruptions and is recapitulated by `todo` with no arguments). clarify:
# proactive user questions with numbered options (harness workflow pattern: the
# agent asks for clarity instead of guessing on ambiguous tasks).

import fnmatch as _fnmatch

def search_files(pattern, path=".", target="content", file_glob=None,
                 limit=50, context=0, output_mode="content"):
    """Search file contents (regex) or find files by name (glob).

    Returns matches with file:line prefixes, a file list, or match counts —
    depending on output_mode — capped at `limit` lines. Never searches /.
    """
    if not pattern:
        return "Error: pattern is required."
    base = os.path.abspath(os.path.expanduser(path or "."))
    if not os.path.exists(base):
        return f"Error: path '{path}' does not exist. Use list_directory to check it."
    limit = max(1, min(int(limit or 50), 500))
    context = max(0, min(int(context or 0), 10))
    output_mode = output_mode or "content"

    # Respect a .gitignore-ish denylist so we don't walk build artifacts.
    skip_dirs = {".git", "__pycache__", ".pytest_cache", "node_modules",
                 ".cache", "build", "dist", ".venv", "venv", "target"}

    if target == "files":
        # Find files by name/glob pattern, most recently modified first.
        hits = []
        try:
            if os.path.isfile(base):
                hits = [base]
            else:
                for root, dirs, files in os.walk(base):
                    dirs[:] = [d for d in dirs if d not in skip_dirs]
                    for fn in files:
                        full = os.path.join(root, fn)
                        try:
                            if "*" in pattern or "?" in pattern:
                                if _fnmatch.fnmatch(fn, pattern):
                                    hits.append(full)
                            elif fn == pattern or fn.endswith(pattern):
                                hits.append(full)
                        except Exception:
                            continue
        except Exception as e:
            return f"Error searching files: {e}"
        def _mtime(p):
            try:
                return os.path.getmtime(p)
            except Exception:
                return 0
        hits.sort(key=_mtime, reverse=True)
        shown = hits[:limit]
        if not shown:
            return f"No files matching '{pattern}' under {path}."
        rel = lambda p: p[len(base) + 1:] if p.startswith(base) else p
        listing = "\n".join(f"{'[DIR] ' if os.path.isdir(p) else '      '}{rel(p)}"
                            for p in shown)
        more = f"\n... ({len(hits) - len(shown)} more, raised the limit to see them)" \
            if len(hits) > len(shown) else ""
        return f"Found {len(hits)} file(s):\n{listing}{more}"

    # content search (pure Python walker; skips binary/build dirs)
    lines_out = []
    matched_files = []
    total = 0
    if os.path.isfile(base):
        candidates = [base]
    else:
        candidates = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fn in files:
                if file_glob and not _fnmatch.fnmatch(fn, file_glob):
                    continue
                candidates.append(os.path.join(root, fn))
    try:
        rx = re.compile(pattern)
    except re.error as e:
        # Not a valid regex — fall back to fixed-string search.
        rx = re.compile(re.escape(pattern))
    for full in candidates:
        if total >= limit:
            break
        try:
            if os.path.getsize(full) > 5 * 1024 * 1024:
                continue
            with open(full, "r", encoding="utf-8", errors="ignore") as f:
                flines = f.readlines()
        except (OSError, UnicodeError):
            continue  # binary/unreadable — skip
        fmatches = []
        for i, line in enumerate(flines, 1):
            if rx.search(line):
                fmatches.append(i)
                if len(fmatches) > limit:
                    break
        if fmatches:
            matched_files.append(full)
            for i in fmatches:
                if total >= limit:
                    break
                if output_mode == "files_only":
                    lines_out.append(full)
                    total += 1
                    break
                if output_mode == "count":
                    lines_out.append(f"{full}: {len(fmatches)}")
                    total += 1
                    break
                lo = max(1, i - context)
                hi = min(len(flines), i + context)
                for j in range(lo, hi + 1):
                    marker = ":" if j == i else "-"
                    lines_out.append(f"{full}{marker}{j}: {flines[j - 1].rstrip()}")
                total += 1
            if context > 0 and total < limit:
                lines_out.append("")
    if not lines_out:
        return f"No matches for '{pattern}' under {path}." + (
            f" (glob: {file_glob})" if file_glob else "")
    header = f"{len(matched_files)} file(s) matched '{pattern}':"
    out = header + "\n" + "\n".join(lines_out[:limit])
    if total > len([l for l in lines_out if l.strip()]):
        out += "\n... (truncated at limit)"
    return out

def list_directory(path="."):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return f"Error: directory {path} does not exist."
        if not os.path.isdir(abs_path):
            return f"Error: path {path} is not a directory."
        
        items = os.listdir(abs_path)
        if not items:
            return f"Directory {path} is empty."
            
        lines = []
        for item in sorted(items):
            item_path = os.path.join(abs_path, item)
            is_dir = os.path.isdir(item_path)
            prefix = "[DIR] " if is_dir else "      "
            size_str = ""
            if not is_dir:
                try:
                    size = os.path.getsize(item_path)
                    if size < 1024:
                        size_str = f" ({size} B)"
                    elif size < 1024 * 1024:
                        size_str = f" ({size / 1024:.1f} KB)"
                    else:
                        size_str = f" ({size / (1024*1024):.1f} MB)"
                except:
                    pass
            lines.append(f"{prefix}{item}{size_str}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing directory: {e}"

def is_binary_file(filepath):
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(1024)
            if b'\x00' in chunk:
                return True
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7f})
            non_text = sum(1 for c in chunk if c not in text_chars)
            if len(chunk) > 0 and (non_text / len(chunk)) > 0.30:
                return True
        return False
    except:
        return False

def extract_code_outline(content):
    """Return a list of top-level definitions with line numbers."""
    outline = []
    patterns = re.compile(
        r'^(?:'
        r'(?:async\s+)?def\s+\w+'          # Python functions
        r'|class\s+\w+'                     # Python/JS/TS/Rust classes
        r'|(?:pub\s+)?(?:fn|impl|struct|enum|trait)\s+\w+'  # Rust
        r'|func\s+\w+'                      # Go
        r'|(?:async\s+)?function\s+\w+'    # JS/TS
        r'|(?:export\s+)?(?:const|let)\s+\w+\s*=\s*(?:async\s+)?\('  # arrow fns
        r'|(?:interface|type)\s+\w+'        # TS
        r')'
    )
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.lstrip()
        if patterns.match(stripped):
            outline.append(f"  L{i:>5}: {stripped[:90]}")
        if len(outline) >= 40:
            outline.append("  ... (outline truncated at 40 entries)")
            break
    return "\n".join(outline)

def effective_max_tool_output():
    """The per-call tool output cap the running engine applies: INFER_EFFECTIVE_MAX_TOOL_OUTPUT
    (exported by ai.c / agent/ after deriving it from the context window), else an explicit
    INFER_MAX_TOOL_OUTPUT, else the 65536 default."""
    for k in ("INFER_EFFECTIVE_MAX_TOOL_OUTPUT", "INFER_MAX_TOOL_OUTPUT"):
        try:
            v = int(os.environ.get(k) or 0)
        except ValueError:
            v = 0
        if v > 0:
            return v
    return 65536


def read_file(path, start_line=None, end_line=None):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return f"Error: file {path} does not exist."

        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in ['.png', '.jpg', '.jpeg', '.webp', '.pdf'] and is_binary_file(abs_path):
            return f"Error: Cannot read binary file '{path}'. This file appears to be a compiled binary or non-text file."

        if ext in ['.png', '.jpg', '.jpeg', '.webp']:
            return f"[IMAGE_DATA_SUCCESS:{abs_path}]"

        if ext == '.pdf':
            content = extract_text_from_pdf(abs_path)
        else:
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception as e:
                return f"Error reading text file: {e}"

        lines = content.splitlines()
        total_lines = len(lines)
        total_chars = len(content)

        # Line-range read (model requested a specific slice)
        if start_line is not None or end_line is not None:
            try:
                s_val = int(start_line) if start_line is not None else 1
            except (ValueError, TypeError):
                s_val = 1
            try:
                e_val = int(end_line) if end_line is not None else total_lines
            except (ValueError, TypeError):
                e_val = total_lines
            s = max(0, s_val - 1)
            e = min(total_lines, max(s, e_val))
            # Keep the chunk under the per-call cap (whole lines), so a too-wide range is cut
            # with a "continue at" hint instead of being truncated or BLOCKED by the agent loop.
            chunk_limit = max(2000, int(effective_max_tool_output() * 0.8))
            used, stop = 0, s
            while stop < e:
                used += len(lines[stop].encode("utf-8", "replace")) + 1
                if used > chunk_limit and stop > s:
                    break
                stop += 1
            snippet = "\n".join(lines[s:stop])
            hint = "[Use read_file with start_line/end_line to read other sections]"
            if stop < e:
                hint = (f"[Requested lines {s+1}-{e} exceed the per-call output limit (~{chunk_limit} chars); "
                        f"showing {s+1}-{stop}. Continue with start_line={stop+1}]")
            return (f"[{abs_path} | lines {s+1}-{stop} of {total_lines}]\n"
                    f"{hint}\n\n"
                    f"{snippet}")

        # Thresholds follow the engine's effective per-call cap (derived from the model's
        # context window): a long-context model reads a 120 KB file whole, and the digest
        # sub-agent is only used for files far beyond what one result can carry.
        max_tool = effective_max_tool_output()
        small_file_limit = max(12000, int(max_tool * 0.8))
        large_file_limit = max(80000, int(max_tool * 5.0))

        if total_chars <= small_file_limit:
            return content

        # Large file (small_file_limit – large_file_limit): return smart outline + head + tail
        if total_chars <= large_file_limit:
            outline = extract_code_outline(content)
            head = "\n".join(lines[:80])
            tail = "\n".join(lines[-30:])
            omitted = total_lines - 110
            parts = [
                f"[Large file: {total_lines} lines, {total_chars} chars | {abs_path}]",
                f"[To read a specific section: read_file(path, start_line=N, end_line=M)]",
            ]
            if outline:
                parts.append(f"\n### Code outline\n{outline}")
            parts.append(f"\n### First 80 lines\n{head}")
            if omitted > 0:
                parts.append(f"\n... ({omitted} lines omitted) ...")
            parts.append(f"\n### Last 30 lines\n{tail}")
            return "\n".join(parts)

        # Very large file (> large_file_limit): delegate digest to a sub-agent
        if os.environ.get("AI_DIGESTING") == "1":
            # Fall back to the outline + head/tail approach to avoid infinite recursion
            outline = extract_code_outline(content)
            head = "\n".join(lines[:80])
            tail = "\n".join(lines[-30:])
            omitted = total_lines - 110
            parts = [
                f"[Large file: {total_lines} lines, {total_chars} chars | {abs_path}]",
                f"[To read a specific section: read_file(path, start_line=N, end_line=M)]",
            ]
            if outline:
                parts.append(f"\n### Code outline\n{outline}")
            parts.append(f"\n### First 80 lines\n{head}")
            if omitted > 0:
                parts.append(f"\n... ({omitted} lines omitted) ...")
            parts.append(f"\n### Last 30 lines\n{tail}")
            return "\n".join(parts)

        ai_bin = os.environ.get("INFER_BIN_PATH")
        if not ai_bin or not os.path.exists(ai_bin):
            ai_bin = "/usr/local/bin/ai"
        if not os.path.exists(ai_bin):
            ai_bin = "./ai"
        if not os.path.exists(ai_bin):
            # Fall back to the outline + head approach
            outline = extract_code_outline(content)
            head = "\n".join(lines[:60])
            return (f"[Very large file: {total_lines} lines | {abs_path}]\n"
                    f"[Sub-agent digest unavailable; showing outline + first 60 lines]\n\n"
                    f"### Code outline\n{outline}\n\n### First 60 lines\n{head}")

        digest_prompt = (
            f"Read and digest the file '{abs_path}'. "
            f"Summarise its purpose, structure, key functions/classes/variables, "
            f"and any important patterns or TODOs. "
            f"Keep the summary under 800 words. Output in markdown."
        )
        try:
            task_timeout = 180
            env_timeout = os.environ.get("INFER_TASK_TIMEOUT")
            if env_timeout:
                try:
                    task_timeout = max(90, int(env_timeout) // 2)
                except ValueError:
                    pass
            env = os.environ.copy()
            env["AI_DIGESTING"] = "1"
            proc = subprocess.run(
                [ai_bin, digest_prompt],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=task_timeout,
                env=env
            )
            summary = proc.stdout.strip()
            if not summary:
                summary = "(sub-agent returned no output)"
        except Exception as ex:
            summary = f"(sub-agent error: {ex})"

        return (f"[Very large file: {total_lines} lines, {total_chars} chars | {abs_path}]\n"
                f"[Digest produced by sub-agent]\n\n{summary}")

    except Exception as e:
        return f"Error opening file: {e}"

def write_file(path, content):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as f:
            f.write(content)
        return f"File successfully written to {path}"
    except Exception as e:
        return f"Error writing file: {e}"

def edit_file(path, search_content, replace_content):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return f"Error: file {path} does not exist."
        with open(abs_path, "r") as f:
            content = f.read()
        if search_content in content:
            new_content = content.replace(search_content, replace_content)
            with open(abs_path, "w") as f:
                f.write(new_content)
            return f"File successfully edited at {path}"
        # Fuzzy retry: strip trailing whitespace per line and compare
        search_lines = search_content.splitlines()
        content_lines = content.splitlines()
        n = len(search_lines)
        matched_start = -1
        for i in range(len(content_lines) - n + 1):
            if all(content_lines[i + j].rstrip() == search_lines[j].rstrip()
                   for j in range(n)):
                matched_start = i
                break
        if matched_start >= 0:
            content_lines_with_ends = content.splitlines(keepends=True)
            original_span = "".join(content_lines_with_ends[matched_start:matched_start + n])
            # If search_content doesn't end with a newline, strip the trailing newline from original_span
            # so we don't eat it during replacement.
            if not search_content.endswith(('\n', '\r')):
                if original_span.endswith('\r\n'):
                    original_span = original_span[:-2]
                elif original_span.endswith('\n'):
                    original_span = original_span[:-1]
            new_content = content.replace(original_span, replace_content, 1)
            with open(abs_path, "w") as f:
                f.write(new_content)
            return f"File successfully edited at {path} (fuzzy whitespace match used)"
        return (f"Error: search content not found in {path}. "
                f"Make sure the search block matches exactly including whitespace.")
    except Exception as e:
        return f"Error editing file: {e}"

def patch_file(path, hunks=None, search=None, replace=None):
    """Atomically apply one or more search-and-replace patches to a file.
    hunks is an array of {"search": "...", "replace": "..."} objects.
    All search blocks must match before any modification is written.
    Supports fuzzy whitespace tolerance per hunk."""
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return f"Error: file '{path}' does not exist."
        
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        patch_list = []
        if hunks and isinstance(hunks, list):
            for h in hunks:
                if isinstance(h, dict) and "search" in h and "replace" in h:
                    patch_list.append((h["search"], h["replace"]))
        elif search is not None and replace is not None:
            patch_list.append((str(search), str(replace)))
        
        if not patch_list:
            return "Error: patch_file requires either 'hunks' array of {search, replace} or 'search' and 'replace' arguments."

        new_content = content
        applied = 0
        for s_text, r_text in patch_list:
            if s_text in new_content:
                new_content = new_content.replace(s_text, r_text, 1)
                applied += 1
            else:
                s_lines = s_text.splitlines()
                c_lines = new_content.splitlines()
                n = len(s_lines)
                matched_start = -1
                for i in range(len(c_lines) - n + 1):
                    if all(c_lines[i + j].rstrip() == s_lines[j].rstrip() for j in range(n)):
                        matched_start = i
                        break
                if matched_start >= 0:
                    c_lines_ends = new_content.splitlines(keepends=True)
                    orig_span = "".join(c_lines_ends[matched_start:matched_start + n])
                    if not s_text.endswith(('\n', '\r')) and orig_span.endswith(('\n', '\r')):
                        orig_span = orig_span[:-1] if orig_span.endswith('\n') else orig_span[:-2]
                    new_content = new_content.replace(orig_span, r_text, 1)
                    applied += 1
                else:
                    return f"Error: patch hunk not found in '{path}':\n```\n{s_text[:200]}\n```\nAborted all changes."
        
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Successfully applied {applied} patch hunk(s) to '{path}'."
    except Exception as e:
        return f"Error patching file: {e}"
