"""Offline unit tests for the Google Calendar and Zulip integrations.

No network or credentials required: the Google Calendar service object and the
Zulip client are replaced with MagicMocks, and we assert on the exact request
bodies our code builds (the part that actually matters for correctness) and on
how API responses are formatted back to the agent.

Run: pytest tests/test_integrations.py -v
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# These integrations depend on optional third-party libs; skip cleanly if absent.
pytest.importorskip("googleapiclient")
pytest.importorskip("google.oauth2.credentials")
pytest.importorskip("zulip")

import gcal  # noqa: E402
import zulip_mcp_server as zms  # noqa: E402


# ── Google Calendar ──────────────────────────────────────────────────────────
def _mock_service():
    """A MagicMock standing in for the googleapiclient service. Every
    .events().<op>(...).execute() call returns a benign event dict."""
    service = MagicMock()
    execute = service.events.return_value
    for op in ("insert", "patch", "delete", "quickAdd"):
        getattr(execute, op).return_value.execute.return_value = {
            "id": "evt123", "summary": "Mock Event",
            "htmlLink": "https://cal/evt123",
            "start": {"dateTime": "2026-07-05T14:00:00-06:00"},
        }
    return service


def test_create_event_uses_timezone_for_naive_time():
    service = _mock_service()
    with patch.object(gcal, "get_calendar_service", return_value=service):
        out = gcal.create_event("Dentist", "2026-07-05T14:00:00",
                                "2026-07-05T15:00:00", time_zone="America/New_York")
    body = service.events().insert.call_args.kwargs["body"]
    assert body["start"] == {"dateTime": "2026-07-05T14:00:00", "timeZone": "America/New_York"}
    assert body["end"]["timeZone"] == "America/New_York"
    assert "evt123" in out


def test_create_event_defaults_to_local_timezone():
    service = _mock_service()
    with patch.object(gcal, "get_calendar_service", return_value=service), \
         patch.object(gcal, "local_tz_name", return_value="Europe/Berlin"):
        gcal.create_event("Standup", "2026-07-05T09:00:00", "2026-07-05T09:15:00")
    body = service.events().insert.call_args.kwargs["body"]
    assert body["start"]["timeZone"] == "Europe/Berlin"


def test_create_event_all_day():
    service = _mock_service()
    with patch.object(gcal, "get_calendar_service", return_value=service):
        gcal.create_event("Vacation", "2026-07-05", "2026-07-06")
    body = service.events().insert.call_args.kwargs["body"]
    assert body["start"] == {"date": "2026-07-05"}
    assert "timeZone" not in body["start"]


def test_create_event_attendees_from_csv_string():
    service = _mock_service()
    with patch.object(gcal, "get_calendar_service", return_value=service):
        gcal.create_event("Sync", "2026-07-05T10:00:00", "2026-07-05T11:00:00",
                          attendees="a@x.com, b@y.com")
    body = service.events().insert.call_args.kwargs["body"]
    assert body["attendees"] == [{"email": "a@x.com"}, {"email": "b@y.com"}]


def test_update_event_patches_only_given_fields():
    service = _mock_service()
    with patch.object(gcal, "get_calendar_service", return_value=service):
        out = gcal.update_event("evt123", start_time="2026-07-05T16:00:00",
                                time_zone="UTC")
    call = service.events().patch.call_args
    assert call.kwargs["eventId"] == "evt123"
    body = call.kwargs["body"]
    assert body == {"start": {"dateTime": "2026-07-05T16:00:00", "timeZone": "UTC"}}
    assert "summary" not in body  # untouched fields are not sent
    assert "Changed: start" in out


def test_update_event_rejects_empty_patch():
    service = _mock_service()
    with patch.object(gcal, "get_calendar_service", return_value=service):
        out = gcal.update_event("evt123")
    assert "nothing to update" in out.lower()
    service.events().patch.assert_not_called()


def test_delete_event():
    service = _mock_service()
    with patch.object(gcal, "get_calendar_service", return_value=service):
        out = gcal.delete_event("evt123", calendar_id="work@x.com")
    call = service.events().delete.call_args
    assert call.kwargs == {"calendarId": "work@x.com", "eventId": "evt123"}
    assert "Successfully deleted" in out


def test_quick_add_passes_text_to_google():
    service = _mock_service()
    with patch.object(gcal, "get_calendar_service", return_value=service):
        out = gcal.quick_add("Lunch with Sam tomorrow 1pm")
    call = service.events().quickAdd.call_args
    assert call.kwargs["text"] == "Lunch with Sam tomorrow 1pm"
    assert call.kwargs["calendarId"] == "primary"
    assert "evt123" in out


def test_calendar_errors_are_returned_not_raised():
    service = MagicMock()
    service.events.return_value.delete.side_effect = RuntimeError("boom")
    with patch.object(gcal, "get_calendar_service", return_value=service):
        out = gcal.delete_event("evt123")
    assert out.startswith("Error deleting event")


# ── Zulip ────────────────────────────────────────────────────────────────────
def _mock_zulip(**returns):
    client = MagicMock()
    for method, value in returns.items():
        getattr(client, method).return_value = value
    return client


def test_add_reaction_builds_payload():
    client = _mock_zulip(add_reaction={"result": "success"})
    with patch.object(zms, "get_zulip_client", return_value=client):
        out = zms.do_add_reaction({"message_id": 42, "emoji_name": ":thumbs_up:"})
    # colons stripped, id coerced to int
    client.add_reaction.assert_called_once_with({"message_id": 42, "emoji_name": "thumbs_up"})
    assert "Successfully added" in out


def test_add_reaction_requires_fields():
    client = _mock_zulip()
    with patch.object(zms, "get_zulip_client", return_value=client):
        out = zms.do_add_reaction({"message_id": 42})
    assert out.startswith("Error")
    client.add_reaction.assert_not_called()


def test_edit_message_content_and_topic():
    client = _mock_zulip(update_message={"result": "success"})
    with patch.object(zms, "get_zulip_client", return_value=client):
        out = zms.do_edit_message({"message_id": 7, "content": "fixed", "topic": "moved"})
    client.update_message.assert_called_once_with(
        {"message_id": 7, "content": "fixed", "topic": "moved"})
    assert "content and topic" in out


def test_edit_message_requires_something_to_change():
    client = _mock_zulip()
    with patch.object(zms, "get_zulip_client", return_value=client):
        out = zms.do_edit_message({"message_id": 7})
    assert out.startswith("Error")
    client.update_message.assert_not_called()


def test_edit_message_surfaces_api_error():
    client = _mock_zulip(update_message={"result": "error", "msg": "no permission"})
    with patch.object(zms, "get_zulip_client", return_value=client):
        out = zms.do_edit_message({"message_id": 7, "content": "x"})
    assert "no permission" in out


def test_send_message_stream_requires_topic():
    client = _mock_zulip()
    with patch.object(zms, "get_zulip_client", return_value=client):
        out = zms.do_send_message({"message_type": "stream", "to": "general", "content": "hi"})
    assert "topic" in out.lower()
    client.send_message.assert_not_called()


def test_tools_list_advertises_new_zulip_tools():
    names = {t["name"] for t in (zms.TOOL_SEND_MESSAGE, zms.TOOL_GET_MESSAGES,
                                 zms.TOOL_ADD_REACTION, zms.TOOL_EDIT_MESSAGE)}
    assert {"zulip_add_reaction", "zulip_edit_message"} <= names


# ── Reminders (set_reminder + delivery) ──────────────────────────────────────
import datetime  # noqa: E402
import ai_mcp  # noqa: E402


def _capture_schedule(monkeypatch):
    """Patch schedule_task so set_reminder doesn't spawn a real background
    process; capture the args instead."""
    calls = {}

    def fake(task_id, prompt, interval_seconds, run_once=False, extra=None):
        calls.update(task_id=task_id, prompt=prompt,
                     interval_seconds=interval_seconds, run_once=run_once,
                     extra=extra or {})
        return f"Successfully scheduled task '{task_id}'."

    monkeypatch.setattr(ai_mcp, "schedule_task", fake)
    return calls


def test_set_reminder_with_delay_seconds(monkeypatch):
    calls = _capture_schedule(monkeypatch)
    out = ai_mcp.set_reminder(message="submit report", delay_seconds=7200,
                              zulip_to="me@x.com")
    assert calls["run_once"] is True
    assert 7195 <= calls["interval_seconds"] <= 7200
    extra = calls["extra"]
    assert extra["kind"] == "reminder"
    assert extra["message"] == "submit report"
    assert extra["zulip_to"] == "me@x.com"
    assert "Reminder set" in out


def test_set_reminder_with_iso_when(monkeypatch):
    calls = _capture_schedule(monkeypatch)
    future = (datetime.datetime.now() + datetime.timedelta(hours=3)).replace(microsecond=0)
    ai_mcp.set_reminder(message="call Alice", when=future.isoformat(), zulip_to="me@x.com")
    # ~3h in the future, within a small tolerance
    assert 3 * 3600 - 30 <= calls["interval_seconds"] <= 3 * 3600 + 5


def test_set_reminder_defaults_recipient_from_env(monkeypatch):
    calls = _capture_schedule(monkeypatch)
    monkeypatch.setenv("AI_REMINDER_ZULIP_TO", "owner@x.com")
    ai_mcp.set_reminder(message="standup", delay_seconds=600)
    assert calls["extra"]["zulip_to"] == "owner@x.com"


def test_set_reminder_rejects_past_time(monkeypatch):
    _capture_schedule(monkeypatch)
    out = ai_mcp.set_reminder(message="oops", delay_seconds=1, zulip_to="me@x.com")
    assert "past or too soon" in out.lower()


def test_set_reminder_requires_recipient(monkeypatch):
    _capture_schedule(monkeypatch)
    monkeypatch.delenv("AI_REMINDER_ZULIP_TO", raising=False)
    out = ai_mcp.set_reminder(message="hi", delay_seconds=600)
    assert "recipient" in out.lower()


def test_set_reminder_requires_message(monkeypatch):
    _capture_schedule(monkeypatch)
    out = ai_mcp.set_reminder(message="", delay_seconds=600, zulip_to="me@x.com")
    assert out.lower().startswith("error")


def test_deliver_reminder_sends_dm(monkeypatch):
    sent = {}
    monkeypatch.setattr(zms, "do_send_message", lambda a: sent.update(a) or "Successfully sent")
    status = ai_mcp._deliver_reminder(
        {"message": "drink water", "zulip_to": "me@x.com"})
    assert sent["message_type"] == "private"
    assert sent["to"] == "me@x.com"
    assert "drink water" in sent["content"]
    assert "Reminder" in sent["content"]
    assert "Successfully sent" in status


def test_deliver_reminder_sends_to_stream(monkeypatch):
    sent = {}
    monkeypatch.setattr(zms, "do_send_message", lambda a: sent.update(a) or "ok")
    ai_mcp._deliver_reminder(
        {"message": "team lunch", "zulip_stream": "general", "zulip_topic": "food"})
    assert sent["message_type"] == "stream"
    assert sent["to"] == "general"
    assert sent["topic"] == "food"


def test_set_reminder_persists_task_file(monkeypatch, tmp_path):
    """End-to-end through the real schedule_task: the reminder is written to a
    task file with kind=reminder and run_once, and NO background process spawns
    (Popen is stubbed)."""
    import json
    import subprocess
    monkeypatch.setenv("HOME", str(tmp_path))
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(a) or MagicMock())

    out = ai_mcp.set_reminder(message="water the plants", delay_seconds=3600,
                              zulip_to="me@x.com", task_id="rem_test")
    assert "Reminder set" in out
    task_file = tmp_path / ".config" / "ai" / "scheduled_tasks" / "rem_test.json"
    assert task_file.exists()
    data = json.loads(task_file.read_text())
    assert data["kind"] == "reminder"
    assert data["message"] == "water the plants"
    assert data["zulip_to"] == "me@x.com"
    assert data["run_once"] is True
    assert 3595 <= data["interval_seconds"] <= 3600
    assert len(spawned) == 1  # scheduler was launched exactly once (stubbed)


def test_scheduler_loop_delivers_reminder(monkeypatch, tmp_path):
    """Drive the real run_scheduler_loop reminder branch: a zero-delay run_once
    reminder task must call _deliver_reminder exactly once and then exit."""
    import json
    monkeypatch.setenv("HOME", str(tmp_path))
    task_dir = tmp_path / ".config" / "ai" / "scheduled_tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "rem_fire.json").write_text(json.dumps({
        "task_id": "rem_fire", "prompt": "[reminder]", "interval_seconds": 0,
        "run_once": True, "kind": "reminder", "message": "stretch",
        "zulip_to": "me@x.com", "env": {},
    }))
    delivered = []
    monkeypatch.setattr(ai_mcp, "_deliver_reminder",
                        lambda td: delivered.append(td) or "sent")

    ai_mcp.run_scheduler_loop("rem_fire")  # returns when the loop exits

    assert len(delivered) == 1
    assert delivered[0]["message"] == "stretch"
    assert not (task_dir / "rem_fire.json").exists()  # run_once cleaned up
