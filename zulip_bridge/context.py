"""Conversation context window management."""
import os
import logging

logger = logging.getLogger("ZulipBridge")



class ContextWindowManager:
    """Manages conversation context to stay within AI model's context window."""
    
    def __init__(self, max_tokens=None, max_messages=15):
        """
        Initialize the context window manager.
        
        Args:
            max_tokens: Maximum estimated tokens for the prompt (conservative estimate).
                        If None, uses BRIDGE_MAX_CONTEXT_TOKENS or scales with LLAMA_CTX_SIZE.
            max_messages: Maximum number of context messages to include
        """
        if max_tokens is None:
            env_tokens = os.environ.get("BRIDGE_MAX_CONTEXT_TOKENS")
            if env_tokens and env_tokens.isdigit():
                max_tokens = int(env_tokens)
            else:
                ctx_env = os.environ.get("LLAMA_CTX_SIZE", "262144")
                try:
                    ctx_val = int(ctx_env)
                    max_tokens = min(32768, max(4096, ctx_val // 4))
                except ValueError:
                    max_tokens = 32768
        self.max_tokens = max_tokens
        self.max_messages = max_messages
    
    def estimate_tokens(self, text):
        """Rough token estimation: ~4 chars per token for English text."""
        return len(text) // 4
    
    def truncate_context(self, context_messages, current_content):
        """
        Truncate context messages to fit within the token / message budget.

        Strategy: keep the most recent messages and drop the oldest ones when
        either the ``max_tokens`` budget or the ``max_messages`` cap is
        exceeded.  This preserves the most relevant conversational context.

        Args:
            context_messages: List of message dictionaries
            current_content: The current message content

        Returns:
            Tuple of (truncated messages, whether truncation occurred)
        """
        if not context_messages:
            return context_messages, False

        # Estimate how many characters the current query occupies.
        estimated_length = len(current_content)
        kept = []

        # Walk backwards through the context so the most recent messages are kept.
        for msg in reversed(context_messages):
            sender = msg.get("sender_full_name", msg.get("sender_email", "Unknown"))
            body = msg.get("content", "").strip()
            msg_length = len(f"- {sender}: {body}")

            # Check token budget (rough estimate: 4 chars ≈ 1 token)
            if estimated_length + msg_length > self.max_tokens * 4:
                logger.info(
                    f"Context truncated to fit token budget: keeping "
                    f"{len(kept)} of {len(context_messages)} messages."
                )
                break

            # Check message count cap
            if len(kept) >= self.max_messages:
                logger.info(
                    f"Context truncated to fit message cap: keeping "
                    f"{len(kept)} of {len(context_messages)} messages."
                )
                break

            kept.append(msg)
            estimated_length += msg_length

        # Restore to chronological order so the prompt reads naturally.
        kept = list(reversed(kept))
        truncated = len(kept) < len(context_messages)
        return kept, truncated
    
    def format_context(self, context_messages, bot_email):
        """Format context messages into a readable string for the prompt."""
        if not context_messages:
            return ""
        
        context_lines = ["---", "Recent conversation context (for reference):"]
        for m in context_messages:
            sender = m.get("sender_full_name", m.get("sender_email"))
            if m.get("sender_email") == bot_email:
                sender = "AI (You)"
            else:
                sender = f"User ({sender})"
            body = m.get("content", "").strip()
            if "\n" in body:
                body = "\n".join("  " + line for line in body.splitlines())
            context_lines.append(f"- {sender}: {body}")
        context_lines.append("---")
        context_lines.append("Latest query/message:")
        
        return "\n".join(context_lines)
