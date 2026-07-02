import pytest
from unittest.mock import MagicMock, patch
import zulip_ai_bridge

def test_construct_prompt_with_context():
    bridge = MagicMock()
    bridge.bot_email = "bot@example.com"
    
    # Empty context case
    prompt = zulip_ai_bridge.ZulipAiBridge._construct_prompt_with_context(
        bridge,
        msg={"type": "private"},
        content="hello bot",
        context_messages=[]
    )
    assert prompt == "hello bot"
    
    # Non-empty context case
    context_msgs = [
        {"sender_email": "user@example.com", "sender_full_name": "Alice", "content": "hello", "id": 10},
        {"sender_email": "bot@example.com", "sender_full_name": "Bot", "content": "hi there\nhow can I help?", "id": 11}
    ]
    prompt = zulip_ai_bridge.ZulipAiBridge._construct_prompt_with_context(
        bridge,
        msg={"type": "private"},
        content="do something",
        context_messages=context_msgs
    )
    
    expected_lines = [
        "---",
        "Recent conversation context (for reference):",
        "- User (Alice): hello",
        "- AI (You):",
        "  hi there",
        "  how can I help?",
        "---",
        "Latest query/message:",
        "do something"
    ]
    for line in expected_lines:
        assert line in prompt

@patch('zulip.Client')
def test_get_context_messages_stream(mock_client_class):
    mock_client = mock_client_class.return_value
    mock_client.email = "bot@example.com"
    
    with patch('zulip_ai_bridge.ZulipAiBridge._detect_owner', return_value=None):
        bridge = zulip_ai_bridge.ZulipAiBridge()
    
    msg = {
        "id": 100,
        "type": "stream",
        "display_recipient": "general",
        "subject": "issue",
        "sender_email": "user@example.com"
    }
    
    mock_client.get_messages.return_value = {
        "result": "success",
        "messages": [
            {"id": 98, "sender_email": "user@example.com", "content": "help"},
            {"id": 99, "sender_email": "bot@example.com", "content": "coming"},
            {"id": 100, "sender_email": "user@example.com", "content": "thanks"}
        ]
    }
    
    msgs = bridge._get_context_messages(msg)
    assert len(msgs) == 2
    assert msgs[0]["id"] == 98
    assert msgs[1]["id"] == 99
    
    # Check that get_messages payload is correct
    mock_client.get_messages.assert_called_once()
    payload = mock_client.get_messages.call_args[0][0]
    assert payload["anchor"] == 100
    assert payload["num_before"] == 5
    assert payload["num_after"] == 0
    assert payload["narrow"] == [
        {"operator": "stream", "operand": "general"},
        {"operator": "topic", "operand": "issue"}
    ]

@patch('zulip.Client')
def test_get_context_messages_private(mock_client_class):
    mock_client = mock_client_class.return_value
    mock_client.email = "bot@example.com"
    
    with patch('zulip_ai_bridge.ZulipAiBridge._detect_owner', return_value=None):
        bridge = zulip_ai_bridge.ZulipAiBridge()
    
    msg = {
        "id": 100,
        "type": "private",
        "display_recipient": [
            {"email": "user@example.com", "full_name": "Alice"},
            {"email": "bot@example.com", "full_name": "Bot"}
        ],
        "sender_email": "user@example.com"
    }
    
    mock_client.get_messages.return_value = {
        "result": "success",
        "messages": [
            {"id": 98, "sender_email": "user@example.com", "content": "dm help"},
            {"id": 100, "sender_email": "user@example.com", "content": "dm help 2"}
        ]
    }
    
    msgs = bridge._get_context_messages(msg)
    assert len(msgs) == 1
    assert msgs[0]["id"] == 98
    
    mock_client.get_messages.assert_called_once()
    payload = mock_client.get_messages.call_args[0][0]
    assert payload["narrow"] == [
        {"operator": "pm-with", "operand": "user@example.com,bot@example.com"}
    ]

@patch('zulip.Client')
@patch('threading.Thread')
def test_handle_message_privacy_restriction(mock_thread, mock_client_class):
    mock_client = mock_client_class.return_value
    mock_client.email = "bot@example.com"
    
    with patch('zulip_ai_bridge.ZulipAiBridge._detect_owner', return_value=None):
        bridge = zulip_ai_bridge.ZulipAiBridge()
    
    def reset_mocks():
        mock_thread.reset_mock()
    
    with patch.dict('os.environ', {'ZULIP_USER': 'allowed_user'}):
        # Case 1: Match sender_email
        msg = {
            "id": 100,
            "type": "private",
            "sender_email": "allowed_user@example.com",
            "content": "hello"
        }
        bridge.handle_message(msg)
        mock_thread.assert_called_once()
        
        # Case 2: Match sender_username
        reset_mocks()
        msg = {
            "id": 101,
            "type": "private",
            "sender_email": "allowed_user",
            "content": "hello"
        }
        bridge.handle_message(msg)
        mock_thread.assert_called_once()

        # Case 3: Match sender_full_name
        reset_mocks()
        msg = {
            "id": 102,
            "type": "private",
            "sender_email": "other@example.com",
            "sender_full_name": "allowed_user",
            "content": "hello"
        }
        bridge.handle_message(msg)
        mock_thread.assert_called_once()

        # Case 4: No match - should not start thread
        reset_mocks()
        msg = {
            "id": 103,
            "type": "private",
            "sender_email": "someone_else@example.com",
            "sender_full_name": "Someone Else",
            "content": "hello"
        }
        bridge.handle_message(msg)
        mock_thread.assert_not_called()

        # Case 5: Match numeric ID with 'user' prefix automatically
        reset_mocks()
        with patch.dict('os.environ', {'ZULIP_USER': '1091223'}):
            msg = {
                "id": 104,
                "type": "private",
                "sender_email": "user1091223@example.com",
                "content": "hello"
            }
            bridge.handle_message(msg)
            mock_thread.assert_called_once()

@patch('zulip.Client')
@patch('subprocess.run')
def test_process_message_raw_output_env(mock_sub_run, mock_client_class):
    mock_client = mock_client_class.return_value
    mock_client.email = "bot@example.com"
    
    with patch('zulip_ai_bridge.ZulipAiBridge._detect_owner', return_value=None):
        bridge = zulip_ai_bridge.ZulipAiBridge()
    bridge._get_context_messages = MagicMock(return_value=[])
    
    msg = {
        "id": 100,
        "type": "stream",
        "display_recipient": "general",
        "subject": "issue",
        "sender_email": "user@example.com"
    }
    
    mock_sub_run.return_value = MagicMock(stdout="result", returncode=0, stderr="")
    
    bridge._process_message(msg, "hello")
    mock_sub_run.assert_called_once()
    kwargs = mock_sub_run.call_args[1]
    assert "env" in kwargs
    assert kwargs["env"]["INFER_RAW_OUTPUT"] == "1"
    assert kwargs["env"]["INFER_AUTO_APPROVE"] == "1"

@patch('zulip.Client')
def test_detect_owner(mock_client_class):
    mock_client = mock_client_class.return_value
    mock_client.email = "bot@example.com"
    
    mock_client.get_messages.return_value = {
        "result": "success",
        "messages": [
            {"sender_email": "bot@example.com", "content": "hello"},
            {"sender_email": "owner@example.com", "content": "hi bot"},
            {"sender_email": "bot@example.com", "content": "reply"}
        ]
    }
    
    bridge = zulip_ai_bridge.ZulipAiBridge()
    assert bridge.detected_owner == "owner@example.com"
    
    mock_client.get_messages.return_value = {"result": "error", "msg": "API Limit"}
    bridge = zulip_ai_bridge.ZulipAiBridge()
    assert bridge.detected_owner is None


