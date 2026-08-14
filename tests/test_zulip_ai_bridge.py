"""Tests for ContextWindowManager and FileParser in the Zulip AI bridge."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zulip_ai_bridge import ContextWindowManager, FileParser


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

    def get_me(self):
        class Response:
            json = {"email": "bot@example.zulipchat.com"}
        return Response()

    def get_messages(self, payload):
        return {"result": "success", "messages": []}


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

    with patch("zulip_ai_bridge.subprocess.run", side_effect=fake_run):
        bridge._process_message(msg, "do something")

    assert any(f == "--auto" for f in captured["cmd"])
    assert captured["env_auto"] == "1"
    assert "done" in client.last_sent["content"]
