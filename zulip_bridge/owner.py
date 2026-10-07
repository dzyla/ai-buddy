"""Bot owner detection (who is allowed to talk to the bridge)."""
import logging

logger = logging.getLogger("ZulipBridge")


def lookup_bot_owner(client, bot_user_id):
    """Return (bot_owner_id, owner_email) for a bot account, or (None, None).

    The email is the one the bot sees in `sender_email` (on realms that hide
    emails this is the `user<id>@<realm>` form, not the real address)."""
    if not bot_user_id or not hasattr(client, "get_user_by_id"):
        return None, None
    res = client.get_user_by_id(bot_user_id)
    owner_id = (res.get("user") or {}).get("bot_owner_id") if isinstance(res, dict) else None
    if not owner_id:
        return None, None
    ores = client.get_user_by_id(owner_id)
    email = (ores.get("user") or {}).get("email") if isinstance(ores, dict) else None
    return owner_id, email


def detect_owner(client, bot_email, bot_user_id, log=print):
    """Detect the owner's Zulip email. Returns (email, source) or (None, None).

    Prefers the authoritative `bot_owner_id` of the bot account. Falls back
    to recent private-message history only when it is unambiguous (exactly
    one non-bot sender): otherwise whoever DMed the bot last before a
    restart would silently become the owner. Set ZULIP_USER to be explicit.
    """
    try:
        _owner_id, email = lookup_bot_owner(client, bot_user_id)
        if email:
            log(f"Detected owner from bot_owner_id: {email}")
            return email, "bot_owner_id"
    except Exception as e:
        logger.debug(f"Could not resolve bot owner: {e}")
    try:
        payload = {
            "anchor": "newest",
            "num_before": 20,
            "num_after": 0,
            "narrow": [{"operator": "is", "operand": "private"}],
            "apply_markdown": False
        }
        res = client.get_messages(payload)
        if res.get("result") == "success":
            senders = {
                m.get("sender_email") for m in res.get("messages", [])
                if m.get("sender_email") and m.get("sender_email") != bot_email
            }
            if len(senders) == 1:
                owner = senders.pop()
                log(f"Detected owner from past private messages: {owner}")
                return owner, "dm_history"
            if senders:
                log("⚠️ Multiple users have DMed the bot; refusing to guess the owner. Set ZULIP_USER.")
    except Exception as e:
        log(f"Error detecting owner from messages: {e}")
    return None, None


def describe_owner_value(value):
    """Explain how a ZULIP_USER value is matched against senders."""
    v = (value or "").strip().lower()
    if not v:
        return "empty: nobody is allowed"
    if v.isdigit():
        return f"numeric user id: matches sender_id == {v} (or a sender email user{v}@...)"
    if "@" in v:
        return "email: exact, case-insensitive match on the sender's email as the bot sees it"
    return "bare username: matches the part before '@' of the sender's email (prefer an email or user id)"
