"""Tests for ContextWindowManager in the Zulip AI bridge."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zulip_ai_bridge import ContextWindowManager


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
    """Context should be truncated when budget is exceeded."""
    # Build messages that exceed 3000 chars
    long_message = "x" * 4000
    messages = [{"sender_full_name": f"User{i}", "sender_email": f"user{i}@example.com", "content": long_message} for i in range(5)]
    result, truncated = window_manager.truncate_context(messages, "Query")
    # Should be truncated
    assert truncated
    assert len(result) < 5


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
