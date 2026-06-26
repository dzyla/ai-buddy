#!/usr/bin/env python3
import sys
import os
import json
import subprocess
import urllib.request
import urllib.parse
import re

CONFIG_PATHS = [
    os.path.join(os.getcwd(), "mcp.json"),
    os.path.join(os.getcwd(), "mcp_config.json"),
    os.path.expanduser("~/.config/ai/mcp.json"),
    os.path.expanduser("~/.config/ai/mcp_config.json"),
    os.path.expanduser("~/.gemini/config/mcp_config.json"),
    os.path.expanduser("~/.lmstudio/mcp.json"),
]

def load_config():
    for path in CONFIG_PATHS:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    if "mcpServers" in data:
                        return data["mcpServers"]
                    return data
            except Exception as e:
                print(f"Warning: failed to load config from {path}: {e}", file=sys.stderr)
    return {}

def run_jsonrpc(proc, method, params, req_id):
    req = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params
    }
    req_str = json.dumps(req) + "\n"
    proc.stdin.write(req_str)
    proc.stdin.flush()

    while True:
        line = proc.stdout.readline()
        if not line:
            raise Exception("Connection closed by server")
        try:
            resp = json.loads(line)
            if resp.get("id") == req_id:
                return resp
        except json.JSONDecodeError:
            pass

def send_notification(proc, method, params=None):
    req = {
        "jsonrpc": "2.0",
        "method": method
    }
    if params is not None:
        req["params"] = params
    req_str = json.dumps(req) + "\n"
    proc.stdin.write(req_str)
    proc.stdin.flush()

def start_server(cfg):
    cmd = []
    if "command" in cfg:
        cmd.append(cfg["command"])
    if "args" in cfg:
        cmd.extend(cfg["args"])
    
    if not cmd:
        return None

    env = os.environ.copy()
    if "env" in cfg:
        for k, v in cfg["env"].items():
            env[k] = str(v)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        bufsize=1
    )
    return proc

def init_server(proc):
    init_params = {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "ai", "version": "1.0"}
    }
    resp = run_jsonrpc(proc, "initialize", init_params, req_id=1)
    send_notification(proc, "notifications/initialized")
    return resp

def list_tools(server_name, cfg):
    proc = start_server(cfg)
    if not proc:
        return []
    try:
        init_server(proc)
        resp = run_jsonrpc(proc, "tools/list", {}, req_id=2)
        tools = resp.get("result", {}).get("tools", [])
        namespaced_tools = []
        for t in tools:
            clean_server = "".join(c if c.isalnum() or c == "_" else "_" for c in server_name)
            t["name"] = f"{clean_server}__{t['name']}"
            namespaced_tools.append(t)
        return namespaced_tools
    except Exception as e:
        print(f"Error listing tools from {server_name}: {e}", file=sys.stderr)
        return []
    finally:
        try:
            proc.terminate()
        except:
            pass

def call_tool(server_name, cfg, tool_name, arguments):
    proc = start_server(cfg)
    if not proc:
        return {"error": "Failed to start server"}
    try:
        init_server(proc)
        resp = run_jsonrpc(proc, "tools/call", {"name": tool_name, "arguments": arguments}, req_id=3)
        return resp.get("result", {})
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            proc.terminate()
        except:
            pass

def ddg_lite_search(query):
    try:
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')
        
        trs = re.findall(r'<tr.*?>(.*?)</tr>', html, re.DOTALL)
        results = []
        
        i = 0
        while i < len(trs):
            tr = trs[i]
            link_match = re.search(r'(<a[^>]+class=\'result-link\'[^>]*>)(.*?)</a>', tr, re.DOTALL)
            if link_match:
                tag = link_match.group(1)
                text = link_match.group(2)
                href_match = re.search(r'href="([^"]+)"', tag)
                link = href_match.group(1) if href_match else ""
                title = re.sub(r'<[^>]+>', '', text).strip()
                
                snippet = ""
                if i + 1 < len(trs):
                    next_tr = trs[i + 1]
                    snippet_match = re.search(r'<td[^>]+class=\'result-snippet\'[^>]*>(.*?)</td>', next_tr, re.DOTALL)
                    if snippet_match:
                        snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
                        snippet = snippet.replace('&amp;', '&').replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
                
                results.append(f"Title: {title}\nURL: {link}\nSnippet: {snippet}\n")
                if len(results) >= 5:
                    break
                i += 2
                continue
            i += 1
            
        if not results:
            return "No results found."
            
        return "\n".join(results)
    except Exception as e:
        return f"Error during web search: {e}"

def fetch_webpage(url):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')
        
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<p[^>]*>', '\n\n', html, flags=re.IGNORECASE)
        html = re.sub(r'<br[^>]*>', '\n', html, flags=re.IGNORECASE)
        html = re.sub(r'<h[1-6][^>]*>', '\n\n# ', html, flags=re.IGNORECASE)
        html = re.sub(r'</h[1-6]>', '\n', html, flags=re.IGNORECASE)
        
        text = re.sub(r'<[^>]+>', '', html)
        text = text.replace('&nbsp;', ' ').replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&#x27;', "'").replace('&#39;', "'").replace('&ndash;', '-').replace('&mdash;', '-')
        
        lines = [line.strip() for line in text.splitlines()]
        text = "\n".join([line for line in lines if line])
        
        if len(text) > 10000:
            text = text[:10000] + "\n... (truncated)"
            
        return text
    except Exception as e:
        return f"Error fetching webpage: {e}"

MEMORY_PATH = os.path.expanduser("~/.config/ai/memory.txt")

def save_memory(content):
    try:
        os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)
        if len(content) > 4000:
            content = content[-4000:]
        with open(MEMORY_PATH, "w") as f:
            f.write(content)
        return "Memory updated successfully."
    except Exception as e:
        return f"Error saving memory: {e}"

def extract_text_from_pdf(path):
    # Try pdftotext first (very common on Linux via poppler-utils)
    try:
        proc = subprocess.run(["pdftotext", path, "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode == 0:
            return proc.stdout
    except Exception:
        pass
    
    # Try pypdf Python library
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        if text:
            return text
    except Exception:
        pass

    # Try pdfplumber Python library
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() or ""
            if text:
                return text
    except Exception:
        pass

    return "Error: Could not parse PDF. Please ensure the 'pdftotext' command-line utility or 'pypdf' Python library is installed."

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

def highlight_line(line, lang):
    if not lang:
        return f"  \033[36m{line}\033[0m"
        
    stripped = line.strip()
    if stripped.startswith('#') or stripped.startswith('//') or stripped.startswith('/*') or stripped.endswith('*/'):
        return f"  \033[90m{line}\033[0m"
        
    string_placeholder = "___STR_PLACEHOLDER_{}___"
    strings = []
    
    def repl_str(match):
        strings.append(match.group(0))
        return string_placeholder.format(len(strings) - 1)
        
    temp_line = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'', repl_str, line)
    
    keywords = [
        "def", "class", "return", "if", "elif", "else", "for", "while", "break", "continue", 
        "import", "from", "as", "try", "except", "finally", "raise", "assert", "with", "in", 
        "is", "not", "and", "or", "lambda", "global", "nonlocal", "pass", "yield", "del",
        "int", "char", "float", "double", "void", "struct", "union", "enum", "typedef", 
        "const", "static", "extern", "volatile", "inline", "switch", "case", "default", 
        "do", "goto", "sizeof", "alignof", "then", "fi", "done", "esac", "local", "export", 
        "function", "let", "var", "fn", "impl", "pub", "use", "mod"
    ]
    keyword_re = r'\b(' + '|'.join(keywords) + r')\b'
    temp_line = re.sub(keyword_re, r'\033[1;33m\1\033[0m', temp_line)
    
    constants = ["True", "False", "None", "true", "false", "null", "NULL", "self"]
    const_re = r'\b(' + '|'.join(constants) + r')\b'
    temp_line = re.sub(const_re, r'\033[35m\1\033[0m', temp_line)
    
    temp_line = re.sub(r'\b(\d+)\b', r'\033[35m\1\033[0m', temp_line)
    
    for idx, s in enumerate(strings):
        temp_line = temp_line.replace(string_placeholder.format(idx), f"\033[32m{s}\033[0m")
        
    return f"  {temp_line}"

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

def read_file(path):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return f"Error: file {path} does not exist."
            
        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in ['.png', '.jpg', '.jpeg', '.webp', '.pdf'] and is_binary_file(abs_path):
            return f"Error: Cannot read binary file '{path}'. This file appears to be a compiled binary or non-text file."
        
        # If it's an image, return a special tag that the C binary intercepts to load base64
        if ext in ['.png', '.jpg', '.jpeg', '.webp']:
            return f"[IMAGE_DATA_SUCCESS:{abs_path}]"
            
        # If PDF
        if ext == '.pdf':
            return extract_text_from_pdf(abs_path)
            
        # Otherwise, treat as text file
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if len(content) > 12000:
                content = content[:12000] + "\n... (truncated to 12KB)"
            return content
        except Exception as e:
            return f"Error reading text file: {e}"
            
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
        if search_content not in content:
            return f"Error: search content not found in {path}. Make sure the search block matches exactly including whitespace."
        new_content = content.replace(search_content, replace_content)
        with open(abs_path, "w") as f:
            f.write(new_content)
        return f"File successfully edited at {path}"
    except Exception as e:
        return f"Error editing file: {e}"

def render_math(text):
    latex_symbols = {
        r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\delta': 'δ', 
        r'\epsilon': 'ε', r'\zeta': 'ζ', r'\eta': 'η', r'\theta': 'θ',
        r'\iota': 'ι', r'\kappa': 'κ', r'\lambda': 'λ', r'\mu': 'μ', 
        r'\nu': 'ν', r'\xi': 'ξ', r'\pi': 'π', r'\rho': 'ρ', 
        r'\sigma': 'σ', r'\tau': 'τ', r'\upsilon': 'υ', r'\phi': 'φ', 
        r'\chi': 'χ', r'\psi': 'ψ', r'\omega': 'omega',
        r'\Delta': 'Δ', r'\Theta': 'Θ', r'\Lambda': 'Λ', r'\Pi': 'Π', 
        r'\Sigma': 'Σ', r'\Phi': 'Φ', r'\Psi': 'Ψ', r'\Omega': 'Ω',
        r'\infty': '∞', r'\times': '×', r'\div': '÷', r'\pm': '±',
        r'\cdot': '·', r'\neq': '≠', r'\ne': '≠', r'\leq': '≤', 
        r'\le': '≤', r'\geq': '≥', r'\ge': '≥', r'\approx': '≈', 
        r'\propto': '∝', r'\partial': '∂', r'\nabla': '∇', 
        r'\sum': '∑', r'\prod': '∏', r'\int': '∫', r'\oint': '∮',
        r'\sqrt': '√', r'\sim': '~', r'\forall': '∀', r'\exists': '∃', 
        r'\in': '∈', r'\notin': '∉', r'\ni': '∋', r'\emptyset': '∅', 
        r'\cap': '∩', r'\cup': '∪', r'\subset': '⊂', r'\supset': '⊃',
        r'\subseteq': '⊆', r'\supseteq': '⊇', r'\rightarrow': '→', 
        r'\leftarrow': '←', r'\uparrow': '↑', r'\downarrow': '↓', 
        r'\leftrightarrow': '↔', r'\Rightarrow': '⇒', r'\Leftarrow': '⇐',
        r'\hbar': 'ħ', r'\degree': '°'
    }
    
    text = text.replace('$$', '').replace('$', '')
    for latex, unicode_char in latex_symbols.items():
        text = text.replace(latex, unicode_char)
        
    superscripts = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾', 'n': 'ⁿ', 'i': 'ⁱ'}
    subscripts = {'0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉', '+': '₊', '-': '₋', '=': '₌', '(': '₍', ')': '₎', 'i': 'ᵢ', 'j': 'ⱼ', 'k': 'ₖ', 'x': 'ₓ'}
    
    def repl_super(match):
        val = match.group(1) or match.group(2)
        return "".join(superscripts.get(c, c) for c in val)
        
    text = re.sub(r'\^\{([^}]+)\}|\^([0-9+\-nix])', repl_super, text)
    
    def repl_sub(match):
        val = match.group(1) or match.group(2)
        return "".join(subscripts.get(c, c) for c in val)
        
    text = re.sub(r'\_\{([^}]+)\}|\_([0-9+\-ijkx])', repl_sub, text)
    text = re.sub(r'√\{([^}]+)\}', r'√\1', text)
    return text

def render_math_safely(line):
    code_placeholder = "___CODE_PLACEHOLDER_{}___"
    codes = []
    
    def repl_code(match):
        codes.append(match.group(0))
        return code_placeholder.format(len(codes) - 1)
        
    temp_line = re.sub(r'`[^`\n]+`', repl_code, line)
    temp_line = render_math(temp_line)
    for idx, c in enumerate(codes):
        temp_line = temp_line.replace(code_placeholder.format(idx), c)
    return temp_line

def render_markdown(text):
    lines = text.splitlines()
    rendered = []
    in_code_block = False
    
    # Helper to check if a line is a table row
    def is_table_row(line):
        return '|' in line

    # Helper to check if a line is a table separator
    def is_table_separator(line):
        if '|' not in line:
            return False
        cleaned = line.replace('|', '').replace(':', '').replace('-', '').strip()
        return len(cleaned) == 0

    # Helper to parse a markdown table row into cells
    def parse_row(row):
        parts = row.split('|')
        if parts[0].strip() == '':
            parts = parts[1:]
        if len(parts) > 0 and parts[-1].strip() == '':
            parts = parts[:-1]
        return [cell.strip() for cell in parts]

    # Helper to strip ANSI codes to get visible length
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    def visible_len(t):
        return len(ansi_escape.sub('', t))

    # Helper to format inline markdown inside a cell
    def format_cell(cell, is_header=False):
        cell = re.sub(r'\*\*(.*?)\*\*|__(.*?)__', r'\033[1m\1\2\033[22m', cell)
        cell = re.sub(r'\*(.*?)\*|_(.*?)_', r'\033[3m\1\2\033[23m', cell)
        cell = re.sub(r'`(.*?)`', r'\033[33m\1\033[39m', cell)
        if is_header:
            return f"\033[1;36m{cell}\033[0m"
        return cell

    # Helper to pad a cell based on alignment and visible width
    def pad_cell(cell, width, align):
        vis_len = visible_len(cell)
        padding = width - vis_len
        if padding <= 0:
            return cell
        if align == 'center':
            left = padding // 2
            right = padding - left
            return ' ' * left + cell + ' ' * right
        elif align == 'right':
            return ' ' * padding + cell
        else:
            return cell + ' ' * padding

    # Helper to render a table block
    def render_table(table_rows):
        if len(table_rows) < 2:
            return table_rows
            
        header_cells = parse_row(table_rows[0])
        # Parse alignment from separator row
        alignments = []
        sep_cells = parse_row(table_rows[1])
        for cell in sep_cells:
            if cell.startswith(':') and cell.endswith(':'):
                alignments.append('center')
            elif cell.endswith(':'):
                alignments.append('right')
            else:
                alignments.append('left')
                
        body_rows = [parse_row(r) for r in table_rows[2:]]
        num_cols = len(header_cells)
        
        # Align column counts for body rows
        aligned_body = []
        for r in body_rows:
            if len(r) < num_cols:
                r = r + [''] * (num_cols - len(r))
            elif len(r) > num_cols:
                r = r[:num_cols]
            aligned_body.append(r)
            
        if len(alignments) < num_cols:
            alignments = alignments + ['left'] * (num_cols - len(alignments))
        alignments = alignments[:num_cols]
        
        # Format the header and body cells
        f_header = [format_cell(c, is_header=True) for c in header_cells]
        f_body = [[format_cell(c) for c in r] for r in aligned_body]
        
        # Calculate max column widths
        col_widths = [0] * num_cols
        for col_idx in range(num_cols):
            w = visible_len(f_header[col_idx])
            for row in f_body:
                w = max(w, visible_len(row[col_idx]))
            col_widths[col_idx] = w
            
        # Draw top line
        top_parts = ['─' * (w + 2) for w in col_widths]
        top_line = '\033[90m┌' + '┬'.join(top_parts) + '┐\033[0m'
        
        # Draw header row
        header_parts = []
        for idx, cell in enumerate(f_header):
            padded = pad_cell(cell, col_widths[idx], alignments[idx])
            header_parts.append(f" {padded} ")
        header_line = '\033[90m│\033[0m' + '\033[90m│\033[0m'.join(header_parts) + '\033[90m│\033[0m'
        
        # Draw separator line
        sep_parts = ['─' * (w + 2) for w in col_widths]
        sep_line = '\033[90m├' + '┼'.join(sep_parts) + '┤\033[0m'
        
        # Draw body lines
        body_lines = []
        for row in f_body:
            row_parts = []
            for idx, cell in enumerate(row):
                padded = pad_cell(cell, col_widths[idx], alignments[idx])
                row_parts.append(f" {padded} ")
            body_lines.append('\033[90m│\033[0m' + '\033[90m│\033[0m'.join(row_parts) + '\033[90m│\033[0m')
            
        # Draw bottom line
        bottom_parts = ['─' * (w + 2) for w in col_widths]
        bottom_line = '\033[90m└' + '┴'.join(bottom_parts) + '┘\033[0m'
        
        return [top_line, header_line, sep_line] + body_lines + [bottom_line]

    i = 0
    in_code_block = False
    lang = ""
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            if not in_code_block:
                in_code_block = True
                lang = line[3:].strip().lower()
            else:
                in_code_block = False
                lang = ""
            rendered.append("\033[90m" + "─" * 45 + "\033[0m")
            i += 1
            continue
            
        if in_code_block:
            rendered.append(highlight_line(line, lang))
            i += 1
            continue
            
        # Handle markdown tables
        if not in_code_block and is_table_row(line) and i + 1 < len(lines) and is_table_separator(lines[i + 1]):
            table_rows = []
            while i < len(lines) and is_table_row(lines[i]):
                table_rows.append(lines[i])
                i += 1
            rendered.extend(render_table(table_rows))
            continue

        h_match = re.match(r'^(#{1,6})\s+(.*)', line)
        if h_match:
            level = len(h_match.group(1))
            content = h_match.group(2)
            color = "35" if level == 1 else ("34" if level == 2 else "36")
            rendered.append(f"\n\033[1;{color}m{content}\033[0m")
            i += 1
            continue
            
        list_match = re.match(r'^(\s*[-*+])\s+(.*)', line)
        if list_match:
            indent = list_match.group(1)[:-1]
            content = list_match.group(2)
            line = f"{indent}• {content}"
            
        num_match = re.match(r'^(\s*\d+\.)\s+(.*)', line)
        if num_match:
            prefix = num_match.group(1)
            content = num_match.group(2)
            line = f"{prefix} {content}"

        line = re.sub(r'\*\*(.*?)\*\*|__(.*?)__', r'\033[1m\1\2\033[22m', line)
        line = re.sub(r'\*(.*?)\*|_(.*?)_', r'\033[3m\1\2\033[23m', line)
        line = re.sub(r'`(.*?)`', r'\033[33m\1\033[39m', line)
        
        line = render_math_safely(line)
        
        rendered.append(line)
        i += 1
        
    return "\n".join(rendered)

def main():
    if len(sys.argv) < 2:
        print("Usage: ai_mcp.py [list-tools | call-tool | render-markdown]", file=sys.stderr)
        sys.exit(1)

    action = sys.argv[1]
    mcp_servers = load_config()

    if action == "render-markdown":
        if len(sys.argv) < 3:
            sys.exit(0)
        text = sys.argv[2]
        print(render_markdown(text))
        sys.exit(0)

    if action == "list-tools":
        openai_tools = []

        # Native list_directory
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List the contents of a directory (files and subdirectories) on the host system to explore the project structure.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the directory to list. Defaults to the current working directory '.' if not specified."
                        }
                    }
                }
            }
        })
        
        # Native execute_command
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Run a shell command on the host system and return its stdout/stderr. Use this to inspect files, check disk space, list directories, run diagnostics, or perform tasks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact shell command to execute."
                        }
                    },
                    "required": ["command"]
                }
            }
        })

        # Native web_search
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web using DuckDuckGo Lite to find information, news, code examples, documentation, or facts. No API key needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query."
                        }
                    },
                    "required": ["query"]
                }
            }
        })

        # Native fetch_webpage
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "fetch_webpage",
                "description": "Download and read the text/markdown content of a website/URL to gather detailed information. No API key needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL of the webpage to fetch."
                        }
                    },
                    "required": ["url"]
                }
            }
        })

        # Native save_memory
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "save_memory",
                "description": "Save key facts, user preferences, configurations, or context to persistent memory. This memory is automatically loaded as context in subsequent runs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The exact content to store in memory. Keep it concise."
                        }
                    },
                    "required": ["content"]
                }
            }
        })

        # Native delegate_task
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "delegate_task",
                "description": "Delegate a sub-task or investigation to a new helper AI agent. The agent runs independently with full system access and returns a summary. Use this to parallelize work or break down complex tasks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The detailed instructions/task for the helper agent."
                        }
                    },
                    "required": ["task"]
                }
            }
        })

        # Native read_file
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the contents of a file. Supports text files, PDFs (extracts text content), and image files (PNG, JPG, JPEG, WEBP) which are rendered directly into your vision model context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file to read."
                        }
                    },
                    "required": ["path"]
                }
            }
        })

        # Native write_file
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write new content to a file (creates parent directories if needed).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file to write to."
                        },
                        "content": {
                            "type": "string",
                            "description": "The exact content to write to the file."
                        }
                    },
                    "required": ["path", "content"]
                }
            }
        })

        # Native edit_file
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Apply search-and-replace text edits to an existing file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file to edit."
                        },
                        "search_content": {
                            "type": "string",
                            "description": "The exact text block to search for and replace."
                        },
                        "replace_content": {
                            "type": "string",
                            "description": "The replacement text block."
                        }
                    },
                    "required": ["path", "search_content", "replace_content"]
                }
            }
        })

        for server_name, cfg in mcp_servers.items():
            tools = list_tools(server_name, cfg)
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("inputSchema", {
                            "type": "object",
                            "properties": {}
                        })
                    }
                })

        print(json.dumps(openai_tools))

    elif action == "call-tool":
        if len(sys.argv) < 5:
            print("Usage: ai_mcp.py call-tool <server_name> <tool_name> <arguments_json>", file=sys.stderr)
            sys.exit(1)
        
        server_name = sys.argv[2]
        tool_name = sys.argv[3]
        args_json = sys.argv[4]

        try:
            arguments = json.loads(args_json)
        except Exception as e:
            print(json.dumps({"error": f"Failed to parse arguments JSON: {e}"}))
            sys.exit(1)

        # Route custom tools
        if tool_name == "list_directory" or server_name == "list_directory":
            path = arguments.get("path", ".")
            result = list_directory(path)
            print(result)
        elif tool_name == "web_search" or server_name == "web_search":
            query = arguments.get("query", "")
            result = ddg_lite_search(query)
            print(result)
        elif tool_name == "fetch_webpage" or server_name == "fetch_webpage":
            url = arguments.get("url", "")
            result = fetch_webpage(url)
            print(result)
        elif tool_name == "save_memory" or server_name == "save_memory":
            content = arguments.get("content", "")
            result = save_memory(content)
            print(result)
        elif tool_name == "read_file" or server_name == "read_file":
            path = arguments.get("path", "")
            result = read_file(path)
            print(result)
        elif tool_name == "write_file" or server_name == "write_file":
            path = arguments.get("path", "")
            content = arguments.get("content", "")
            result = write_file(path, content)
            print(result)
        elif tool_name == "edit_file" or server_name == "edit_file":
            path = arguments.get("path", "")
            search_content = arguments.get("search_content", "")
            replace_content = arguments.get("replace_content", "")
            result = edit_file(path, search_content, replace_content)
            print(result)
        elif tool_name == "delegate_task" or server_name == "delegate_task":
            task = arguments.get("task", "")
            try:
                ai_bin = "/usr/local/bin/ai"
                if not os.path.exists(ai_bin):
                    ai_bin = "./ai"
                
                proc = subprocess.run(
                    [ai_bin, task],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=60
                )
                output = proc.stdout.strip()
                err = proc.stderr.strip()
                
                result = ""
                if output:
                    result += output
                if err:
                    result += f"\n[Agent Stderr]: {err}"
                if not result:
                    result = "Agent completed the task but returned no output."
                print(result)
            except Exception as e:
                print(f"Error delegating task: {e}")
        else:
            # Route to MCP server
            cfg = mcp_servers.get(server_name)
            if not cfg:
                # Try matching clean server name
                for k in mcp_servers.keys():
                    clean_k = "".join(c if c.isalnum() or c == "_" else "_" for c in k)
                    if clean_k == server_name:
                        cfg = mcp_servers[k]
                        break

            if not cfg:
                print(json.dumps({"error": f"MCP server '{server_name}' not found in config"}))
                sys.exit(1)

            result = call_tool(server_name, cfg, tool_name, arguments)
            print(json.dumps(result))

if __name__ == "__main__":
    main()
