"""Terminal markdown rendering (render-markdown subcommand) with syntax highlighting and math."""
import re

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

    # Numbers must run FIRST — before keyword/constant substitutions inject digits
    # inside ANSI escape codes (e.g. \033[1;33m), which would otherwise be
    # re-matched by \b(\d+)\b and corrupt the sequence.
    temp_line = re.sub(r'\b(\d+)\b', r'\033[35m\1\033[0m', temp_line)

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
    
    for idx, s in enumerate(strings):
        temp_line = temp_line.replace(string_placeholder.format(idx), f"\033[32m{s}\033[0m")
        
    return f"  {temp_line}"

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
        
    text = re.sub(r'\^\{([^}]+)\}|\^([0-9+\-nix]+)', repl_super, text)
    
    def repl_sub(match):
        val = match.group(1) or match.group(2)
        return "".join(subscripts.get(c, c) for c in val)
        
    text = re.sub(r'\_\{([^}]+)\}|\_([0-9+\-ijkx]+)', repl_sub, text)
    text = re.sub(r'√\{([^}]+)\}', r'√\1', text)
    return text

def render_math_safely(line):
    code_placeholder = "___CODE_PLACEHOLDER_{}___"
    codes = []
    
    def repl_code(match):
        codes.append(match.group(0))
        return code_placeholder.format(len(codes) - 1)
        
    # Protect backtick inline code
    temp_line = re.sub(r'`[^`\n]+`', repl_code, line)
    
    # Render block math $$ ... $$
    def repl_block_math(match):
        math_content = match.group(1)
        return render_math(math_content)
        
    temp_line = re.sub(r'\$\$(.*?)\$\$', repl_block_math, temp_line)
    
    # Render inline math $ ... $
    def repl_inline_math(match):
        math_content = match.group(1)
        return render_math(math_content)
        
    temp_line = re.sub(r'\$([^$]+)\$', repl_inline_math, temp_line)
    
    # Render scientific notation outside math blocks (e.g. 10^-3 or 2^10)
    superscripts = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾', 'n': 'ⁿ', 'i': 'ⁱ'}
    def repl_super(match):
        val = match.group(1) or match.group(2)
        return "".join(superscripts.get(c, c) for c in val)
    
    temp_line = re.sub(r'(?<=\d)\^\{([^}]+)\}|(?<=\d)\^([0-9+\-nix]+)', repl_super, temp_line)
    
    # Restore code blocks
    for idx, c in enumerate(codes):
        temp_line = temp_line.replace(code_placeholder.format(idx), c)
    return temp_line

def render_markdown(text):
    if not text:
        return ""
    try:
        from rich.console import Console
        from rich.markdown import Markdown, Heading, BlockQuote
        from rich.text import Text
        from rich.segment import Segment
        import shutil

        def custom_heading_rich_console(self, console, options):
            text = self.text
            text.justify = "left"
            if self.tag == "h1":
                yield Text("")
                yield Text("# ", style="bold cyan") + text
                yield Text("")
            elif self.tag == "h2":
                yield Text("")
                yield Text("## ", style="bold magenta") + text
                yield Text("")
            else:
                yield Text("")
                yield Text("#" * int(self.tag[1]) + " ", style="bold yellow") + text
                yield Text("")

        def custom_blockquote_rich_console(self, console, options):
            render_options = options.update(width=options.max_width - 4)
            lines = console.render_lines(self.elements, render_options, style=self.style)
            style = self.style
            new_line = Segment("\n")
            padding = Segment("  ", style)
            for line in lines:
                yield padding
                yield from line
                yield new_line

        Heading.__rich_console__ = custom_heading_rich_console
        BlockQuote.__rich_console__ = custom_blockquote_rich_console

        cols = shutil.get_terminal_size((80, 24)).columns
        console = Console(width=min(cols, 110), force_terminal=True, legacy_windows=False)
        with console.capture() as capture:
            console.print(Markdown(text))
        raw_res = capture.get()
        lines = [line.rstrip() for line in raw_res.splitlines()]
        res = "\n".join(lines).strip('\n')
        if res:
            return res
    except Exception:
        pass

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
        if parts[0].strip() == '': parts = parts[1:]
        if len(parts) > 0 and parts[-1].strip() == '': parts = parts[:-1]
        return [cell.strip() for cell in parts]
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    def visible_len(t):
        return len(ansi_escape.sub('', t))
    def format_cell(cell, is_header=False):
        cell = re.sub(r'\*\*(.*?)\*\*', r'\033[1m\1\033[22m', cell)
        cell = re.sub(r'(?<!\w)__(.*?)__(?!\w)', r'\033[1m\1\033[22m', cell)
        cell = re.sub(r'\*(.*?)\*', r'\033[3m\1\033[23m', cell)
        cell = re.sub(r'(?<!\w)_(.*?)_(?!\w)', r'\033[3m\1\033[23m', cell)
        cell = re.sub(r'`(.*?)`', r'\033[33m\1\033[39m', cell)
        cell = render_math_safely(cell)
        if is_header:
            return f"\033[1;36m{cell}\033[0m"
        return cell
    def pad_cell(cell, width, align):
        vis_len = visible_len(cell)
        padding = width - vis_len
        if padding <= 0: return cell
        if align == 'center':
            left = padding // 2
            right = padding - left
            return ' ' * left + cell + ' ' * right
        elif align == 'right':
            return ' ' * padding + cell
        else: return cell + ' ' * padding
    def render_table(table_rows):
        if len(table_rows) < 2: return table_rows
        header_cells = parse_row(table_rows[0])
        alignments = []
        sep_cells = parse_row(table_rows[1])
        for cell in sep_cells:
            if cell.startswith(':') and cell.endswith(':'): alignments.append('center')
            elif cell.endswith(':'): alignments.append('right')
            else: alignments.append('left')
        body_rows = [parse_row(r) for r in table_rows[2:]]
        num_cols = len(header_cells)
        aligned_body = []
        for r in body_rows:
            if len(r) < num_cols: r = r + [''] * (num_cols - len(r))
            elif len(r) > num_cols: r = r[:num_cols]
            aligned_body.append(r)
        if len(alignments) < num_cols: alignments = alignments + ['left'] * (num_cols - len(alignments))
        alignments = alignments[:num_cols]

        raw_widths = [0] * num_cols
        for col_idx in range(num_cols):
            w = len(header_cells[col_idx])
            for r in aligned_body:
                w = max(w, len(r[col_idx]))
            raw_widths[col_idx] = w

        import shutil
        term_width = shutil.get_terminal_size((80, 24)).columns
        border_overhead = 3 * num_cols + 1
        available_width = term_width - border_overhead

        col_widths = list(raw_widths)
        total_raw = sum(raw_widths)
        if total_raw > available_width and available_width > 0:
            allocated = [min(w, 8) for w in raw_widths]
            remaining = available_width - sum(allocated)
            if remaining > 0:
                needs_more = [i for i, w in enumerate(raw_widths) if w > allocated[i]]
                if needs_more:
                    total_needed = sum(raw_widths[i] - allocated[i] for i in needs_more)
                    for i in needs_more:
                        extra = int(remaining * (raw_widths[i] - allocated[i]) / total_needed)
                        allocated[i] += extra
                    diff = available_width - sum(allocated)
                    idx = 0
                    while diff > 0 and needs_more:
                        allocated[needs_more[idx % len(needs_more)]] += 1
                        diff -= 1
                        idx += 1
            col_widths = [max(1, w) for w in allocated]

        def wrap_text(text, width):
            if not text:
                return [""]
            segments = text.split('\n')
            all_lines = []
            for segment in segments:
                words = segment.split(' ')
                current_line = []
                current_len = 0
                for word in words:
                    word_len = len(word)
                    if current_len + word_len + (1 if current_line else 0) <= width:
                        current_line.append(word)
                        current_len += word_len + (1 if len(current_line) > 1 else 0)
                    else:
                        if word_len > width:
                            if current_line:
                                all_lines.append(" ".join(current_line))
                            for j in range(0, len(word), width):
                                all_lines.append(word[j:j+width])
                            current_line = []
                            current_len = 0
                        else:
                            if current_line:
                                all_lines.append(" ".join(current_line))
                            current_line = [word]
                            current_len = word_len
                if current_line:
                    all_lines.append(" ".join(current_line))
            return all_lines

        def render_wrapped_row(row_cells, is_header=False, row_idx=None):
            wrapped_cells = []
            for col_idx in range(num_cols):
                wrapped_cells.append(wrap_text(row_cells[col_idx], col_widths[col_idx]))
            max_lines = max(len(c) for c in wrapped_cells)
            for col_idx in range(num_cols):
                while len(wrapped_cells[col_idx]) < max_lines:
                    wrapped_cells[col_idx].append("")
            row_lines = []
            for line_idx in range(max_lines):
                line_parts = []
                for col_idx in range(num_cols):
                    cell_line = wrapped_cells[col_idx][line_idx]
                    formatted = format_cell(cell_line, is_header)
                    padded = pad_cell(formatted, col_widths[col_idx], alignments[col_idx])
                    # Alternating row shading for body rows
                    if row_idx is not None and row_idx % 2 == 1 and not is_header:
                        # Subtle dim background for even-indexed body rows
                        padded = f"\033[48;5;236m{padded}\033[0m"
                    line_parts.append(f" {padded} ")
                row_lines.append('\033[90m│\033[0m' + '\033[90m│\033[0m'.join(line_parts) + '\033[90m│\033[0m')
            return row_lines

        top_parts = ['─' * (w + 2) for w in col_widths]
        top_line = '\033[90m┌' + '┬'.join(top_parts) + '┐\033[0m'
        
        header_lines = render_wrapped_row(header_cells, is_header=True)
        
        sep_parts = ['─' * (w + 2) for w in col_widths]
        sep_line = '\033[90m├' + '┼'.join(sep_parts) + '┤\033[0m'
        
        body_lines = []
        for row_idx, row in enumerate(aligned_body):
            body_lines.extend(render_wrapped_row(row, is_header=False, row_idx=row_idx))
            
        bottom_parts = ['─' * (w + 2) for w in col_widths]
        bottom_line = '\033[90m└' + '┴'.join(bottom_parts) + '┘\033[0m'
        
        return [top_line] + header_lines + [sep_line] + body_lines + [bottom_line]

    i = 0
    in_code_block = False
    lang = ""
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            if not in_code_block:
                in_code_block = True
                lang = line[3:].strip().lower()
                lang_display = lang if lang else "text"
                # Badge-style language label with dim background
                badge = f"\033[38;5;245m {lang_display} \033[0m"
                rendered.append(f"{badge}  \033[90m{'─' * 40}\033[0m")
                code_line_num = 0
            else:
                in_code_block = False
                lang = ""
                rendered.append(f"\033[90m{'─' * 42}\033[0m")
            i += 1
            continue
            
        if in_code_block:
            # Line numbers in dim gray, content with syntax highlighting
            rendered.append(f"  \033[90m{code_line_num:4d}\033[0m {highlight_line(line, lang)[2:]}")
            code_line_num += 1
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
            if level == 1:
                rendered.append(f"\n\033[1;35m{content}\033[0m")
                rendered.append(f"\033[35m{'─' * len(content)}\033[0m")
            elif level == 2:
                rendered.append(f"\n\033[1;34m▸ {content}\033[0m")
            else:
                color = "36" if level == 3 else "90"
                rendered.append(f"\n\033[1;{color}m{content}\033[0m")
            i += 1
            continue

        # Blockquotes: render with a left accent border
        if line.startswith('> '):
            bq_content = line[2:]
            rendered.append(f"  \033[33m│\033[0m {bq_content}")
            i += 1
            continue
            
        list_match = re.match(r'^(\s*[-*+])\s+(.*)', line)
        if list_match:
            indent = list_match.group(1)[:-1]
            content = list_match.group(2)
            # Use colored bullets
            line = f"{indent}\033[36m•\033[0m {content}"
            
        num_match = re.match(r'^(\s*\d+\.)\s+(.*)', line)
        if num_match:
            prefix = num_match.group(1)
            content = num_match.group(2)
            line = f"\033[36m{prefix}\033[0m {content}"

        line = re.sub(r'\*\*(.*?)\*\*', r'\033[1m\1\033[22m', line)
        line = re.sub(r'(?<!\w)__(.*?)__(?!\w)', r'\033[1m\1\033[22m', line)
        line = re.sub(r'\*(.*?)\*', r'\033[3m\1\033[23m', line)
        line = re.sub(r'(?<!\w)_(.*?)_(?!\w)', r'\033[3m\1\033[23m', line)
        line = re.sub(r'`(.*?)`', r'\033[33m\1\033[39m', line)
        
        line = render_math_safely(line)
        
        rendered.append(line)
        i += 1
        
    return "\n".join(rendered)
