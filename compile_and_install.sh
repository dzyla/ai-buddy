#!/bin/bash
set -e

echo "Compiling ai..."
gcc -o ai ai.c cJSON.c -lcurl

echo "Installing ai to /usr/bin..."
sudo cp ai /usr/bin/
sudo chmod +x /usr/bin/ai
sudo cp ai_mcp.py /usr/bin/
sudo chmod +x /usr/bin/ai_mcp.py

echo "Installing skills to ~/.config/ai/skills/ ..."
SKILLS_SRC="$(dirname "$0")/.agents/skills"
SKILLS_DST="$HOME/.config/ai/skills"
if [ -d "$SKILLS_SRC" ]; then
    mkdir -p "$SKILLS_DST"
    cp -r "$SKILLS_SRC"/. "$SKILLS_DST/"
    echo "  Copied $(ls "$SKILLS_SRC" | wc -l) skill(s) to $SKILLS_DST"
else
    echo "  No .agents/skills/ directory found — skipping."
fi

echo "Done — 'ai' and 'ai_mcp.py' are now installed in /usr/bin/"
