"""Tests for ContextWindowManager and FileParser in the Zulip AI bridge."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zulip_ai_bridge import ContextWindowManager, FileParser


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Keep tests from reading/writing the real ~/.local/share/ai and ~/.cache."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ZULIP_USER", raising=False)
    return home


def _join_workers(bridge, timeout=5):
    for t in list(getattr(bridge, "_workers", ())):
        t.join(timeout)


# ---------------------------------------------------------------------------
# ContextWindowManager
# ---------------------------------------------------------------------------

@pytest.fixture
def window_manager():
    return ContextWindowManager(max_tokens=4096, max_messages=10)


def test_empty_context(window_manager):
    """Empty context should be returned unchanged."""
    messages, truncated = window_manager.truncate_context([], "Hello")
    assert messages == []
    assert not truncated


def test_single_message(window_manager):
    """A single short message should not be truncated."""
    messages = [{"sender_full_name": "Alice", "sender_email": "alice@example.com", "content": "Hello"}]
    result, truncated = window_manager.truncate_context(messages, "Short query")
    assert len(result) == 1
    assert not truncated


def test_truncation_at_budget(window_manager):
    """Context should be truncated when the token budget is exceeded."""
    # Build messages that exceed the 4096-token budget (16384 chars).
    long_message = "x" * 4000
    messages = [{"sender_full_name": f"User{i}", "sender_email": f"user{i}@example.com", "content": long_message} for i in range(5)]
    result, truncated = window_manager.truncate_context(messages, "Query")
    # Should be truncated
    assert truncated
    assert len(result) < 5


def test_truncation_keeps_recent(window_manager):
    """When truncating, the most recent messages should be retained."""
    short = "short"
    messages = [{"sender_full_name": f"User{i}", "sender_email": f"user{i}@example.com", "content": short} for i in range(5)]
    # Force truncation by setting a small token budget that fits only ~1 message.
    # Each formatted message is ~15 chars; budget = 5 * 4 = 20 chars.
    small_wm = ContextWindowManager(max_tokens=5, max_messages=10)
    result, truncated = small_wm.truncate_context(messages, "")
    assert truncated
    # Most recent message (User4) should be kept.
    assert result[-1]["sender_full_name"] == "User4"
    assert len(result) == 1


def test_truncation_respects_message_cap(window_manager):
    """Truncation should also trigger when the message count cap is hit."""
    messages = [{"sender_full_name": f"User{i}", "sender_email": f"user{i}@example.com", "content": "Hi"} for i in range(5)]
    # Set a message cap of 2.
    cap_wm = ContextWindowManager(max_tokens=100000, max_messages=2)
    result, truncated = cap_wm.truncate_context(messages, "Query")
    assert truncated
    assert len(result) == 2
    # Most recent two messages should be kept.
    assert result[-1]["sender_full_name"] == "User4"
    assert result[0]["sender_full_name"] == "User3"


def test_estimate_tokens():
    """Token estimation should scale with text length."""
    wm = ContextWindowManager()
    short = "Hi"
    long = "Hello, this is a much longer string of text to estimate"
    assert wm.estimate_tokens(short) < wm.estimate_tokens(long)


def test_format_context_with_bot_messages():
    """Format should label bot messages as 'AI (You)'."""
    wm = ContextWindowManager()
    bot_email = "bot@example.com"
    messages = [
        {"sender_full_name": "Bot", "sender_email": bot_email, "content": "I can help with that."},
        {"sender_full_name": "Alice", "sender_email": "alice@example.com", "content": "Thanks!"},
    ]
    formatted = wm.format_context(messages, bot_email)
    assert "AI (You)" in formatted
    assert "User (Alice)" in formatted


def test_format_context_empty():
    """Empty context should return empty string."""
    wm = ContextWindowManager()
    assert wm.format_context([], "bot@example.com") == ""


def test_format_multiline_content():
    """Multiline content should be indented."""
    wm = ContextWindowManager()
    messages = [{"sender_full_name": "Alice", "sender_email": "alice@example.com", "content": "Line 1\nLine 2"}]
    formatted = wm.format_context(messages, "bot@example.com")
    assert "  Line 1" in formatted
    assert "  Line 2" in formatted


# ---------------------------------------------------------------------------
# FileParser._is_trusted_url
# ---------------------------------------------------------------------------

class MockClient:
    def __init__(self, base_url):
        self.base_url = base_url


@pytest.fixture
def file_parser():
    client = MockClient("https://example.zulipchat.com")
    return FileParser(client, "https://example.zulipchat.com")


def test_is_trusted_url_exact(file_parser):
    """Exact domain match should be trusted."""
    assert file_parser._is_trusted_url(
        "https://example.zulipchat.com/uploads/..."
    ) is True


def test_is_trusted_url_subdomain(file_parser):
    """Subdomain of allowed domain should be trusted."""
    assert file_parser._is_trusted_url(
        "https://sub.example.zulipchat.com/uploads/..."
    ) is True


def test_is_trusted_url_different_domain(file_parser):
    """Different domain should be blocked."""
    assert file_parser._is_trusted_url(
        "https://evil.com/uploads/..."
    ) is False


def test_is_trusted_url_https_variants(file_parser):
    """HTTP and HTTPS should be handled consistently."""
    assert file_parser._is_trusted_url(
        "http://example.zulipchat.com/uploads/..."
    ) is True


def test_is_trusted_url_path_only(file_parser):
    """Relative path should be trusted (same origin)."""
    assert file_parser._is_trusted_url(
        "/uploads/..."
    ) is True


# ---------------------------------------------------------------------------
# ZulipAiBridge._manage_context_window
# ---------------------------------------------------------------------------

class MockZulipClient:
    def __init__(self):
        self.base_url = "https://example.zulipchat.com"
        self.email = "bot@example.zulipchat.com"
        self.sent_messages = []

    def get_me(self):
        class Response:
            json = {"email": "bot@example.zulipchat.com"}
        return Response()

    def get_messages(self, payload):
        return {"result": "success", "messages": []}

    def send_message(self, payload):
        self.sent_messages.append(payload)
        return {"result": "success", "id": len(self.sent_messages)}


def test_manage_context_window_filters_bot_messages():
    """Bot's own messages should be excluded from context."""
    from zulip_ai_bridge import ZulipAiBridge
    client = MockZulipClient()
    bridge = ZulipAiBridge(client=client)

    context_messages = [
        {"id": 1, "sender_email": "user1@example.com", "content": "Hi"},
        {"id": 2, "sender_email": "bot@example.zulipchat.com", "content": "Hello! How can I help?"},
        {"id": 3, "sender_email": "user2@example.com", "content": "Need help with X."},
    ]
    result = bridge._manage_context_window(context_messages, "Help me with X")
    assert len(result) == 2
    assert all(m["sender_email"] != "bot@example.zulipchat.com" for m in result)


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ZulipAiBridge trust / redirect tests
# ---------------------------------------------------------------------------

def test_is_trusted_url_exact():
    """Exact domain match should be trusted."""
    from zulip_ai_bridge import ZulipAiBridge
    bridge = ZulipAiBridge(client=MockZulipClient())
    assert bridge._is_trusted_url("https://example.zulipchat.com") is True


def test_is_trusted_url_subdomain():
    """Subdomain of the configured domain should be trusted."""
    from zulip_ai_bridge import ZulipAiBridge
    bridge = ZulipAiBridge(client=MockZulipClient())
    assert bridge._is_trusted_url("https://sub.example.zulipchat.com") is True


def test_is_trusted_url_untrusted():
    """Different domain should be rejected."""
    from zulip_ai_bridge import ZulipAiBridge
    bridge = ZulipAiBridge(client=MockZulipClient())
    assert bridge._is_trusted_url("https://evil.com") is False


def test_is_trusted_redirect_trusted_target():
    """Redirect to a trusted final URL should pass."""
    from zulip_ai_bridge import ZulipAiBridge
    bridge = ZulipAiBridge(client=MockZulipClient())
    assert bridge._is_trusted_redirect("https://example.zulipchat.com/file", "https://example.zulipchat.com/old") is True


def test_is_trusted_redirect_untrusted_target():
    """Redirect to an untrusted final URL should be rejected."""
    from zulip_ai_bridge import ZulipAiBridge
    bridge = ZulipAiBridge(client=MockZulipClient())
    assert bridge._is_trusted_redirect("https://evil.com/steal", "https://example.zulipchat.com/redirect") is False


def test_download_file_blocks_untrusted_redirect():
    """_download_file should refuse to download from an untrusted redirect target."""
    from unittest.mock import patch
    from zulip_ai_bridge import ZulipAiBridge

    class FakeResponse:
        url = "https://evil.com/steal"
        def raise_for_status(self):
            pass
        def iter_content(self, chunk_size=8192):
            return iter([b"malicious content"])

    bridge = ZulipAiBridge(client=MockZulipClient())
    with patch("zulip_ai_bridge.requests.get", return_value=FakeResponse()):
        success, err = bridge._download_file("https://example.zulipchat.com/original", "/tmp/malicious.bin")
    assert success is False
    assert "untrusted" in err


def test_download_file_allows_trusted_redirect():
    """_download_file should follow a redirect to a trusted target."""
    from unittest.mock import patch
    from zulip_ai_bridge import ZulipAiBridge

    class FakeResponse:
        url = "https://example.zulipchat.com/redirected"
        headers = {}
        def raise_for_status(self):
            pass
        def iter_content(self, chunk_size=8192):
            return iter([b"trusted content"])

    bridge = ZulipAiBridge(client=MockZulipClient())
    with patch("zulip_ai_bridge.requests.get", return_value=FakeResponse()):
        success, err = bridge._download_file("https://example.zulipchat.com/original", "/tmp/trusted.bin")
    assert success is True
    assert err is None


# ---------------------------------------------------------------------------
# ZulipAiBridge._ai_mode / _truncate_reply (mode + delivery behaviour)
# ---------------------------------------------------------------------------

class _ModeClient(MockZulipClient):
    def send_message(self, payload):
        self.last_sent = payload
        return {"result": "success", "id": 1}


def test_ai_mode_defaults_to_auto(monkeypatch):
    """Without BRIDGE_AI_MODE set, the bridge should default to auto mode."""
    from zulip_ai_bridge import ZulipAiBridge
    monkeypatch.delenv("BRIDGE_AI_MODE", raising=False)
    bridge = ZulipAiBridge(client=MockZulipClient())
    assert bridge._ai_mode() == "auto"


def test_ai_mode_reads_env(monkeypatch):
    from zulip_ai_bridge import ZulipAiBridge
    monkeypatch.setenv("BRIDGE_AI_MODE", "plan")
    bridge = ZulipAiBridge(client=MockZulipClient())
    assert bridge._ai_mode() == "plan"


def test_truncate_reply_short_untouched():
    from zulip_ai_bridge import ZulipAiBridge
    bridge = ZulipAiBridge(client=MockZulipClient())
    text = "short reply"
    assert bridge._truncate_reply(text) == text


def test_truncate_reply_long_cut():
    from zulip_ai_bridge import ZulipAiBridge
    bridge = ZulipAiBridge(client=MockZulipClient())
    text = "x" * 20000
    out = bridge._truncate_reply(text, max_chars=9000)
    assert len(out) < len(text)
    assert "truncated" in out


def test_process_message_uses_auto_flags(monkeypatch):
    """In auto mode, the subprocess should be invoked with --auto and
    INFER_AUTO_APPROVE set, and send an empty reply only when ai returns nothing."""
    from unittest.mock import patch
    from zulip_ai_bridge import ZulipAiBridge

    monkeypatch.delenv("BRIDGE_AI_MODE", raising=False)
    client = _ModeClient()
    bridge = ZulipAiBridge(client=client)

    msg = {
        "type": "private",
        "sender_email": "owner@example.com",
        "content": "do something",
    }

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env_auto"] = kwargs.get("env", {}).get("INFER_AUTO_APPROVE")
        class R:
            returncode = 0
            stdout = "done"
            stderr = ""
        return R()

    with patch("zulip_ai_bridge.run_ai_subprocess", side_effect=fake_run):
        bridge._process_message(msg, "do something")

    assert any(f == "--auto" for f in captured["cmd"])
    assert captured["env_auto"] == "1"
    assert "done" in client.last_sent["content"]


def test_long_job_detection_and_flags():
    """Long jobs should be detected, send confirmation, set extended timeout, and pass -c."""
    from unittest.mock import patch
    from zulip_ai_bridge import ZulipAiBridge

    class _CaptureClient(MockZulipClient):
        def __init__(self):
            super().__init__()
            self.sent_messages = []
            self.reactions = []

        def send_message(self, payload):
            self.sent_messages.append(payload)
            return {"result": "success", "id": len(self.sent_messages)}

        def add_reaction(self, payload):
            self.reactions.append(payload)
            return {"result": "success"}

    client = _CaptureClient()
    bridge = ZulipAiBridge(client=client)

    msg = {
        "id": 42,
        "type": "private",
        "sender_email": "owner@example.com",
        "content": "/long run deep research on kinase inhibitors",
    }

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = kwargs.get("timeout")
        class R:
            returncode = 0
            stdout = "Research completed successfully."
            stderr = ""
        return R()

    with patch("zulip_ai_bridge.run_ai_subprocess", side_effect=fake_run):
        bridge._process_message(msg, msg["content"])

    assert bridge._is_long_job(msg["content"])
    assert "-c" in captured["cmd"]
    assert captured["timeout"] >= 3600
    # Confirmation message was sent
    assert any("Task confirmed in extended execution mode" in m["content"] for m in client.sent_messages)
    # Final result was sent
    assert any("Research completed successfully." in m["content"] for m in client.sent_messages)


def test_send_full_reply_multipart():
    """Large replies (>8500 chars) should be split across multiple parts."""
    from zulip_ai_bridge import ZulipAiBridge

    class _MultiClient(MockZulipClient):
        def __init__(self):
            super().__init__()
            self.sent = []

        def send_message(self, payload):
            self.sent.append(payload)
            return {"result": "success", "id": len(self.sent)}

    client = _MultiClient()
    bridge = ZulipAiBridge(client=client)

    msg = {"type": "private", "sender_email": "owner@example.com"}
    long_text = ("Paragraph of data.\n\n" * 600)  # ~12,000 chars

    bridge._send_full_reply(msg, long_text)
    assert len(client.sent) >= 2
    assert "Part 1" in client.sent[0]["content"]
    assert "Part 2" in client.sent[1]["content"]


# ---------------------------------------------------------------------------
# ZulipMemoryManager & Fallback Extraction Tests
# ---------------------------------------------------------------------------

def test_zulip_memory_manager(tmp_path):
    """Test recording and retrieving Zulip chat records from memory."""
    from zulip_ai_bridge import ZulipMemoryManager

    storage_dir = str(tmp_path / "zulip_chats")
    mem = ZulipMemoryManager(storage_dir=storage_dir)

    chat_record = {
        "session_id": "test_zlp_001",
        "sender_email": "owner@example.com",
        "query": "What's going on with the workstation?",
        "status": "success",
        "duration_s": 1.25,
        "response_delivered": "System load is normal.",
        "ai_mode": "auto"
    }

    sid = mem.record_chat(chat_record)
    assert sid == "test_zlp_001"

    # Verify retrieval from JSONL
    recent = mem.get_recent(limit=5)
    assert len(recent) == 1
    assert recent[0]["session_id"] == "test_zlp_001"
    assert recent[0]["query"] == "What's going on with the workstation?"

    # Verify retrieval of detailed session
    detail = mem.get_chat("test_zlp_001")
    assert detail is not None
    assert detail["response_delivered"] == "System load is normal."


def test_extract_session_fallback_from_reasoning(tmp_path):
    """Fallback extraction should extract reasoning_content when content is null."""
    import json
    from zulip_ai_bridge import extract_session_fallback

    sess_file = tmp_path / "last.json"
    data = {
        "messages": [
            {"role": "user", "content": "Run ai-backend settings"},
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "The selected settings are: Qwen3.8-27B model, ctx 80000, MTP enabled."
            }
        ]
    }
    sess_file.write_text(json.dumps(data), encoding="utf-8")

    text, status, snip = extract_session_fallback(last_sess_path=str(sess_file), raw_stdout="")
    assert status == "fallback_reasoning"
    assert "The selected settings are: Qwen3.8-27B" in text
    assert "Response extracted from agent reasoning" in text


def test_extract_session_fallback_from_tools(tmp_path):
    """Fallback extraction should summarize tool actions when assistant executed tools."""
    import json
    from zulip_ai_bridge import extract_session_fallback

    sess_file = tmp_path / "last.json"
    data = {
        "messages": [
            {"role": "user", "content": "check gpu"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"function": {"name": "execute_command", "arguments": '{"command": "nvidia-smi"}'}}]
            },
            {
                "role": "tool",
                "name": "execute_command",
                "content": "NVIDIA RTX 5080, 54C, 227W"
            }
        ]
    }
    sess_file.write_text(json.dumps(data), encoding="utf-8")

    text, status, _ = extract_session_fallback(last_sess_path=str(sess_file), raw_stdout="")
    assert status == "fallback_tools"
    assert "execute_command" in text
    assert "RTX 5080" in text


def test_slash_commands_in_bridge(tmp_path):
    """Test /ping, /history, and /debug commands."""
    from unittest.mock import patch
    from zulip_ai_bridge import ZulipAiBridge, ZulipMemoryManager

    client = MockZulipClient()
    bridge = ZulipAiBridge(client=client)
    bridge.detected_owner = "owner@example.com"
    bridge.memory = ZulipMemoryManager(storage_dir=str(tmp_path / "zulip_chats"))

    # Seed one record
    bridge.memory.record_chat({
        "session_id": "seed_01",
        "sender_email": "owner@example.com",
        "query": "hello world",
        "status": "success",
        "duration_s": 0.5,
        "response_delivered": "Hello!"
    })

    # Test /ping
    bridge.handle_message({"type": "private", "sender_email": "owner@example.com", "content": "/ping"})
    assert any("Zulip AI Bridge is active" in m["content"] for m in client.sent_messages)

    # Test /history
    bridge.handle_message({"type": "private", "sender_email": "owner@example.com", "content": "/history"})
    assert any("Recent Zulip Chat Memory" in m["content"] for m in client.sent_messages)

    # Test /debug
    bridge.handle_message({"type": "private", "sender_email": "owner@example.com", "content": "/debug"})
    assert any("Zulip AI Bridge Diagnostics" in m["content"] for m in client.sent_messages)


def test_session_fallback_on_empty_stdout(tmp_path):
    """When subprocess stdout is empty, fallback should extract response from last.json session."""
    import json
    from unittest.mock import patch
    from zulip_ai_bridge import ZulipAiBridge

    class _CaptureClient(MockZulipClient):
        def __init__(self):
            super().__init__()
            self.sent_messages = []

        def send_message(self, payload):
            self.sent_messages.append(payload)
            return {"result": "success", "id": len(self.sent_messages)}

    client = _CaptureClient()
    bridge = ZulipAiBridge(client=client)

    sess_dir = tmp_path / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    last_json = sess_dir / "last.json"

    msg = {
        "id": 55,
        "type": "private",
        "sender_email": "owner@example.com",
        "content": "hello",
    }

    def fake_run(cmd, **kwargs):
        # The agent writes its session file while it runs.
        last_json.write_text(json.dumps({
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "Hello from cached session!"}
            ]
        }))
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    with patch("zulip_ai_bridge.run_ai_subprocess", side_effect=fake_run), \
         patch("os.path.expanduser", side_effect=lambda p: str(last_json) if "last.json" in p else p):
        bridge._process_message(msg, msg["content"])

    assert any("Hello from cached session!" in m["content"] for m in client.sent_messages)


def test_error_reporting_on_failed_agent():
    """When subprocess exits with non-zero error, the error should be surfaced clearly."""
    from unittest.mock import patch
    from zulip_ai_bridge import ZulipAiBridge

    class _CaptureClient(MockZulipClient):
        def __init__(self):
            super().__init__()
            self.sent_messages = []

        def send_message(self, payload):
            self.sent_messages.append(payload)
            return {"result": "success", "id": len(self.sent_messages)}

    client = _CaptureClient()
    bridge = ZulipAiBridge(client=client)

    msg = {
        "id": 56,
        "type": "private",
        "sender_email": "owner@example.com",
        "content": "test error",
    }

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 2
            stdout = ""
            stderr = "CUDA out of memory"
        return R()

    with patch("zulip_ai_bridge.run_ai_subprocess", side_effect=fake_run), \
         patch("os.path.isfile", return_value=False):
        bridge._process_message(msg, msg["content"])

    assert any("CUDA out of memory" in m["content"] or "exit code 2" in m["content"] for m in client.sent_messages)


# ---------------------------------------------------------------------------
# Stream Mention Filtering & UX Tests
# ---------------------------------------------------------------------------

def test_stream_message_without_mention_is_ignored(monkeypatch):
    """In shared streams/channels, messages without bot mention must be ignored."""
    from unittest.mock import patch
    from zulip_ai_bridge import ZulipAiBridge

    monkeypatch.setenv("ZULIP_USER", "owner@example.com")
    client = MockZulipClient()
    bridge = ZulipAiBridge(client=client)

    msg = {
        "id": 101,
        "type": "stream",
        "display_recipient": "general",
        "subject": "random topic",
        "sender_email": "owner@example.com",
        "content": "Computational design of binders vs NiV: check metrics",
    }

    with patch.object(bridge, "_process_message") as mock_proc:
        bridge.handle_message(msg)
        mock_proc.assert_not_called()
    assert len(client.sent_messages) == 0


def test_stream_message_with_mention_is_processed(monkeypatch):
    """In shared streams, messages explicitly mentioning the bot should be processed with mention stripped."""
    from unittest.mock import patch
    from zulip_ai_bridge import ZulipAiBridge

    monkeypatch.setenv("ZULIP_USER", "owner@example.com")
    client = MockZulipClient()
    bridge = ZulipAiBridge(client=client)
    bridge.bot_name = "AI bot"

    msg = {
        "id": 102,
        "type": "stream",
        "display_recipient": "general",
        "subject": "random topic",
        "sender_email": "owner@example.com",
        "content": "@**AI bot** summarize this paper for me",
    }

    processed_content = []

    def fake_process(msg, content):
        processed_content.append(content)

    with patch.object(bridge, "_process_message", side_effect=fake_process):
        bridge.handle_message(msg)
        _join_workers(bridge)

    assert len(processed_content) == 1
    assert processed_content[0] == "summarize this paper for me"


def test_stream_message_with_user_id_mention(monkeypatch):
    """Silent or user-id-qualified mentions like @_**AI bot|4242** should be recognized."""
    from unittest.mock import patch
    from zulip_ai_bridge import ZulipAiBridge

    monkeypatch.setenv("ZULIP_USER", "owner@example.com")
    client = MockZulipClient()
    bridge = ZulipAiBridge(client=client)
    bridge.bot_user_id = 4242
    bridge.bot_name = "AI bot"

    msg = {
        "id": 103,
        "type": "stream",
        "display_recipient": "research",
        "subject": "binding",
        "sender_email": "owner@example.com",
        "content": "@_**AI bot|4242**: what is the binding affinity?",
    }

    processed_content = []

    def fake_process(msg, content):
        processed_content.append(content)

    with patch.object(bridge, "_process_message", side_effect=fake_process):
        bridge.handle_message(msg)
        _join_workers(bridge)

    assert len(processed_content) == 1
    assert processed_content[0] == "what is the binding affinity?"


def test_private_message_processed_without_mention(monkeypatch):
    """In private 1-on-1 chats, messages should be processed directly without needing a mention."""
    from unittest.mock import patch
    from zulip_ai_bridge import ZulipAiBridge

    monkeypatch.setenv("ZULIP_USER", "owner@example.com")
    client = MockZulipClient()
    bridge = ZulipAiBridge(client=client)

    msg = {
        "id": 104,
        "type": "private",
        "sender_email": "owner@example.com",
        "content": "hello there",
    }

    processed_content = []

    def fake_process(msg, content):
        processed_content.append(content)

    with patch.object(bridge, "_process_message", side_effect=fake_process):
        bridge.handle_message(msg)
        _join_workers(bridge)

    assert len(processed_content) == 1
    assert processed_content[0] == "hello there"


def test_help_command(monkeypatch):
    """The /help command should return the available commands list."""
    from zulip_ai_bridge import ZulipAiBridge

    monkeypatch.setenv("ZULIP_USER", "owner@example.com")
    client = MockZulipClient()
    bridge = ZulipAiBridge(client=client)

    msg = {
        "id": 105,
        "type": "private",
        "sender_email": "owner@example.com",
        "content": "/help",
    }

    bridge.handle_message(msg)
    assert len(client.sent_messages) == 1
    reply = client.sent_messages[0]["content"]
    assert "Zulip AI Bridge Commands" in reply
    assert "/ping" in reply
    assert "/mode" in reply
    assert "/wake" in reply


def test_process_message_urls_relative_and_markdown(tmp_path):
    """process_message_urls should parse relative and markdown upload links."""
    from unittest.mock import patch
    from zulip_ai_bridge import FileParser

    client = MockClient("https://example.zulipchat.com")
    parser = FileParser(client, "https://example.zulipchat.com")

    # Create dummy parsed file
    test_file = tmp_path / "sample.txt"
    test_file.write_text("Hello from uploaded document!")

    content = "Please analyze [sample.txt](/user_uploads/12345/sample.txt) now."

    with patch.object(parser, "_download_file", return_value=(True, None)), \
         patch.object(parser, "parse_file", return_value="Hello from uploaded document!"):
        new_content, processed = parser.process_message_urls(content)

    assert "sample.txt" in processed
    assert "```[File: sample.txt]" in new_content
    assert "Hello from uploaded document!" in new_content






# ---------------------------------------------------------------------------
# Robustness / security regression tests
# ---------------------------------------------------------------------------

def _bridge(client=None):
    from zulip_ai_bridge import ZulipAiBridge
    return ZulipAiBridge(client=client or MockZulipClient())


def test_owner_check_rejects_display_name_spoof(monkeypatch):
    """A sender whose *display name* equals the owner's email must not pass."""
    bridge = _bridge()
    bridge.detected_owner = "owner@example.com"
    msg = {"id": 1, "type": "private", "sender_email": "attacker@example.org",
           "sender_full_name": "owner@example.com", "content": "hi"}
    with patch_process(bridge) as calls:
        bridge.handle_message(msg)
        _join_workers(bridge)
    assert calls == []


def test_owner_check_rejects_user_prefix_spoof(monkeypatch):
    """ZULIP_USER=bob must not authorize userbob@..."""
    monkeypatch.setenv("ZULIP_USER", "bob")
    bridge = _bridge()
    msg = {"id": 1, "type": "private", "sender_email": "userbob@example.org", "content": "hi"}
    with patch_process(bridge) as calls:
        bridge.handle_message(msg)
        _join_workers(bridge)
    assert calls == []


def test_owner_check_numeric_id_matches_sender_id(monkeypatch):
    monkeypatch.setenv("ZULIP_USER", "42")
    bridge = _bridge()
    msg = {"id": 1, "type": "private", "sender_email": "someone@example.com",
           "sender_id": 42, "content": "hi"}
    with patch_process(bridge) as calls:
        bridge.handle_message(msg)
        _join_workers(bridge)
    assert calls == ["hi"]


def test_no_owner_configured_rejects_everyone(monkeypatch):
    """With no ZULIP_USER and no detectable owner, nobody is authorized
    (previously fell back to the local unix username, matching any email
    with that local part)."""
    import getpass
    bridge = _bridge()
    bridge.detected_owner = None
    msg = {"id": 1, "type": "private",
           "sender_email": f"{getpass.getuser()}@example.org", "content": "hi"}
    with patch_process(bridge) as calls:
        bridge.handle_message(msg)
        _join_workers(bridge)
    assert calls == []


class _OwnerClient(MockZulipClient):
    def __init__(self, pm_senders, bot_owner_id=None):
        super().__init__()
        self.pm_senders = pm_senders
        self.bot_owner_id = bot_owner_id

    def get_profile(self):
        return {"result": "success", "user_id": 7, "full_name": "AI bot",
                "email": self.email}

    def get_user_by_id(self, user_id, **kw):
        if user_id == 7:
            u = {"user_id": 7, "email": self.email, "is_bot": True}
            if self.bot_owner_id:
                u["bot_owner_id"] = self.bot_owner_id
            return {"result": "success", "user": u}
        if user_id == 3:
            return {"result": "success", "user": {"user_id": 3, "email": "owner@example.com"}}
        return {"result": "error", "msg": "no such user"}

    def get_messages(self, payload):
        return {"result": "success",
                "messages": [{"id": i, "sender_email": e} for i, e in enumerate(self.pm_senders)]}


def test_detect_owner_prefers_bot_owner_id():
    bridge = _bridge(_OwnerClient(["attacker@example.org"], bot_owner_id=3))
    assert bridge.detected_owner == "owner@example.com"


def test_detect_owner_ambiguous_pm_history_is_rejected():
    bridge = _bridge(_OwnerClient(["owner@example.com", "attacker@example.org"]))
    assert bridge.detected_owner is None


def test_detect_owner_unambiguous_pm_history():
    bridge = _bridge(_OwnerClient(["owner@example.com", "owner@example.com"]))
    assert bridge.detected_owner == "owner@example.com"


import contextlib


@contextlib.contextmanager
def patch_process(bridge):
    from unittest.mock import patch
    calls = []
    with patch.object(bridge, "_process_message", side_effect=lambda m, c: calls.append(c)):
        yield calls


def test_handle_message_never_raises_on_malformed_event():
    """A malformed event must not propagate out of the listener callback
    (it would tear down call_on_each_message and drop the event queue)."""
    bridge = _bridge()
    bridge._safe_handle_message({"bogus": True})  # no sender_email/content


def test_run_survives_callback_exception():
    from unittest.mock import patch
    bridge = _bridge()
    seen = []

    def fake_handle(m):
        if m.get("boom"):
            raise RuntimeError("boom")
        seen.append(m)

    def fake_call_on_each_message(cb):
        cb({"boom": True})
        cb({"ok": True})
        raise KeyboardInterrupt

    bridge.client.call_on_each_message = fake_call_on_each_message
    with patch.object(bridge, "handle_message", side_effect=fake_handle):
        with pytest.raises(KeyboardInterrupt):
            bridge.run()
    assert seen == [{"ok": True}]


def test_file_downloads_happen_off_listener_thread(monkeypatch):
    """Uploads are downloaded/parsed in the worker, not the listener callback."""
    from unittest.mock import patch
    monkeypatch.setenv("ZULIP_USER", "owner@example.com")
    bridge = _bridge()
    started = []

    class FakeThread:
        def __init__(self, target, args=(), daemon=None):
            started.append((target, args))
        def start(self):
            pass

    with patch.object(bridge._file_parser, "process_message_urls",
                      side_effect=AssertionError("ran on listener thread")), \
         patch("zulip_ai_bridge.threading.Thread", FakeThread):
        bridge.handle_message({"id": 1, "type": "private", "sender_email": "owner@example.com",
                               "content": "see /user_uploads/1/a.txt"})
    assert len(started) == 1


def test_worker_exception_reports_and_does_not_raise():
    from unittest.mock import patch
    client = MockZulipClient()
    bridge = _bridge(client)
    msg = {"id": 9, "type": "private", "sender_email": "owner@example.com", "content": "x"}
    with patch.object(bridge, "_process_message", side_effect=RuntimeError("kaboom")):
        bridge._worker(msg, "x")
    assert any("kaboom" in m["content"] for m in client.sent_messages)


def test_worker_threads_are_pruned():
    from unittest.mock import patch
    bridge = _bridge()
    with patch.object(bridge, "_process_message"):
        for i in range(3):
            bridge._dispatch({"id": i, "type": "private", "sender_email": "o@example.com"}, "x")
        _join_workers(bridge)
    assert len(bridge._workers) == 0


# --- file download safety --------------------------------------------------

def test_upload_label_path_traversal_is_contained(tmp_path):
    from unittest.mock import patch
    parser = FileParser(MockClient("https://chat.example.com"), "https://chat.example.com")
    dests = []

    def fake_dl(url, dest):
        dests.append(dest)
        return False, "nope"

    with patch.object(parser, "_download_file", side_effect=fake_dl):
        parser.process_message_urls("[../../../.bashrc](/user_uploads/1/x/.bashrc) "
                                    "[/etc/passwd](/user_uploads/1/y/passwd)")
    root = os.path.realpath(os.path.expanduser("~/.cache/zulip_ai_uploads"))
    assert len(dests) == 2
    for d in dests:
        assert os.path.realpath(d).startswith(root + os.sep), d


def test_download_enforces_size_cap_without_content_length(tmp_path, monkeypatch):
    from unittest.mock import patch
    import zulip_ai_bridge as zab
    monkeypatch.setattr(zab, "MAX_FILE_SIZE", 1000)
    closed = []

    class FakeResponse:
        url = "https://chat.example.com/user_uploads/1/big.bin"
        headers = {}
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=8192):
            for _ in range(10):
                yield b"x" * 500
        def close(self): closed.append(True)

    parser = FileParser(MockClient("https://chat.example.com"), "https://chat.example.com")
    dest = tmp_path / "big.bin"
    with patch("zulip_ai_bridge.requests.get", return_value=FakeResponse()):
        ok, err = parser._download_file("https://chat.example.com/user_uploads/1/big.bin", str(dest))
    assert ok is False and "size" in err
    assert not dest.exists()
    assert closed


def test_download_bad_content_length_does_not_raise(tmp_path):
    from unittest.mock import patch

    class FakeResponse:
        url = "https://chat.example.com/f"
        headers = {"Content-Length": "garbage"}
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=8192): return iter([b"ok"])
        def close(self): pass

    parser = FileParser(MockClient("https://chat.example.com"), "https://chat.example.com")
    with patch("zulip_ai_bridge.requests.get", return_value=FakeResponse()):
        ok, err = parser._download_file("https://chat.example.com/f", str(tmp_path / "f"))
    assert ok is True


def test_trusted_url_rejects_parent_domain():
    parser = FileParser(MockClient("https://chat.example.com"), "https://chat.example.com")
    assert parser._is_trusted_url("https://example.com/user_uploads/x") is False
    assert parser._is_trusted_url("https://chat.example.com:443/user_uploads/x") is True
    assert parser._is_trusted_url("https://chat.example.com@evil.org/x") is False


def test_parse_pdf_without_optional_libs(tmp_path):
    from unittest.mock import patch
    with patch.dict(sys.modules, {"pdfplumber": None, "pypdfium2": None}):
        parser = FileParser(MockClient("https://chat.example.com"), "https://chat.example.com")
    f = tmp_path / "x.pdf"
    f.write_bytes(b"not really a pdf")
    parser._parse_pdf(str(f))  # must not raise AttributeError


def test_parse_excel_does_not_require_pillow(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from unittest.mock import patch
    wb = openpyxl.Workbook()
    wb.active.append(["gene", "score"])
    f = tmp_path / "t.xlsx"
    wb.save(f)
    with patch.dict(sys.modules, {"PIL": None}):
        parser = FileParser(MockClient("https://chat.example.com"), "https://chat.example.com")
    assert "gene" in (parser._parse_excel(str(f)) or "")


# --- delivery -------------------------------------------------------------

def test_send_reply_retries_on_error_result(monkeypatch):
    import zulip_ai_bridge as zab
    monkeypatch.setattr(zab.time, "sleep", lambda s: None)

    class C(MockZulipClient):
        def __init__(self):
            super().__init__()
            self.n = 0
        def send_message(self, payload):
            self.n += 1
            self.sent_messages.append(dict(payload))
            if self.n == 1:
                return {"result": "error", "code": "RATE_LIMIT_HIT", "retry-after": 0.01,
                        "msg": "API usage exceeded rate limit"}
            return {"result": "success", "id": self.n}

    client = C()
    bridge = _bridge(client)
    bridge._send_reply({"type": "private", "sender_email": "owner@example.com"}, "hello")
    assert [m["content"] for m in client.sent_messages] == ["hello", "hello"]


def test_send_reply_error_result_triggers_notice(monkeypatch):
    import zulip_ai_bridge as zab
    monkeypatch.setattr(zab.time, "sleep", lambda s: None)

    class C(MockZulipClient):
        def send_message(self, payload):
            self.sent_messages.append(dict(payload))
            if payload["content"] == "hello":
                return {"result": "error", "msg": "Stream does not exist"}
            return {"result": "success", "id": 1}

    client = C()
    bridge = _bridge(client)
    bridge._send_reply({"type": "private", "sender_email": "owner@example.com"}, "hello")
    assert "Could not deliver" in client.sent_messages[-1]["content"]


def test_send_reply_group_dm_goes_to_all_participants():
    client = MockZulipClient()
    bridge = _bridge(client)
    msg = {"type": "private", "sender_email": "owner@example.com",
           "display_recipient": [{"email": "owner@example.com"}, {"email": "colleague@example.com"},
                                 {"email": "bot@example.zulipchat.com"}]}
    bridge._send_reply(msg, "hi")
    assert sorted(client.sent_messages[0]["to"]) == ["colleague@example.com", "owner@example.com"]


def test_send_full_reply_balances_code_fences():
    client = MockZulipClient()
    bridge = _bridge(client)
    text = "intro\n\n```python\n" + "\n".join(f"x = {i}" for i in range(3000)) + "\n```\n"
    bridge._send_full_reply({"type": "private", "sender_email": "owner@example.com"}, text)
    assert len(client.sent_messages) >= 2
    for m in client.sent_messages:
        assert m["content"].count("```") % 2 == 0, m["content"][-200:]
        assert len(m["content"]) <= 10000


# --- subprocess handling ----------------------------------------------------

def test_run_ai_subprocess_returns_output():
    from zulip_ai_bridge import run_ai_subprocess
    r = run_ai_subprocess(["sh", "-c", "echo out; echo err >&2; exit 3"], env=os.environ.copy(), timeout=10)
    assert r.returncode == 3 and r.stdout.strip() == "out" and r.stderr.strip() == "err"


def test_run_ai_subprocess_kills_grandchildren_on_timeout(tmp_path):
    import subprocess, time
    from zulip_ai_bridge import run_ai_subprocess
    pidfile = tmp_path / "pid"
    with pytest.raises(subprocess.TimeoutExpired):
        run_ai_subprocess(["sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait"],
                          env=os.environ.copy(), timeout=1)
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        # reap if it became our zombie (it should not: it is reparented)
        time.sleep(0.05)
    else:
        os.kill(pid, 9)
        pytest.fail("grandchild survived the timeout")


def test_large_prompt_is_passed_via_stdin(monkeypatch):
    """argv strings > 128 KiB fail with E2BIG on Linux; big prompts go via stdin."""
    from unittest.mock import patch
    client = MockZulipClient()
    bridge = _bridge(client)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return R()

    big = "A" * 200000
    with patch("zulip_ai_bridge.run_ai_subprocess", side_effect=fake_run):
        bridge._process_message({"id": 1, "type": "private", "sender_email": "owner@example.com"}, big)
    assert all(len(a.encode()) < 100000 for a in captured["cmd"])
    assert big in captured["input"]


def test_session_fallback_ignores_stale_session(tmp_path):
    import json, time
    from zulip_ai_bridge import extract_session_fallback
    sess = tmp_path / "last.json"
    sess.write_text(json.dumps([{"role": "assistant", "content": "answer from an OLD run"}]))
    old = time.time() - 3600
    os.utime(sess, (old, old))
    text, status, _ = extract_session_fallback(last_sess_path=str(sess), raw_stdout="",
                                               min_mtime=time.time() - 5)
    assert "OLD run" not in text
    assert status == "empty_output"


# --- memory ---------------------------------------------------------------

def test_memory_get_recent_reads_tail(tmp_path):
    from zulip_ai_bridge import ZulipMemoryManager
    mem = ZulipMemoryManager(storage_dir=str(tmp_path / "zulip_chats"))
    for i in range(50):
        mem.record_chat({"session_id": f"s{i}", "query": "q" * 2000, "status": "success"})
    recent = mem.get_recent(limit=3)
    assert [r["session_id"] for r in recent] == ["s49", "s48", "s47"]


# --- MCP server -------------------------------------------------------------

def test_mcp_get_messages_does_not_fall_back_to_unfiltered_query():
    from unittest.mock import MagicMock, patch
    import zulip_mcp_server as zms
    client = MagicMock()
    client.get_subscriptions.return_value = {"result": "error", "msg": "nope"}
    with patch.object(zms, "get_zulip_client", return_value=client):
        out = zms.do_get_messages({})
    assert out.startswith("Error")
    client.get_messages.assert_not_called()


# ---------------------------------------------------------------------------
# --check / doctor mode (offline: the Zulip client is always mocked)
# ---------------------------------------------------------------------------

import types


class _CheckClient(_OwnerClient):
    """Mocked zulip.Client for run_check; records whether the server was hit."""

    def __init__(self, pm_senders=(), bot_owner_id=None, profile_ok=True):
        super().__init__(list(pm_senders), bot_owner_id=bot_owner_id)
        self.base_url = "https://example.zulipchat.com/api/"
        self.profile_ok = profile_ok
        self.contacted = False

    def get_profile(self):
        self.contacted = True
        if not self.profile_ok:
            return {"result": "error", "msg": "Invalid API key"}
        res = super().get_profile()
        res["is_bot"] = True
        return res


@pytest.fixture
def check_env(_isolated_home, monkeypatch, tmp_path):
    """Home with a valid 0600 zuliprc, a stub zulip package and an ai binary."""
    import zulip_ai_bridge as zb
    rc = _isolated_home / ".zuliprc"
    rc.write_text("[api]\nemail=ai-bot@example.zulipchat.com\nkey=abc123\n"
                  "site=https://example.zulipchat.com\n")
    rc.chmod(0o600)
    ai_bin = tmp_path / "ai"
    ai_bin.write_text("#!/bin/sh\n")
    ai_bin.chmod(0o755)
    monkeypatch.setenv("INFER_BIN_PATH", str(ai_bin))
    for k in ("ZULIP_CONFIG", "ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_SITE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(zb, "zulip", types.SimpleNamespace(Client=object, __version__="9.9"))
    return rc


def _run_check(client=None, **kw):
    import zulip_ai_bridge as zb
    lines = []
    rc = zb.run_check(client_factory=(lambda: client) if client else None,
                      out=lines.append, **kw)
    return rc, "\n".join(lines)


def test_check_offline_ok_does_not_contact_server(check_env, monkeypatch):
    monkeypatch.setenv("ZULIP_USER", "42")
    client = _CheckClient()
    rc, out = _run_check(client)
    assert rc == 0, out
    assert "zuliprc found" in out and "0o600" in out
    assert "ai-bot@example.zulipchat.com" in out
    assert "Effective owner: 42 (from ZULIP_USER)" in out
    assert "numeric user id" in out
    assert client.contacted is False


def test_check_missing_package(check_env, monkeypatch):
    import zulip_ai_bridge as zb
    monkeypatch.setattr(zb, "zulip", types.SimpleNamespace(Client=None))
    rc, out = _run_check(_CheckClient())
    assert rc == 1
    assert "-m pip install zulip" in out


def test_check_missing_zuliprc_hints_mcp_path(check_env):
    check_env.unlink()
    alt = check_env.parent / ".config" / "zulip"
    alt.mkdir(parents=True)
    (alt / "zuliprc").write_text("[api]\n")
    rc, out = _run_check(_CheckClient())
    assert rc == 1
    assert ".zuliprc does not exist" in out
    assert "ZULIP_CONFIG=" in out


def test_check_zuliprc_from_zulip_config_env(check_env, monkeypatch, tmp_path):
    other = tmp_path / "bot.zuliprc"
    other.write_text(check_env.read_text())
    other.chmod(0o600)
    check_env.unlink()
    monkeypatch.setenv("ZULIP_CONFIG", str(other))
    rc, out = _run_check(_CheckClient())
    assert rc == 0, out
    assert str(other) in out


def test_check_warns_on_world_readable_zuliprc(check_env):
    check_env.chmod(0o644)
    rc, out = _run_check(_CheckClient())
    assert rc == 0
    assert "[WARN]" in out and f"chmod 600 {check_env}" in out


def test_check_incomplete_zuliprc_fails(check_env):
    check_env.write_text("[api]\nemail=ai-bot@example.zulipchat.com\nkey=abc\n")
    rc, out = _run_check(_CheckClient())
    assert rc == 1
    assert "missing site" in out


def test_check_online_resolves_bot_owner(check_env):
    client = _CheckClient(pm_senders=["attacker@example.org"], bot_owner_id=3)
    rc, out = _run_check(client, online=True)
    assert rc == 0, out
    assert client.contacted
    assert "bot_owner_id=3" in out
    assert "Effective owner: owner@example.com (from bot_owner_id)" in out


def test_check_online_without_owner_fails_closed(check_env):
    client = _CheckClient(pm_senders=["a@example.com", "b@example.com"])
    rc, out = _run_check(client, online=True)
    assert rc == 1
    assert "ignore EVERY message" in out


def test_check_online_warns_when_zulip_user_differs_from_owner(check_env, monkeypatch):
    monkeypatch.setenv("ZULIP_USER", "someone@example.com")
    rc, out = _run_check(_CheckClient(bot_owner_id=3), online=True)
    assert rc == 0
    assert "Effective owner: someone@example.com (from ZULIP_USER)" in out
    assert "ZULIP_USER=3" in out


def test_check_online_bad_api_key(check_env):
    rc, out = _run_check(_CheckClient(profile_ok=False), online=True)
    assert rc == 1
    assert "Invalid API key" in out


def test_check_ai_binary_missing(check_env, monkeypatch):
    import zulip_ai_bridge as zb
    monkeypatch.setattr(zb, "resolve_ai_bin", lambda: "ai")
    monkeypatch.setenv("PATH", "")
    rc, out = _run_check(_CheckClient())
    assert rc == 1
    assert "ai binary not found" in out


def test_apply_env_file_defaults_does_not_override(_isolated_home, monkeypatch):
    import zulip_ai_bridge as zb
    envf = _isolated_home / ".local" / "share" / "ai" / "env"
    envf.parent.mkdir(parents=True)
    envf.write_text('# managed\nexport ZULIP_USER="42"\nexport BRIDGE_AI_MODE="plan"\n')
    monkeypatch.setenv("BRIDGE_AI_MODE", "manual")
    monkeypatch.delenv("ZULIP_USER", raising=False)
    applied = zb.apply_env_file_defaults()
    assert os.environ["ZULIP_USER"] == "42"
    assert os.environ["BRIDGE_AI_MODE"] == "manual"
    assert applied == ["ZULIP_USER"]
    monkeypatch.delenv("ZULIP_USER")


def test_main_check_flag_and_doctor_alias(monkeypatch):
    import zulip_ai_bridge as zb
    calls = []
    monkeypatch.setattr(zb, "apply_env_file_defaults", lambda: [])
    monkeypatch.setattr(zb, "run_check", lambda online=False, env_applied=None: calls.append(online) or 0)
    monkeypatch.setattr(zb, "ZulipAiBridge", lambda: pytest.fail("listener must not start"))
    assert zb.main(["--check"]) == 0
    assert zb.main(["doctor", "--online"]) == 0
    assert calls == [False, True]


def test_bridge_without_zulip_package_explains_install(monkeypatch):
    import zulip_ai_bridge as zb
    monkeypatch.setattr(zb, "zulip", types.SimpleNamespace(Client=None))
    with pytest.raises(RuntimeError, match="pip install zulip"):
        zb.ZulipAiBridge()


def test_effective_owner_sources(monkeypatch):
    bridge = _bridge(_OwnerClient([], bot_owner_id=3))
    assert bridge.effective_owner() == ("owner@example.com", "bot_owner_id")
    monkeypatch.setenv("ZULIP_USER", "42")
    assert bridge.effective_owner() == ("42", "ZULIP_USER")
