#!/bin/bash
set -e

echo "Compiling ai..."
gcc -o ai ai.c -lcurl

echo "Installing ai to /usr/bin..."
sudo cp ai /usr/bin/
sudo chmod +x /usr/bin/ai

echo "Done — 'ai' is now installed in /usr/bin/"
