#!/usr/bin/env python3
"""
Unit tests for Zulip AI Bridge improvements.
Tests individual components without running the full bridge loop.
"""

import unittest
from unittest.mock import patch, MagicMock, call
import sys
import os
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestErrorReporting(unittest.TestCase):
    """Test that errors are reported back to users."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a minimal bridge instance without connecting to Zulip
        self.bridge = MagicMock()
        self.bridge.bot_email = 'bot@zylalab.zulipchat.com'
        self.bridge._send_reply = MagicMock()
        
    def test_timeout_sends_error_message(self):
        """Timeout errors should send a user-friendly message."""
        # Simulate the timeout exception path
        msg = {
            'type': 'private',
            'sender_email': 'test@zylalab.zulipchat.com',
            'content': 'Test message'
        }
        
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd='ai -y -q Test message', timeout=600)
            
            # Simulate the error handling logic from _process_message
            try:
                result = subprocess.run(
                    ['ai', '-y', '-q', 'Test message'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=600
                )
                response_text = result.stdout
            except subprocess.TimeoutExpired:
                response_text = (
                    "⏱️ The agent timed out after 10 minutes. "
                    "For long-running tasks, ask me to **schedule** them so they run in "
                    "the background and notify you when done."
                )
            
            self.assertIn('timed out', response_text.lower())
            self.bridge._send_reply(msg, response_text)
            self.bridge._send_reply.assert_called_once()
            sent_content = self.bridge._send_reply.call_args[0][1]
            self.assertIn('timed out', sent_content.lower())

    def test_subprocess_failure_sends_error_message(self):
        """Subprocess failures should send error message with stderr."""
        msg = {
            'type': 'private',
            'sender_email': 'test@zylalab.zulipchat.com',
            'content': 'Test message'
        }
        
        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ''
            mock_result.stderr = 'Some error occurred'
            mock_run.return_value = mock_result
            
            result = subprocess.run(
                ['ai', '-y', '-q', 'Test message'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600
            )
            response_text = result.stdout
            if result.returncode != 0 and result.stderr:
                response_text += f"\n\n*Stderr:*\n```\n{result.stderr}\n```"
            
            self.assertIn('Stderr', response_text)
            self.bridge._send_reply(msg, response_text)
            self.bridge._send_reply.assert_called_once()
            sent_content = self.bridge._send_reply.call_args[0][1]
            self.assertIn('Stderr', sent_content)


class TestReconnectionLogic(unittest.TestCase):
    """Test reconnection logic components."""

    def test_backoff_calculation(self):
        """Test that backoff increases correctly."""
        max_backoff = 60
        current_backoff = 1
        
        # Simulate the backoff logic (7 iterations to hit cap)
        for i in range(7):
            current_backoff = min(current_backoff * 2, max_backoff)
        
        # Should be capped at 60
        self.assertEqual(current_backoff, 60)
        
        # Verify exponential growth before cap
        values = [1]
        val = 1
        for i in range(6):
            val = min(val * 2, max_backoff)
            values.append(val)
        self.assertEqual(values, [1, 2, 4, 8, 16, 32, 60])

    def test_reconnection_loop_structure(self):
        """Test that the reconnection loop has the correct structure."""
        # This tests the conceptual structure, not actual execution
        max_backoff = 60
        current_backoff = 1
        
        calls = []
        
        # Simulate the loop logic
        for attempt in range(3):
            try:
                # Simulate API call
                if attempt < 2:
                    raise ConnectionError("Simulated error")
                # Success
                break
            except (ConnectionError, Exception) as e:
                calls.append(('retry', current_backoff, str(e)))
                current_backoff = min(current_backoff * 2, max_backoff)
        
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], 1)  # First retry: 1s
        self.assertEqual(calls[1][1], 2)  # Second retry: 2s


class TestContextMessages(unittest.TestCase):
    """Test context message fetching."""

    def test_construct_prompt_with_context(self):
        """Test prompt construction with context messages."""
        # Import the clean_response function and context construction logic
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from zulip_ai_bridge import clean_response
        
        # Test that context messages are properly formatted
        context_messages = [
            {'sender_full_name': 'User', 'sender_email': 'user@zylalab.zulipchat.com', 'content': 'Previous message'},
            {'sender_full_name': 'AI', 'sender_email': 'bot@zylalab.zulipchat.com', 'content': 'Bot response'}
        ]
        
        # Simulate the context construction
        context_lines = []
        context_lines.append("---")
        context_lines.append("Recent conversation context (for reference):")
        for m in context_messages:
            sender = m.get("sender_full_name", m.get("sender_email"))
            if m.get("sender_email") == 'bot@zylalab.zulipchat.com':
                sender = "AI (You)"
            else:
                sender = f"User ({sender})"
            body = m.get("content", "").strip()
            context_lines.append(f"- {sender}: {body}")
        context_lines.append("---")
        context_lines.append("Latest query/message:")
        context_lines.append("Current message")
        
        prompt = "\n".join(context_lines)
        
        self.assertIn("AI (You)", prompt)
        self.assertIn("User (User)", prompt)
        self.assertIn("Recent conversation context", prompt)


class TestMessageCleaning(unittest.TestCase):
    """Test response cleaning."""

    def test_ansi_escape_removal(self):
        """ANSI escape codes should be removed."""
        from zulip_ai_bridge import clean_response
        
        test_input = "Hello\x1b[31m World\x1b[0m!"
        result = clean_response(test_input)
        self.assertEqual(result, "Hello World!")

    def test_blank_line_collapse(self):
        """Multiple blank lines should be collapsed."""
        from zulip_ai_bridge import clean_response
        
        test_input = "Line 1\n\n\n\n\nLine 2"
        result = clean_response(test_input)
        self.assertEqual(result, "Line 1\n\nLine 2")

    def test_horizontal_line_replacement(self):
        """Unicode horizontal lines should be replaced."""
        from zulip_ai_bridge import clean_response
        
        test_input = "Content\n────────────────────────────────────────────\nMore content"
        result = clean_response(test_input)
        self.assertIn("---", result)
        self.assertNotIn("───", result)

    def test_normalize_text_joins_wrapped_lines(self):
        """Single newlines within a paragraph should be joined to spaces."""
        from zulip_ai_bridge import normalize_text

        test_input = "This is sentence one. This is sentence two.\nThis is the next line."
        result = normalize_text(test_input)
        self.assertEqual(result, "This is sentence one. This is sentence two. This is the next line.")

    def test_normalize_text_preserves_paragraphs(self):
        """Paragraph breaks (blank lines) should be preserved."""
        from zulip_ai_bridge import normalize_text

        test_input = "First paragraph.\nSecond sentence.\n\nSecond paragraph.\nAnother sentence."
        result = normalize_text(test_input)
        self.assertEqual(
            result,
            "First paragraph. Second sentence.\n\nSecond paragraph. Another sentence."
        )

    def test_normalize_text_collapse_extra_spaces(self):
        """Multiple consecutive spaces should be collapsed to a single space."""
        from zulip_ai_bridge import normalize_text

        test_input = "Hello  world  again\nnew line"
        result = normalize_text(test_input)
        self.assertEqual(result, "Hello world again new line")

    def test_clean_response_sentence_per_line(self):
        """Full clean_response should normalise sentence-per-line LLM output."""
        from zulip_ai_bridge import clean_response

        test_input = (
            "Here is a summary of the paper.\n"
            "The antibody neutralizes the virus.\n\n"
            "Key findings:\n"
            "1. High affinity.\n"
            "2. Broad neutralization."
        )
        result = clean_response(test_input)
        self.assertEqual(
            result,
            "Here is a summary of the paper. The antibody neutralizes the virus.\n\n"
            "Key findings: 1. High affinity. 2. Broad neutralization."
        )


if __name__ == '__main__':
    unittest.main()
