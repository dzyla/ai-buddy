#!/usr/bin/env python3
import re

with open('/home/dzyla/Code/ai-buddy/ai.c', 'r', encoding='utf-8') as f:
    content = f.read()

old = '''static void print_user_message(const char *message) {
    if (!message || !*message) return;

    int cols = lineed_term_cols() - 6;
    if (cols < 40) cols = 40;

    printf("\\n");

    /* Header bar: subtle magenta accent */
    printf("%s╭─%s %s%s%s %sYou%s %s╮\\n",
           CL_MAGENTA, CL_DIM,
           CL_MAGENTA CL_DIM, "▸", CL_RESET,
           CL_MAGENTA CL_BOLD, CL_RESET, CL_DIM);

    /* Content — word-wrapped at terminal width */
    const char *line = message;
    while (*line) {
        const char *nl = strchr(line, '\\n');
        const char *end;
        int len;
        if (nl) {
            int line_len = (int)(nl - line);
            if (line_len <= cols) {
                len = line_len;
                end = nl + 1;
            } else {
                const char *space = NULL;
                for (int i = 0; i < cols && line[i]; i++) {
                    if (line[i] == ' ') space = line + i;
                }
                if (space) {
                    end = space + 1;
                    len = (int)(space - line);
                } else {
                    end = line + cols;
                    len = cols;
                }
            }
        } else {
            int line_len = (int)strlen(line);
            if (line_len <= cols) {
                len = line_len;
                end = line + line_len;
            } else {
                const char *space = NULL;
                for (int i = 0; i < cols && line[i]; i++) {
                    if (line[i] == ' ') space = line + i;
                }
                if (space) {
                    end = space + 1;
                    len = (int)(space - line);
                } else {
                    end = line + cols;
                    len = cols;
                }
            }
        }

        printf("%s│ %.*s%s%*s│%s\\n",
               CL_DIM, len, line,
               CL_RESET,
               (cols - len > 1) ? cols - len - 1 : 0, " ",
               CL_DIM);
        line = end;
    }

    /* Footer bar */
    printf("%s╰─%s%s╯\\n", CL_DIM, CL_DIM, CL_DIM);
    printf("\\n");
}

static void print_markdown_table(const char *content);'''

new = '''static void print_user_message(const char *message) {
    if (!message || !*message) return;

    int cols = lineed_term_cols() - 4;
    if (cols < 40) cols = 40;

    printf("\\n%s▸ %s", CL_MAGENTA, CL_RESET);

    /* Content — word-wrapped at terminal width */
    const char *line = message;
    while (*line) {
        const char *nl = strchr(line, '\\n');
        const char *end;
        int len;
        if (nl) {
            int line_len = (int)(nl - line);
            if (line_len <= cols) {
                len = line_len;
                end = nl + 1;
            } else {
                const char *space = NULL;
                for (int i = 0; i < cols && line[i]; i++) {
                    if (line[i] == ' ') space = line + i;
                }
                if (space) {
                    end = space + 1;
                    len = (int)(space - line);
                } else {
                    end = line + cols;
                    len = cols;
                }
            }
        } else {
            int line_len = (int)strlen(line);
            if (line_len <= cols) {
                len = line_len;
                end = line + line_len;
            } else {
                const char *space = NULL;
                for (int i = 0; i < cols && line[i]; i++) {
                    if (line[i] == ' ') space = line + i;
                }
                if (space) {
                    end = space + 1;
                    len = (int)(space - line);
                } else {
                    end = line + cols;
                    len = cols;
                }
            }
        }

        printf("%.*s%s%*s\\n",
               len, line, CL_RESET,
               (cols - len > 1) ? cols - len - 1 : 0, " ");
        line = end;
    }

    printf("\\n");
}'''

if old in content:
    content = content.replace(old, new)
    with open('/home/dzyla/Code/ai-buddy/ai.c', 'w', encoding='utf-8') as f:
        f.write(content)
    print('OK')
else:
    # Try finding the function start and end, then replace
    start = content.find('static void print_user_message(const char *message) {')
    if start >= 0:
        # Find the matching closing brace for the function
        # Count braces
        end_marker = content.find('static void print_markdown_table', start)
        if end_marker >= 0:
            content = content[:start] + new + '\n' + content[end_marker:]
            with open('/home/dzyla/Code/ai-buddy/ai.c', 'w', encoding='utf-8') as f:
                f.write(content)
            print('OK (fallback)')
        else:
            print('Could not find end marker')
    else:
        print('Could not find start')
