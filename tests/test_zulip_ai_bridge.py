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
