# Zulip Bridge Improvement Plan

## Overview
Systematic improvements to `zulip_ai_bridge.py` — one at a time, each with tests.

## Phase 1: Reliability & Resilience

### 1. Automatic Reconnection with Exponential Backoff
- **Why**: Bridge currently exits on any Zulip API error, leaving users without a bot.
- **How**: Wrap `run()` in a loop. On `ConnectionError`/`APIError`, sleep with backoff (1s, 2s, 4s, 8s... up to 60s max), then retry.
- **Test**: Mock `zulip.Client.call_on_each_message` to raise; verify retry loop runs multiple times.

### 2. Error Reporting Back to User
- **Why**: Currently some error paths silently drop messages or log only to stderr.
- **How**: Ensure every error in `_process_message` sends a feedback message back to the sender.
- **Test**: Mock `_send_reply` and verify it's called on timeout and subprocess failure.

### 3. Graceful Shutdown
- **Why**: Current bridge exits abruptly on Ctrl+C, potentially dropping in-flight responses.
- **How**: Register SIGINT/SIGTERM handlers that set a flag, stop the event loop, and log clean exit.
- **Test**: Send SIGTERM to a running bridge; verify it logs "Shutting down" and exits cleanly.

## Phase 2: Usability & Routing

### 4. Reply-Aware Threading
- **Why**: Context fetch is thread-aware but doesn't distinguish "reply to bot" vs "new question in same thread."
- **How**: Add explicit conversation ID tracking per thread. When a user replies to a bot message, continue that conversation context.
- **Test**: Simulate two messages in same thread; verify context window expands correctly.

### 5. Heartbeat / Liveness Check
- **Why**: Hard to know if the bot is alive without sending a test message.
- **How**: Add `/ping` command that returns "🟢 Zulip AI Bridge is alive." Also add periodic health check via DM.
- **Test**: Send `/ping`; verify response contains "alive" and no other processing.

### 6. Role-Based Access Control
- **Why**: Currently single-user model. Team environments need tiered access.
- **How**: Add config file support (`~/.config/ai/zulip_bridge.json`) mapping emails to roles (admin/user). Admins can manage bot; users can only query.
- **Test**: Verify unauthorized users are rejected; admins get special commands.

## Phase 3: Observability

### 7. Request Metrics & Logging
- **Why**: No visibility into usage, latency, or failure rates.
- **How**: Log request count, latency, token usage (if available), and errors. Optional metrics endpoint.
- **Test**: Verify log file contains expected entries after processing messages.

## Execution Order
1. Phase 1 items first (reliability is foundational)
2. Phase 2 next (usability)
3. Phase 3 last (observability)

Each phase: write code → write tests → run tests → commit.
