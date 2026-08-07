#!/usr/bin/env python3
"""Update CLI visuals for better modern interface."""

import re

with open('/home/dzyla/Code/ai-buddy/ai.c', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update print_user_message header
old_user_header = '''    /* Header bar */
    printf("%s╭%s %s%s  %sYou%s%s%s╮\\n",
           CL_DIM, CL_MAGENTA, CL_DIM, "─",
           CL_MAGENTA CL_BOLD, CL_RESET, CL_DIM, CL_DIM);'''

new_user_header = '''    /* Header bar: subtle magenta accent */
    printf("%s╭─%s %s%s%s %sYou%s %s╮\\n",
           CL_MAGENTA, CL_DIM,
           CL_MAGENTA CL_DIM, "▸", CL_RESET,
           CL_MAGENTA CL_BOLD, CL_RESET, CL_DIM);'''

if old_user_header in content:
    content = content.replace(old_user_header, new_user_header)
    print("✓ Updated print_user_message header")
else:
    print("✗ print_user_message header not found")

# Update footer for user message
old_user_foot = '    printf("%s╰%s%s╯\\n", CL_DIM, CL_DIM, CL_DIM);'
new_user_foot = '    printf("%s╰─%s%s╯\\n", CL_DIM, CL_DIM, CL_DIM);'
if old_user_foot in content:
    content = content.replace(old_user_foot, new_user_foot)
    print("✓ Updated user message footer")

# 2. Update print_response_box
old_resp_header = '''    /* Header bar: model name + turn/tool count */
    printf("%s╭%s %s●%s %s%s%s%s╮\\n",
           CL_DIM, CL_RESET, CL_MAGENTA, CL_RESET,
           CL_MAGENTA CL_BOLD, model_name ? model_name : "assistant",
           CL_DIM, CL_DIM);'''

new_resp_header = '''    /* Header bar: model name + turn/tool count */
    printf("%s╭─%s %s◉%s %s%s%s%s╮\\n",
           CL_MAGENTA, CL_RESET, CL_MAGENTA, CL_RESET,
           CL_MAGENTA CL_BOLD, model_name ? model_name : "assistant",
           CL_DIM, CL_DIM);'''

if old_resp_header in content:
    content = content.replace(old_resp_header, new_resp_header)
    print("✓ Updated print_response_box header")

# Update response footer
old_resp_foot = '    printf("%s╰%s%s╯\\n\\n", CL_DIM, CL_DIM, CL_DIM);'
new_resp_foot = '    printf("%s╰─%s%s╯\\n\\n", CL_DIM, CL_DIM, CL_DIM);'
if old_resp_foot in content:
    content = content.replace(old_resp_foot, new_resp_foot)
    print("✓ Updated response box footer")

# 3. Update print_tool_box
old_tool_header = '''    printf("%s╭%s %s⚙%s %s%s%s%s╮\\n",
           CL_DIM, CL_RESET, hc, CL_RESET,
           CL_BOLD, name, CL_RESET, CL_DIM);'''

new_tool_header = '''    printf("%s╭─%s %s⚙%s %s%s%s%s╮\\n",
           CL_MAGENTA, CL_RESET, hc, CL_RESET,
           CL_BOLD, name, CL_RESET, CL_DIM);'''

if old_tool_header in content:
    content = content.replace(old_tool_header, new_tool_header)
    print("✓ Updated print_tool_box header")

old_tool_foot = '    printf("%s╰%s%s╯\\n", CL_DIM, CL_DIM, CL_DIM);'
new_tool_foot = '    printf("%s╰─%s%s╯\\n", CL_DIM, CL_DIM, CL_DIM);'
if old_tool_foot in content:
    content = content.replace(old_tool_foot, new_tool_foot)
    print("✓ Updated tool box footer")

# 4. Update print_warning_box
old_warn_header = '''    printf("%s╭%s %s⚠%s %s%s%s%s╮\\n",
           CL_DIM, CL_RESET, CL_RED, CL_RESET,
           CL_RED CL_BOLD, title, CL_RESET, CL_DIM);'''

new_warn_header = '''    printf("%s╭─%s %s⚠%s %s%s%s%s╮\\n",
           CL_RED, CL_RESET, CL_RED, CL_RESET,
           CL_RED CL_BOLD, title, CL_RESET, CL_DIM);'''

if old_warn_header in content:
    content = content.replace(old_warn_header, new_warn_header)
    print("✓ Updated print_warning_box header")

old_warn_foot = '    printf("%s╰%s%s╯\\n\\n", CL_DIM, CL_DIM, CL_DIM);'
new_warn_foot = '    printf("%s╰─%s%s╯\\n\\n", CL_RED, CL_DIM, CL_DIM);'
if old_warn_foot in content:
    content = content.replace(old_warn_foot, new_warn_foot)
    print("✓ Updated warning box footer")

# 5. Update print_info_box
old_info_header = '''    printf("%s╭%s %sℹ%s %s%s%s%s╮\\n",
           CL_DIM, CL_RESET, CL_CYAN, CL_RESET,
           CL_CYAN CL_BOLD, title, CL_RESET, CL_DIM);'''

new_info_header = '''    printf("%s╭─%s %sℹ%s %s%s%s%s╮\\n",
           CL_CYAN, CL_RESET, CL_CYAN, CL_RESET,
           CL_CYAN CL_BOLD, title, CL_RESET, CL_DIM);'''

if old_info_header in content:
    content = content.replace(old_info_header, new_info_header)
    print("✓ Updated print_info_box header")

old_info_foot = '    printf("%s╰%s%s╯\\n\\n", CL_DIM, CL_DIM, CL_DIM);'
new_info_foot = '    printf("%s╰─%s%s╯\\n\\n", CL_CYAN, CL_DIM, CL_DIM);'
if old_info_foot in content:
    content = content.replace(old_info_foot, new_info_foot)
    print("✓ Updated info box footer")

# Save
with open('/home/dzyla/Code/ai-buddy/ai.c', 'w', encoding='utf-8') as f:
    f.write(content)
print("\n✓ All visuals updated successfully")
