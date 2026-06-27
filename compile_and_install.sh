#!/bin/bash
set -e

echo "Compiling ai..."
gcc -o ai ai.c cJSON.c -lcurl

echo "Installing ai to /usr/bin..."
sudo cp ai /usr/bin/
sudo chmod +x /usr/bin/ai
sudo cp ai_mcp.py /usr/bin/
sudo chmod +x /usr/bin/ai_mcp.py

echo "Done — 'ai' and 'ai_mcp.py' are now installed in /usr/bin/"
