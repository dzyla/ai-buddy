# Zulip Integration Setup Guide

This guide explains how to set up the **Zulip MCP Server** and the **Real-Time Zulip Listener Bridge** to interact with your local `ai` CLI agent from your phone or desktop via Zulip.

---

## Architecture Overview

There are two primary integration components:
1. **MCP Server ([zulip_mcp_server.py](file:///home/dzyla/Code/ai-buddy/zulip_mcp_server.py))**: Registers with the local `ai` agent as a Model Context Protocol tool. This allows the local `ai` agent (when run from your terminal) to read/write messages to Zulip.
2. **Real-Time Bridge ([zulip_ai_bridge.py](file:///home/dzyla/Code/ai-buddy/zulip_ai_bridge.py))**: A background listener daemon that monitors your Zulip streams or direct messages, runs incoming queries through the `ai` agent, and posts responses back to Zulip.

---

## Step 1: Create a Zulip Bot
To authenticate, you must register a bot user on your Zulip organization page (e.g., `zylalab.zulipchat.com`):
1. Click the **gear icon** (⚙️) -> **Personal settings**.
2. On the left sidebar under **Your account**, click **Bots**.
3. Click **Add a new bot**.
4. Set the following fields:
   * **Bot type**: `Generic bot`
   * **Name**: Choose a display name (e.g. `AI Bot`)
   * **Bot username**: Enter a username (e.g. `ai-bot`)
5. Click **Create bot**.
6. Once created, click the **Download zuliprc** button next to your bot in the active bots list.

---

## Step 2: Configure Credentials locally

Place the downloaded credentials inside your home directory. The Python Zulip client automatically looks here for credentials:

1. Create a file at `~/.zuliprc` (or `~/.config/zulip/zuliprc`).
2. Paste the credentials in the following format:
   ```ini
   [api]
   email=ai-bot@zylalab.zulipchat.com
   key=your-bot-api-key
   site=https://zylalab.zulipchat.com
   ```

---

## Step 3: Set up the MCP Server (Global)

To allow your local CLI `ai` agent to use Zulip as a tool:
1. Create or edit your global MCP configuration file: **[~/.config/ai/mcp.json](file:///home/dzyla/.config/ai/mcp.json)**.
2. Register the `zulip` server block:
   ```json
   {
     "mcpServers": {
       "zulip": {
         "command": "python3",
         "args": ["/home/dzyla/Code/ai-buddy/zulip_mcp_server.py"],
         "env": {
           "ZULIP_SITE": "https://zylalab.zulipchat.com"
         }
       }
     }
   }
   ```
3. Test that the agent registers the tools by running:
   ```bash
   ai "what tools do you have for zulip?"
   ```

---

## Step 4: Run the Real-Time Listener via systemd

To run the listener daemon as a background service under your user context (no root/sudo privileges needed):

1. Create the unit file at **[~/.config/systemd/user/zulip-ai-bridge.service](file:///home/dzyla/.config/systemd/user/zulip-ai-bridge.service)**:
   ```ini
   [Unit]
   Description=Zulip to local AI CLI Bridge
   After=network.target

   [Service]
   ExecStart=/home/dzyla/miniconda3/bin/python3 -u /home/dzyla/Code/ai-buddy/zulip_ai_bridge.py
   Restart=always
   RestartSec=5
   WorkingDirectory=/home/dzyla/Code/ai-buddy
   Environment="PATH=/home/dzyla/.local/bin:/usr/local/bin:/usr/bin:/bin"
   # Optional: Restrict to a specific Zulip username/email (defaults to the local OS user, e.g. "dzyla")
   # Environment="ZULIP_USER=your-username-or-email"

   [Install]
   WantedBy=default.target
   ```
   *(Note: Ensure `ExecStart` points to the Python environment containing the `zulip` package).*

2. Enable and start the service:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable zulip-ai-bridge.service
   systemctl --user start zulip-ai-bridge.service
   ```

3. Manage the service:
   * **Check status**: `systemctl --user status zulip-ai-bridge.service`
   * **View logs**: `journalctl --user -u zulip-ai-bridge.service -f`
   * **Restart**: `systemctl --user restart zulip-ai-bridge.service`

---

## Chatting via Mobile
1. Go to your Zulip Android/iOS app.
2. Create a private stream (channel) and subscribe your bot (`ai-bot@zylalab.zulipchat.com`) to it, or start a Direct Message.
3. Message the bot, and it will respond directly in Zulip!
