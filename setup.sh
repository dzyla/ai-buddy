#!/bin/bash
set -e

echo "1. Installing prerequisites..."
sudo apt-get update
sudo apt-get install -y gcc libcurl4-openssl-dev

echo "2. Building and installing the 'ai' CLI..."
gcc -o ai ai.c cJSON.c -lcurl
sudo cp ai /usr/local/bin/
sudo chmod +x /usr/local/bin/ai
sudo cp ai_mcp.py /usr/local/bin/
sudo chmod +x /usr/local/bin/ai_mcp.py
echo "Installed 'ai' and 'ai_mcp.py' to /usr/local/bin/"

echo "3. Installing the gemma4 inference snap..."
sudo snap install gemma4

# Connecting hardware-observe allows the snap to detect your CPU/GPU 
# and automatically select the most optimized runtime engine.
sudo snap connect gemma4:hardware-observe
sudo gemma4 use-engine --auto

echo "4. Fetching the local API endpoint..."
# Give the inference server a few seconds to initialize
sleep 5

# Extract the OpenAI endpoint URL from the snap's status output
ENDPOINT=$(gemma4 status | grep -i 'openai:' | awk '{print $2}')

# Fallback to the standard default if the extraction misses
if [ -z "$ENDPOINT" ]; then
    ENDPOINT="http://localhost:9090/v1"
fi

# Ensure the endpoint ends with a trailing slash for ai's URL builder
if [[ "$ENDPOINT" != */ ]]; then
    ENDPOINT="${ENDPOINT}/"
fi

echo "5. Configuring environment variables..."
CONFIG_SNIPPET="
# ai configuration for local gemma4 inference snap
export INFER_BASE_URL=\"$ENDPOINT\"
export INFER_API_KEY=\"not-needed\"
export INFER_MODEL=\"gemma4\"
export PUBMED_API_KEY=\"myapp_kZnDpemyN9z43CqNrOYEE-LhAH9_UsxhWTavLkWv22Y\"
"

# Append configuration to the user's bash profile
echo "$CONFIG_SNIPPET" >> ~/.bashrc

echo "========================================"
echo "Installation and configuration complete!"
echo "Run the following command to apply the changes to your current terminal:"
echo "source ~/.bashrc"
echo ""
echo "You can then test the pipeline:"
echo "dmesg | tail -n 20 | ai \"Are there any hardware warnings in these logs?\""
