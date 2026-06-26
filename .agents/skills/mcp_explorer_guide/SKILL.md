---
name: mcp-explorer-guide
description: Guidelines to enforce codebase exploration before modifying code. Instructs the model to list directory contents, locate functions/symbols, and read file contexts before proposing changes.
---

# Codebase Explorer Guidelines

Smaller models sometimes make assumptions about the existing code files and edit them blindly, leading to compilation issues or lost logic. Follow these exploration rules:

## 1. Map the Workspace First
- Before editing or writing code, run `list_directory` on the target path to see the surrounding files and structure.
- Do not assume a file is in a specific directory; search for it if you aren't sure of its exact path.

## 2. Read Existing Declarations
- Always inspect the header files (e.g., `.h` files) or main class/module definitions before implementing new functions.
- If editing a function, use `read_file` or a search tool to check how that function is called in other parts of the codebase to avoid breaking changes.

## 3. Style and Comment Alignment
- Inspect 10-20 lines of the file you intend to modify.
- Align your indentation, brackets, commenting style, and naming conventions with the surrounding code.

## 4. Validate Build Rules
- Look for files like `Makefile`, `setup.sh`, `build.gradle`, or `package.json` to understand how the project is compiled or run, ensuring your changes respect the build configuration.
