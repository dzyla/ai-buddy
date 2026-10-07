# Zulip setup: talk to your local `ai` agent from Zulip

This guide connects the `ai` agent to Zulip so you can DM it (or @-mention it
in a channel) from any device. It uses two parts of this repo:

| Part | What it does |
|---|---|
| `zulip_ai_bridge.py` | A long-running listener. It receives your Zulip messages, runs `ai` locally, and posts the answer back. It is a thin entry point over the `zulip_bridge/` package (`install.sh` copies the package to `~/.local/share/ai/lib`). |
| `zulip_mcp_server.py` | Optional tools the agent can call (`zulip_send_message`, `zulip_get_messages`, `zulip_add_reaction`, `zulip_edit_message`). Reminders also use it. |

Examples below use `example.zulipchat.com` as the organization. Replace it with
your own Zulip address.

**Quick start** (each step is explained below):

```bash
python3 -m pip install zulip                       # 1. dependency (into the python3 the service will use)
mv ~/Downloads/zuliprc ~/.zuliprc && chmod 600 ~/.zuliprc   # 2. the bot's credentials
echo 'export ZULIP_USER="42"' >> ~/.local/share/ai/env      # 3. optional: your user id or email
cd ~/ai-buddy && python3 zulip_ai_bridge.py --check --online # 4. validate the setup
python3 zulip_ai_bridge.py                         # 5. foreground test (DM the bot), then Ctrl+C
./install.sh                                       # 6. installs the systemd unit (does not enable it)
systemctl --user enable --now zulip-ai-bridge      # 7. run it in the background
loginctl enable-linger "$USER"                     # 8. keep it running without a login (WSL/headless)
```

---

## 1. Create a Zulip bot (recommended over a regular account)

1. In Zulip, open **Personal settings → Bots** and click **Add a new bot**.
   Some newer Zulip versions list bots under **Organization settings → Bots**
   instead.
2. Set **Bot type** to **Generic bot**. An "Incoming webhook" bot can only
   post messages, so the bridge cannot read anything with it.
3. Pick a name (for example `AI bot`) and an email prefix (for example `ai`).
   Zulip then creates an address like `ai-bot@example.zulipchat.com`.
4. Click **Add**.

If the button is missing, your organization limits who can create bots
(**Organization settings → Organization permissions → Who can create bots**).
Ask an administrator.

### Why a bot account and not your own

- **You become the owner automatically.** Zulip stores who created a bot as
  its `bot_owner_id`. At startup the bridge reads this value and allows only
  that person, so you usually don't need to configure an owner at all.
- **Your own account cannot work.** The bridge ignores every message sent from
  its own account, because otherwise it would answer its own replies. If it used
  your login, it would ignore everything you write.
- **Smaller blast radius.** A bot's API key only grants access to the bot.
  Your personal API key grants full access to your account, including every DM
  you have received. You can regenerate or deactivate a bot key at any time
  without affecting your login.
- Replies are visibly marked as coming from a bot.

## 2. Download the zuliprc and put it where the code reads it

In **Personal settings → Bots**, find your bot and click its **download
zuliprc** icon. The file looks like this:

```ini
[api]
email=ai-bot@example.zulipchat.com
key=<the bot's API key>
site=https://example.zulipchat.com
```

Save it as **`~/.zuliprc`** and make it private:

```bash
mv ~/Downloads/zuliprc ~/.zuliprc      # on WSL, Windows downloads are under /mnt/c/Users/<you>/Downloads/
chmod 600 ~/.zuliprc
```

`~/.zuliprc` is the one location that every component reads.

| Component | Where it looks for credentials, in order |
|---|---|
| Bridge (`zulip_ai_bridge.py`), via `zulip.Client()` | The `ZULIP_EMAIL`, `ZULIP_API_KEY` and `ZULIP_SITE` env vars override individual values. The file is `$ZULIP_CONFIG` if set, otherwise `~/.zuliprc`. |
| MCP tools and reminders (`zulip_mcp_server.py`) | 1. `ZULIP_SITE` + `ZULIP_EMAIL` + `ZULIP_API_KEY` (all three) → 2. `$ZULIP_CONFIG_PATH` → 3. `~/.config/zulip/zuliprc` → 4. the same lookup as the bridge (`$ZULIP_CONFIG`, then `~/.zuliprc`) |

To keep the file somewhere else, set `ZULIP_CONFIG=/path/to/zuliprc`. Both
components honor that variable. A file only at `~/.config/zulip/zuliprc` is
**not** found by the bridge.

Don't put the API key in `ZULIP_API_KEY` unless you have to. The scheduler
copies every `ZULIP_*` variable into its task files under
`~/.config/ai/scheduled_tasks/`.

## 3. Choose who is allowed to talk to the bot (`ZULIP_USER`)

The bridge answers exactly **one** person, the owner. Messages from anyone
else are dropped silently. The owner is determined at startup, in this order:

1. **`ZULIP_USER`**, if set.
2. Otherwise the **bot's owner** (`bot_owner_id`, the person who created the bot).
3. Otherwise, if exactly one person has ever DMed the bot (checking the last 20
   DMs), that person.
4. Otherwise **nobody**. The bridge fails closed and logs
   `⚠️ No owner configured or detected: ALL messages will be ignored.`

If you created the bot yourself, step 2 usually covers you and you can skip
`ZULIP_USER`. Set it if someone else created the bot, if you want to be
explicit, or if the bot ignores you.

### Accepted formats

| `ZULIP_USER` value | How it matches | Notes |
|---|---|---|
| `42` (numeric user id) | the sender's user id is 42, or the sender's email is `user42@...` | **Recommended.** Works even if your organization hides email addresses. |
| `you@example.com` | exact, case-insensitive match on the sender's email *as the bot sees it* | Fails if emails are hidden. See below. |
| `you` (bare username) | the part before `@` of the sender's email | Weakest. Prefer one of the other two. |

Display names are never matched, because anyone can rename themselves to
look like you.

### Finding your user id and email

- **Easiest:** after step 2, run `python3 zulip_ai_bridge.py --check --online`
  (section 5). If you created the bot, `bot_owner_id=...` is your user id, and
  the line also shows your email exactly as the bot sees it.
- **Email:** **Personal settings → Account & privacy** shows your email and
  who is allowed to see it.
- **User id:** in recent Zulip versions, click your avatar and choose
  **View your profile**. The profile shows your user ID.

**Hidden emails.** Many organizations show members' email addresses only to
administrators. In that case the bot sees you as
`user42@example.zulipchat.com` instead of your real address, and
`ZULIP_USER=you@example.com` will not match. Use the number (`42`) instead.

### Where to put it

Add it to the env file the `ai` tool already uses:

```bash
echo 'export ZULIP_USER="42"' >> ~/.local/share/ai/env
```

The bridge loads `~/.local/share/ai/env` itself at startup. Values already set
in the process environment take priority, so
`ZULIP_USER=42 python3 zulip_ai_bridge.py` overrides the file. `ai-backend`
keeps unknown keys like `ZULIP_USER` when it rewrites the file, but it drops
comment lines.

## 4. Install the dependency

The bridge needs the `zulip` package (plus `requests`, which it pulls in).
Install it into **the same interpreter that will run the bridge**:

```bash
command -v python3            # the interpreter install.sh pins in the service
python3 -m pip install zulip
```

The `--check` output starts with `Python interpreter: ...`, which shows
exactly which interpreter needs the package.

- The agent's own tools run as `python3 ai_mcp.py ...`, using the first
  `python3` on `PATH`. Reminders and the Zulip MCP tools need `zulip` in that
  interpreter too. If you have several Pythons (conda plus the system Python),
  install it in both, or make sure one comes first everywhere.
- On Debian and Ubuntu, the system `/usr/bin/python3` may have no `pip`
  (`No module named pip`), or it may refuse with `externally-managed-environment`.
  Use your conda/venv Python, or run `sudo apt install python3-pip` and then
  `python3 -m pip install --user --break-system-packages zulip`.
- Optional extras for reading attachments: `pdfplumber`, `pypdfium2` or
  `pypdf` (PDFs), `openpyxl` (Excel), and `pytesseract` plus `Pillow` (OCR
  of images, which also needs the `tesseract` program).

## 5. Check the configuration (`--check`)

```bash
cd ~/ai-buddy
python3 zulip_ai_bridge.py --check            # offline: never contacts Zulip
python3 zulip_ai_bridge.py --check --online   # also logs in and resolves the owner
```

`doctor` works as an alias for `--check`. The check verifies these things:

- the package is importable;
- the zuliprc exists, is readable, has `chmod 600`, and contains
  `email`, `key` and `site`;
- a client can be constructed;
- `ZULIP_USER` is set, and which format it uses;
- the `ai` binary can be found (`$INFER_BIN_PATH`, then `./ai` next to the
  script, then `~/.local/bin/ai`, then `PATH`).

With `--online` it also calls `get_profile`, which catches a wrong key or site,
then prints the bot's `bot_owner_id` and the **effective owner** together with
where it came from. The exit code is 0 when there are no `[FAIL]` lines.

Example of a good result:

```
[ OK ] zulip package importable (version 0.9.1)
[ OK ] zuliprc found: ~/.zuliprc (permissions 0o600)
[ OK ] Credentials: bot email ai-bot@example.zulipchat.com on https://example.zulipchat.com
[ OK ] Authenticated as AI bot <ai-bot@example.zulipchat.com> (user_id 7, is_bot=True)
[ OK ] bot_owner_id=42, owner email as the bot sees it: user42@example.zulipchat.com
[ OK ] Effective owner: user42@example.zulipchat.com (from bot_owner_id)
[ OK ] ai binary: ~/ai-buddy/ai
All checks passed.
```

## 6. First run in the foreground

```bash
cd ~/ai-buddy
python3 zulip_ai_bridge.py
```

A healthy start prints:

```
Loaded credentials for: ai-bot@example.zulipchat.com on https://example.zulipchat.com/api/
Detected owner from bot_owner_id: user42@example.zulipchat.com
Allowed user: user42@example.zulipchat.com (from bot_owner_id)
File parser initialized — will extract content from uploaded documents.
Zulip chat memory manager initialized.
🚀 Starting Zulip AI Bridge listener (threaded — concurrent messages supported)...
```

If `ZULIP_USER` is set, the third line reads `Allowed user: 42 (from ZULIP_USER)`.

Now DM the bot in Zulip, for example `/ping`, which replies instantly without
running the model, and then a real question. For each message the console logs
`Privacy validation - ... -> ALLOWED=True`, then `Processing: ...`, then
`Done in N s (status: success)`. In Zulip you'll see a 💭 reaction while the
agent works, then ✅ (or ⚠️ if something went wrong), followed by the answer.
Long answers are split into up to four parts.

Stop the bridge with **Ctrl+C**.

### Built-in commands (in a DM, or after a mention in a channel)

| Command | Effect |
|---|---|
| `/help` | list commands |
| `/ping` | bridge status, mode, model, timeout |
| `/debug`, `/diag` | inference server health and recent errors |
| `/history`, `/chats` | recent conversations |
| `/mode auto\|plan\|manual` | permission mode for the agent (default `auto`) |
| `/timeout <seconds>` | default task timeout |
| `/wake`, `/restart-server` | start the local `llama-server` |
| `/long <task>` | run with the long timeout (default 2 h) |

`/mode` and `/timeout` last only until the bridge restarts. To make them
permanent, set `BRIDGE_AI_MODE` or `BRIDGE_TASK_TIMEOUT` in the env file.

> **Security:** the default mode is `auto`, which runs `ai --auto` with
> `INFER_AUTO_APPROVE=1`. In this mode the agent runs shell commands on this
> machine **without asking**. Anyone who holds the bot's API key, or can post
> as the owner, can make it run commands. Keep `~/.zuliprc` private, and use
> `BRIDGE_AI_MODE=plan` if you want less autonomy.

### DMs, group DMs and channels

- **DM to the bot:** always handled (owner only).
- **Group DM:** handled when the owner writes. The reply goes to the **whole
  group**.
- **Channels (streams):** the bot only sees channels it is **subscribed** to.
  Add it in the channel's settings under **Subscribers**. By default it reacts
  only to the owner's messages that **@-mention** it (`@**AI bot**`). The
  mention is stripped and the reply is posted in the same topic. To answer
  every owner message in subscribed channels without a mention, set
  `BRIDGE_REQUIRE_STREAM_MENTIONS=0`.
- **Context:** the previous 5 messages in the same DM or topic are added to
  the prompt, including messages from other people in that topic. The bot's
  own earlier replies are left out.
- **Attachments:** files you upload (links containing `/user_uploads/`) are
  downloaded with the bot's credentials, converted to text (up to 10 MB per
  file, 50,000 characters of text), and inserted into the prompt. Downloads are
  kept in `~/.cache/zulip_ai_uploads/` for 7 days.

## 7. Run it as a systemd user service

`./install.sh` copies `services/zulip-ai-bridge.service` to
`~/.config/systemd/user/`. On the way it fills in your checkout path and the
absolute path of the `python3` on your `PATH`, and it reloads systemd. It
**does not enable** the service. To install the unit by hand instead:

```bash
mkdir -p ~/.config/systemd/user
cp services/zulip-ai-bridge.service ~/.config/systemd/user/   # assumes the repo is at ~/ai-buddy
systemctl --user daemon-reload
```

Then enable and start it:

```bash
systemctl --user enable --now zulip-ai-bridge        # start now and at every boot/login
systemctl --user status zulip-ai-bridge              # is it running?
journalctl --user -u zulip-ai-bridge -f              # live logs (the same lines as the foreground run)
systemctl --user restart zulip-ai-bridge             # after editing the env file or zuliprc
systemctl --user disable --now zulip-ai-bridge       # stop and disable
```

**Headless servers and WSL:** user services normally stop when you log out.
Run this once so they keep running:

```bash
loginctl enable-linger "$USER"
```

On WSL, systemd must also be enabled in `/etc/wsl.conf` (`[boot]` then
`systemd=true`), followed by `wsl --shutdown` from Windows. WSL may still stop
the whole distribution when no Windows terminal is attached, which takes the
bridge down with it. Keep a terminal open, or configure WSL to stay running.

The unit restarts the bridge if it crashes, with an increasing delay from
10 s up to 5 min. To change the interpreter or other settings, use a drop-in
(`systemctl --user edit zulip-ai-bridge`) rather than editing the installed
file, because `./install.sh` overwrites it.

## 8. Reminders and the Zulip MCP tools

**Reminders.** For each message, the bridge sets
`AI_REMINDER_ZULIP_TO=<your email as the bot sees it>` in the agent's
environment. So "remind me tomorrow at 9 to call the lab" creates a reminder
with no further setup. At the scheduled time, the scheduler sends it
directly, with no model involved, as a DM **from the bot** through
`zulip_mcp_server.py`. That uses the credential order from section 2, so
`~/.zuliprc` is enough. If Zulip delivery fails, it falls back to a desktop
`notify-send`. You can also target a channel by asking for a stream and topic
(`zulip_stream` and `zulip_topic`; the topic defaults to "Reminders").

**MCP tools** (`zulip_send_message`, `zulip_get_messages`,
`zulip_add_reaction`, `zulip_edit_message`) are only available to the agent if
you register the server in an MCP config:

```json
{
  "mcpServers": {
    "zulip": {
      "command": "python3",
      "args": ["/home/you/.local/bin/zulip_mcp_server.py"]
    }
  }
}
```

Only the **first** config file that exists is used, in this order:
`./mcp.json`, `./mcp_config.json`, `~/.config/ai/mcp.json`,
`~/.config/ai/mcp_config.json`, and so on. The service runs inside the repo,
which ships its own `mcp.json`, so for the bridge the entry has to go into
the repo's `mcp.json`. For `ai` started elsewhere it goes into
`~/.config/ai/mcp.json`. The tools act **as the bot** with the same
credentials, and when no filter is given `zulip_get_messages` reads only
channels the bot is subscribed to and its DMs.

## 9. Environment variables

All of these can be set in `~/.local/share/ai/env` (as `export KEY="value"`)
or in the process environment, which takes priority.

| Variable | Default | Meaning |
|---|---|---|
| `ZULIP_USER` | *(auto: bot owner)* | The only user the bridge answers: numeric user id, email, or bare username. |
| `ZULIP_CONFIG` | `~/.zuliprc` | Path to the zuliprc (read by the zulip library, and so by the bridge and MCP server). |
| `ZULIP_EMAIL`, `ZULIP_API_KEY`, `ZULIP_SITE` | — | Credentials without a file (library-level; each overrides the matching zuliprc value). |
| `ZULIP_CERT_BUNDLE`, `ZULIP_ALLOW_INSECURE`, `ZULIP_CERT`, `ZULIP_CERT_KEY` | — | TLS options of the zulip library, for self-hosted servers. |
| `ZULIP_CONFIG_PATH` | — | MCP server only: an extra zuliprc path checked before `~/.config/zulip/zuliprc`. |
| `BRIDGE_AI_MODE` | `auto` | `auto`, `plan` or `manual`. Passed to `ai` as `--auto`, `--plan` or `--manual`. |
| `BRIDGE_REQUIRE_STREAM_MENTIONS` | `1` | `0`, `false` or `no` answers owner messages in channels without an @-mention. |
| `BRIDGE_TASK_TIMEOUT` (fallback `INFER_TASK_TIMEOUT`) | `1800` | Seconds before an agent run is killed, along with all its child processes. |
| `BRIDGE_LONG_TASK_TIMEOUT` (fallback `INFER_LONG_TASK_TIMEOUT`) | `7200` | Timeout for `/long`, `/deep` and `/job` messages, or ones containing phrases like "deep research". |
| `BRIDGE_SKIP_SERVER_PREFLIGHT` | unset | `1` skips the local inference-server health check and auto-wake. |
| `BRIDGE_MAX_CONTEXT_TOKENS` | `LLAMA_CTX_SIZE/4`, clamped to 4096–32768 | Token budget for the Zulip context messages. |
| `INFER_BIN_PATH` | auto | Explicit path to the `ai` binary. |
| `INFER_BASE_URL` | `http://localhost:8080/v1/` | Used for the health check. Auto-wake only runs for localhost URLs. |

The bridge also sets these for the agent it runs: `AI_REMINDER_ZULIP_TO`
(the sender), `INFER_RAW_OUTPUT=1`, and `INFER_AUTO_APPROVE=1` in `auto` mode.

## 10. Troubleshooting

Start with `python3 zulip_ai_bridge.py --check --online`, then read
`journalctl --user -u zulip-ai-bridge -n 100`.

| Symptom | Cause | Fix |
|---|---|---|
| The bot ignores your messages (no reaction at all) | Owner mismatch. The log shows `Privacy validation - allowed: 'X', sender: 'Y' -> ALLOWED=False`. Often you set your real email while the organization hides emails (the bot sees `user42@...`), or someone else created the bot. | Set `ZULIP_USER` to your **numeric user id** and restart. |
| The log says `⚠️ No owner configured or detected` | `ZULIP_USER` is unset, the bot has no resolvable owner, and several people have DMed it. | Set `ZULIP_USER`. |
| The bot ignores you in a channel but answers DMs | It isn't subscribed to the channel, or you didn't @-mention it. | Subscribe the bot, and mention `@**AI bot**`. Or set `BRIDGE_REQUIRE_STREAM_MENTIONS=0`. |
| `zulip package not importable` from `--check`, or `The 'zulip' Python package is required` at startup | The package is installed in a different Python. | `<interpreter shown by --check> -m pip install zulip`, then restart. |
| `api_key or email not specified and file ~/.zuliprc does not exist` | No credentials found. | Put the zuliprc at `~/.zuliprc`, or set `ZULIP_CONFIG`. |
| `Missing Zulip server URL` | The zuliprc has no `site=` line. | Add `site=https://example.zulipchat.com`. |
| `⚠️ get_profile failed: Invalid API key` at startup, after which nothing happens | Wrong or regenerated key, or a deactivated bot. With a bad key the listener keeps retrying silently. | Download the zuliprc again. |
| Messages sent while the PC slept or WSL was stopped never get answers | The Zulip event queue expired (`BAD_EVENT_QUEUE_ID`). The zulip library registers a new queue automatically, but events from the gap are not replayed. | Resend the message. Use `loginctl enable-linger` and keep WSL running to avoid gaps. |
| A reply says "Inference Server is Offline" | The local `llama-server` is down and auto-wake (up to about 45 s) failed. | `ai-backend status`, `journalctl --user -u llama-server`, or send `/wake`. |
| The ⏳ reaction stays for a long time | The model is loading into VRAM after auto-wake. | Wait. The first reply after an idle period is slower. |
| `⏱️ The agent timed out after 1800s` | The task took longer than `BRIDGE_TASK_TIMEOUT`. | Prefix the message with `/long`, raise it with `/timeout 3600`, or set `BRIDGE_TASK_TIMEOUT` in the env file. |
| `Agent returned no text output` | The model finished without producing text. | `/debug` and `/history`. Try `ai-backend reasoning medium`. |
| `⚠️ Failed to download: Download redirects to untrusted domain` | The server redirects `/user_uploads/` to external storage (for example S3) on another host. The bridge only follows downloads that stay on your Zulip host or its subdomains, so the bot's credentials never leave it. | By design. Paste the text, or attach a file the server serves directly. |
| `Failed to download: Download from untrusted domain` | The link points to another site. | Only Zulip uploads are fetched. Ask the agent to fetch other URLs explicitly. |
| `File exceeds size limit` | The upload is larger than 10 MB. | Send a smaller file or an excerpt. |
| `Cannot extract text from file.pdf` | No PDF or OCR library is installed. | `pip install pdfplumber`, or `pypdf`, or `pytesseract Pillow` for images. |
| `⚠️ Could not deliver reply: ...` | Zulip rejected the message: rate limit (retried automatically), a deleted channel, or similar. | Check the error text. Rate limits clear on their own. |
| Reminders come as a desktop notification instead of a Zulip DM | `zulip_mcp_server.py` couldn't send: `zulip` is missing in the scheduler's Python, or there are no credentials. | Install `zulip` in the first `python3` on `PATH`, and keep `~/.zuliprc`. |
| `systemctl --user` says "Failed to connect to bus" | systemd user session isn't running (common on WSL). | Enable systemd in `/etc/wsl.conf`, then run `wsl --shutdown` and reopen. |

## Where the bridge keeps data

| Path | Contents |
|---|---|
| `~/.local/share/ai/zulip_chats.jsonl`, `~/.cache/ai/zulip_chats.jsonl` | One summary line per conversation. |
| `~/.local/share/ai/zulip_chats/<id>.json` | Full record of each conversation: prompt, output, stderr. |
| `~/.cache/zulip_ai_uploads/` | Downloaded attachments, pruned after 7 days. |
