#!/usr/bin/env python3
"""
MCP server for Zulip chat integration.
Speaks JSON-RPC 2.0 over stdio (MCP transport).
"""

import sys
import os
import json
import traceback

try:
    import zulip
except ImportError:
    pass

TOOL_SEND_MESSAGE = {
    "name": "zulip_send_message",
    "description": "Send a message to a Zulip stream (channel) or private/direct message.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "message_type": {
                "type": "string",
                "enum": ["stream", "private"],
                "description": "The type of message: 'stream' for channels/streams, 'private' for direct/private messages."
            },
            "to": {
                "type": "string",
                "description": "For stream: the stream name (e.g. 'general'). For private: the recipient's email address or a comma-separated list of email addresses."
            },
            "topic": {
                "type": "string",
                "description": "The topic for the stream message (required if message_type is 'stream')."
            },
            "content": {
                "type": "string",
                "description": "The raw Markdown content of the message."
            }
        },
        "required": ["message_type", "to", "content"]
    }
}

TOOL_GET_MESSAGES = {
    "name": "zulip_get_messages",
    "description": "Retrieve recent messages from a Zulip stream (channel) or private messages.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "stream": {
                "type": "string",
                "description": "Filter by stream (channel) name. Optional."
            },
            "topic": {
                "type": "string",
                "description": "Filter by topic name. Optional. Highly recommended if filtering by stream."
            },
            "is_private": {
                "type": "boolean",
                "description": "If true, filter to private (direct) messages only. Optional."
            },
            "num_before": {
                "type": "integer",
                "description": "Number of messages to retrieve before the anchor (default 10, max 100).",
                "default": 10
            },
            "anchor": {
                "type": "string",
                "description": "Message ID anchor to start fetching from, or 'newest' / 'oldest' (default 'newest').",
                "default": "newest"
            }
        }
    }
}

def get_zulip_client():
    if "zulip" not in sys.modules:
        raise Exception("The 'zulip' Python package is not installed. Please run: pip install zulip")

    site = os.environ.get("ZULIP_SITE")
    email = os.environ.get("ZULIP_EMAIL")
    api_key = os.environ.get("ZULIP_API_KEY")

    if site and email and api_key:
        return zulip.Client(site=site, email=email, api_key=api_key)
    
    config_file = os.environ.get("ZULIP_CONFIG_PATH")
    if config_file:
        return zulip.Client(config_file=config_file)
        
    default_config_path = os.path.expanduser("~/.config/zulip/zuliprc")
    if os.path.exists(default_config_path):
        return zulip.Client(config_file=default_config_path)

    return zulip.Client()

def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def _send_error(req_id, code, message):
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})

def do_send_message(arguments):
    client = get_zulip_client()
    
    message_type = arguments.get("message_type")
    to = arguments.get("to")
    content = arguments.get("content")
    
    payload = {
        "type": message_type,
        "to": to,
        "content": content
    }
    
    if message_type == "stream":
        topic = arguments.get("topic")
        if not topic:
            return "Error: 'topic' is required when message_type is 'stream'"
        payload["topic"] = topic

    result = client.send_message(payload)
    if result.get("result") == "success":
        return f"Successfully sent message (ID: {result.get('id')}) to {to}."
    else:
        return f"Error sending message: {result.get('msg', 'Unknown error')}"

def do_get_messages(arguments):
    client = get_zulip_client()
    
    stream = arguments.get("stream")
    topic = arguments.get("topic")
    is_private = arguments.get("is_private")
    num_before = max(1, min(100, int(arguments.get("num_before", 10))))
    anchor = arguments.get("anchor", "newest")
    
    # If no filters are provided, default to checking the bot's subscribed streams and direct messages.
    # This prevents returning general public chat from the organization if the user didn't request it.
    if not stream and not topic and not is_private:
        subs_res = client.get_subscriptions()
        if subs_res.get("result") == "success":
            subscriptions = subs_res.get("subscriptions", [])
            lines = []
            
            # Fetch from subscribed streams
            for sub in subscriptions:
                sub_name = sub.get("name")
                payload = {
                    "anchor": anchor,
                    "num_before": num_before,
                    "num_after": 0,
                    "narrow": [{"operator": "stream", "operand": sub_name}],
                    "apply_markdown": False
                }
                res = client.get_messages(payload)
                if res.get("result") == "success":
                    msgs = res.get("messages", [])
                    if msgs:
                        lines.append(f"=== Channel: {sub_name} ===")
                        for msg in msgs:
                            msg_id = msg.get("id")
                            sender = msg.get("sender_full_name", "Unknown")
                            email = msg.get("sender_email", "")
                            timestamp = msg.get("timestamp", 0)
                            import datetime
                            dt = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                            lines.append(f"[{msg_id}] {dt} - {sender} ({email}):")
                            lines.append(msg.get("content", ""))
                            lines.append("-" * 40)
            
            # Fetch direct/private messages (DMs)
            dm_payload = {
                "anchor": anchor,
                "num_before": num_before,
                "num_after": 0,
                "narrow": [{"operator": "is", "operand": "private"}],
                "apply_markdown": False
            }
            dm_res = client.get_messages(dm_payload)
            if dm_res.get("result") == "success":
                dm_msgs = dm_res.get("messages", [])
                if dm_msgs:
                    lines.append("=== Direct Messages (Private) ===")
                    for msg in dm_msgs:
                        msg_id = msg.get("id")
                        sender = msg.get("sender_full_name", "Unknown")
                        email = msg.get("sender_email", "")
                        timestamp = msg.get("timestamp", 0)
                        import datetime
                        dt = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                        lines.append(f"[{msg_id}] {dt} - {sender} ({email}):")
                        lines.append(msg.get("content", ""))
                        lines.append("-" * 40)
            
            if lines:
                return "\n".join(lines)
            else:
                return "No messages found in your subscribed channels or private messages."

    # Otherwise, query using user-specified filters
    narrow = []
    if stream:
        narrow.append({"operator": "stream", "operand": stream})
    if topic:
        narrow.append({"operator": "topic", "operand": topic})
    if is_private:
        narrow.append({"operator": "is", "operand": "private"})
        
    payload = {
        "anchor": anchor,
        "num_before": num_before,
        "num_after": 0,
        "narrow": narrow,
        "apply_markdown": False
    }
    
    result = client.get_messages(payload)
    if result.get("result") != "success":
        return f"Error getting messages: {result.get('msg', 'Unknown error')}"
        
    messages = result.get("messages", [])
    if not messages:
        return "No messages found matching the filter."
        
    lines = []
    for msg in messages:
        msg_id = msg.get("id")
        sender = msg.get("sender_full_name", "Unknown")
        email = msg.get("sender_email", "")
        timestamp = msg.get("timestamp", 0)
        import datetime
        dt = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        
        lines.append(f"[{msg_id}] {dt} - {sender} ({email}):")
        lines.append(msg.get("content", ""))
        lines.append("-" * 40)
        
    return "\n".join(lines)

def main():
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        req_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if req_id is None:
            continue

        try:
            if method == "initialize":
                _send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "zulip-mcp-server", "version": "1.0.0"}
                    }
                })

            elif method == "tools/list":
                _send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"tools": [TOOL_SEND_MESSAGE, TOOL_GET_MESSAGES]}
                })

            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})

                if tool_name == "zulip_send_message":
                    result = do_send_message(arguments)
                elif tool_name == "zulip_get_messages":
                    result = do_get_messages(arguments)
                else:
                    _send_error(req_id, -32601, f"Unknown tool: {tool_name}")
                    continue

                _send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": result}],
                        "isError": result.startswith("Error:") or result.startswith("⚠️")
                    }
                })

            else:
                _send_error(req_id, -32601, f"Method not found: {method}")

        except Exception as e:
            err_msg = f"Exception in MCP handler: {str(e)}\n{traceback.format_exc()}"
            _send_error(req_id, -32603, err_msg)

if __name__ == "__main__":
    main()
