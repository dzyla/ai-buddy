#!/usr/bin/env python3
import os
import sys
import json
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError
from zoneinfo import ZoneInfo

SCOPES = ['https://www.googleapis.com/auth/calendar']

CONFIG_DIR = os.path.expanduser('~/.config/ai')
TOKEN_PATH = os.path.join(CONFIG_DIR, 'gcal_token.json')
CREDS_PATH = os.path.join(CONFIG_DIR, 'gcal_credentials.json')


NUM_RETRIES = 3  # googleapiclient retries 429 / 403-rateLimit / 5xx with backoff

REAUTH_HINT = (
    "Re-authenticate by running 'python3 gcal.py auth' (or 'python3 gcal_auth_tolerant.py') "
    "in a terminal. If the OAuth consent screen is in 'Testing' mode, Google expires refresh "
    "tokens after 7 days; publishing the app ('In production') avoids repeated re-auth."
)

_HTTP_HINTS = {
    400: "Check that times are ISO 8601 (e.g. '2026-07-05T14:00:00') and end is after start.",
    401: "Credentials were rejected. " + REAUTH_HINT,
    403: "Permission denied or rate limit exceeded (already retried). You may lack write "
         "access to this calendar; try calendar_id='primary' or wait a minute.",
    404: "Not found: check event_id / calendar_id (call gcal_list_events for current IDs).",
    410: "The event has already been deleted.",
    429: "Rate limited by Google (already retried); wait a minute and try again.",
}


class CalendarInputError(ValueError):
    """Bad tool arguments; the message is meant to be read by the LLM."""


def _utc_now_iso():
    """Current UTC time as an RFC3339 'Z' string (timezone-aware; utcnow() is
    deprecated in Python 3.12+)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _valid_zone(name):
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def local_tz_name():
    """Best-effort IANA name of the system timezone (e.g. 'America/New_York').
    Falls back to 'UTC'. Used so naive datetimes passed to create/update events
    are interpreted in the user's local zone rather than rejected by Google."""
    tz = os.environ.get('TZ', '').strip().lstrip(':')
    if 'zoneinfo/' in tz:
        tz = tz.split('zoneinfo/', 1)[1]
    if tz and _valid_zone(tz):
        return tz
    # Debian/Ubuntu keep the name in /etc/timezone
    try:
        with open('/etc/timezone') as f:
            name = f.read().strip()
            if name:
                return name
    except Exception:
        pass
    # Most distros symlink /etc/localtime -> .../zoneinfo/<Area>/<City>
    try:
        link = os.readlink('/etc/localtime')
        if 'zoneinfo/' in link:
            return link.split('zoneinfo/', 1)[1]
    except Exception:
        pass
    return 'UTC'


def _zone(tz_name):
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return datetime.datetime.now().astimezone().tzinfo


def _clean(value):
    return str(value).strip().replace(' ', 'T', 1)


def _parse_dt(value, tz_name, field):
    """Parse an ISO 8601 date or date-time. Returns a datetime.date for bare
    dates, otherwise an aware datetime (naive values are localized to tz_name
    using the DST rules in effect on that date)."""
    s = _clean(value)
    try:
        if len(s) == 10 and 'T' not in s:
            return datetime.date.fromisoformat(s)
        dt = datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        raise CalendarInputError(
            f"invalid {field} {value!r}: use ISO 8601 like '2026-07-05T14:00:00' "
            "(offset optional) or '2026-07-05' for a whole day")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_zone(tz_name))
    return dt


def _has_offset(value):
    s = _clean(value)
    return 'T' in s and (s.endswith('Z') or '+' in s[10:] or '-' in s[10:])


def _rfc3339_bound(value, tz_name, field, end=False):
    """Normalize a range bound for events.list / freebusy, which reject values
    without an offset. A bare date means start of that day (or, for an end
    bound, the end of that day, so '2026-07-05'..'2026-07-05' covers the day)."""
    dt = _parse_dt(value, tz_name, field)
    if not isinstance(dt, datetime.datetime):
        day = dt + datetime.timedelta(days=1) if end else dt
        return datetime.datetime(day.year, day.month, day.day, tzinfo=_zone(tz_name)).isoformat()
    if _has_offset(value):
        return _clean(value)
    return dt.isoformat()


def _as_instant(dt, tz_name):
    if isinstance(dt, datetime.datetime):
        return dt
    return datetime.datetime(dt.year, dt.month, dt.day, tzinfo=_zone(tz_name))


def _event_time(dt_str, time_zone):
    """Build a Google Calendar start/end object. All-day dates (YYYY-MM-DD, no
    'T') use the 'date' field; timestamps use 'dateTime' + an explicit timeZone
    so naive values are unambiguous."""
    if dt_str:
        dt_str = dt_str.strip()
    if dt_str and len(dt_str) == 10 and 'T' not in dt_str:
        return {'date': dt_str}
    if dt_str:
        dt_str = dt_str.replace(' ', 'T', 1)
    return {'dateTime': dt_str, 'timeZone': time_zone}


def _event_span(start_time, end_time, tz):
    """Validate a start/end pair and build the Google start/end objects.
    All-day end dates are exclusive in Google: a single-day event given as
    start == end is bumped to end = start + 1 day."""
    start = _parse_dt(start_time, tz, 'start_time')
    end = _parse_dt(end_time, tz, 'end_time')
    s_all = not isinstance(start, datetime.datetime)
    e_all = not isinstance(end, datetime.datetime)
    if s_all != e_all:
        raise CalendarInputError(
            "start_time and end_time must both be dates ('2026-07-05', all-day event) "
            "or both be date-times ('2026-07-05T14:00:00').")
    if s_all:
        if end < start:
            raise CalendarInputError(f"end_time ({end_time}) must not be before start_time ({start_time}).")
        if end == start:
            end = start + datetime.timedelta(days=1)
        return {'date': start.isoformat()}, {'date': end.isoformat()}
    if end <= start:
        raise CalendarInputError(f"end_time ({end_time}) must be after start_time ({start_time}).")
    return _event_time(str(start_time), tz), _event_time(str(end_time), tz)


def _execute(request):
    return request.execute(num_retries=NUM_RETRIES)


def _http_status(e):
    try:
        return int(getattr(e.resp, 'status', 0))
    except Exception:
        return 0


def _error(action, e):
    """Turn any exception into a short, actionable string for the LLM."""
    if isinstance(e, HttpError):
        status = _http_status(e)
        reason = getattr(e, 'reason', None) or str(e)
        hint = _HTTP_HINTS.get(status)
        return f"Error {action}: Google Calendar API returned HTTP {status}: {reason}." + (f" {hint}" if hint else "")
    if isinstance(e, RefreshError):
        return f"Error {action}: Google Calendar token is expired or revoked ({e}). {REAUTH_HINT}"
    return f"Error {action}: {e}"


def _to_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(',') if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _save_token(creds):
    """Write the token atomically with owner-only permissions."""
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    tmp = TOKEN_PATH + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as token_file:
        token_file.write(creds.to_json())
    os.replace(tmp, TOKEN_PATH)


def get_calendar_service(interactive=False):
    """Initializes and returns the Google Calendar API service. The browser
    OAuth flow only runs when interactive=True ('gcal.py auth'); tool calls
    never block waiting on a browser."""
    creds = None
    refresh_error = None
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception as e:
            print(f"Warning: Failed to load token: {e}", file=sys.stderr)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_token(creds)
            except RefreshError as e:
                # invalid_grant: refresh token expired/revoked -> must re-auth
                print(f"Warning: Failed to refresh token: {e}", file=sys.stderr)
                refresh_error = e
                creds = None
            except Exception as e:
                # Network/transport failure: the token is probably fine, don't force re-auth.
                raise Exception(f"Could not reach Google to refresh the Calendar token ({e}). "
                                "Check the network connection and try again.")
        elif creds:
            creds = None  # invalid and not refreshable

        if not creds:
            if not os.path.exists(CREDS_PATH):
                raise Exception(
                    f"Google Calendar credentials file not found at {CREDS_PATH}.\n\n"
                    "To set it up, please follow these steps:\n"
                    "1. Go to the Google Cloud Console: https://console.cloud.google.com/\n"
                    "2. Create a new project (e.g., 'AI Buddy').\n"
                    "3. Enable the 'Google Calendar API' for your project.\n"
                    "4. Go to 'APIs & Services' -> 'Credentials'.\n"
                    "5. Click 'Configure Consent Screen', choose 'External', fill in basic app info, add scope "
                    "'.../auth/calendar', and add your own email to 'Test users'. Keep app in testing mode.\n"
                    "6. Go to 'Credentials' -> 'Create Credentials' -> 'OAuth client ID'.\n"
                    "7. Choose 'Desktop app' as Application Type, name it, and click 'Create'.\n"
                    "8. Download the JSON credentials file and save it to the path:\n"
                    f"   {CREDS_PATH}\n"
                    "9. Once saved, authorize the app by running this command in your shell:\n"
                    "   python3 gcal.py auth\n"
                )

            if not interactive:
                if refresh_error is not None:
                    raise Exception(f"Google Calendar token is expired or revoked ({refresh_error}). {REAUTH_HINT}")
                raise Exception("Google Calendar authentication required. " + REAUTH_HINT)

            # Run local server flow
            print("Starting authentication flow. A browser window should open shortly.", file=sys.stderr)
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

            # Save the credentials for the next run
            _save_token(creds)
            print("Authentication successful! Token saved.", file=sys.stderr)

    return build('calendar', 'v3', credentials=creds)


def _calendar_list(service):
    """-> ({id: summary}, [selected ids]); tolerant of failures."""
    summaries, selected = {}, []
    try:
        cal_list = _execute(service.calendarList().list())
        for item in cal_list.get('items', []):
            summaries[item['id']] = item.get('summary', item['id'])
            if item.get('selected'):
                selected.append(item['id'])
    except Exception as ex:
        print(f"Warning: Failed to fetch calendar list: {ex}", file=sys.stderr)
    return summaries, selected


def _resolve_calendar_ids(calendar_ids, selected_ids, default):
    ids = _to_list(calendar_ids)
    if not ids or [i.lower() for i in ids] == ['all']:
        return list(selected_ids) if selected_ids else [default]
    return ids


def _fmt_span(event):
    start, end = event.get('start', {}), event.get('end', {})
    if 'date' in start:
        first = start['date']
        last = end.get('date')
        try:  # Google all-day end dates are exclusive; show the inclusive last day
            last = (datetime.date.fromisoformat(last) - datetime.timedelta(days=1)).isoformat()
        except Exception:
            pass
        if not last or last <= first:
            return f"{first} (all day)"
        return f"{first} to {last} (all day)"
    return f"{start.get('dateTime', '?')} to {end.get('dateTime', '?')}"


def list_events(time_min=None, time_max=None, max_results=20, calendar_ids=None, time_zone=None):
    """Lists calendar events across one or more calendars."""
    try:
        tz = time_zone or local_tz_name()
        time_min = _rfc3339_bound(time_min, tz, 'time_min') if time_min else _utc_now_iso()
        if time_max:
            time_max = _rfc3339_bound(time_max, tz, 'time_max', end=True)
        else:
            # Default timeMax to 7 days after timeMin
            time_max = (_parse_dt(time_min, tz, 'time_min') + datetime.timedelta(days=7)).isoformat()
        if _parse_dt(time_max, tz, 'time_max') <= _parse_dt(time_min, tz, 'time_min'):
            raise CalendarInputError(f"time_max ({time_max}) must be after time_min ({time_min}).")
        try:
            max_results = max(1, min(int(max_results or 20), 2500))
        except (TypeError, ValueError):
            max_results = 20

        service = get_calendar_service()
        cal_summaries, selected_ids = _calendar_list(service)
        calendar_ids = _resolve_calendar_ids(calendar_ids, selected_ids, 'primary')

        all_events, failed = [], {}
        for cid in calendar_ids:
            try:
                fetched, page_token = 0, None
                while True:
                    kwargs = dict(calendarId=cid, timeMin=time_min, timeMax=time_max,
                                  maxResults=min(250, max_results), singleEvents=True,
                                  orderBy='startTime', timeZone=tz)
                    if page_token:
                        kwargs['pageToken'] = page_token
                    events_result = _execute(service.events().list(**kwargs))
                    for item in events_result.get('items', []):
                        item['_calendar_id'] = cid
                        all_events.append(item)
                        fetched += 1
                    page_token = events_result.get('nextPageToken')
                    if not page_token or fetched >= max_results:
                        break
            except Exception as e:
                failed[cid] = _error('reading calendar', e).split(': ', 1)[1]

        names = ', '.join(cal_summaries.get(cid, cid) for cid in calendar_ids)
        fail_note = ''
        if failed:
            fail_note = "\nCould not read calendars: " + "; ".join(
                f"{cal_summaries.get(c, c)}: {msg}" for c, msg in failed.items())
        if not all_events:
            if failed and len(failed) == len(calendar_ids):
                return f"Error listing events: could not read any requested calendar.{fail_note}"
            return f"No events found between {time_min} and {time_max} across calendars: {names}.{fail_note}"

        # Sort chronologically by the actual instant (strings with different
        # offsets, or all-day dates, don't sort correctly lexically).
        def get_start_time(event):
            start = event.get('start', {})
            try:
                return _as_instant(_parse_dt(start.get('dateTime') or start.get('date'), tz, 'start'), tz).timestamp()
            except Exception:
                return float('inf')
        all_events.sort(key=get_start_time)
        total = len(all_events)
        all_events = all_events[:max_results]

        result_lines = [f"Schedule from {time_min} to {time_max}:"]
        for idx, event in enumerate(all_events, 1):
            summary = event.get('summary', '(No Title)')
            cid = event.get('_calendar_id', 'primary')
            cal_name = cal_summaries.get(cid, cid)
            loc = f" | Location: {event['location']}" if 'location' in event else ""
            desc = f"\n    Description: {event['description']}" if 'description' in event else ""
            result_lines.append(
                f"[{idx}] {_fmt_span(event)} - {summary} (Calendar: {cal_name}){loc}"
                f" | event_id: {event.get('id', '?')} | calendar_id: {cid}{desc}")
        if total > len(all_events):
            result_lines.append(f"(Showing first {len(all_events)} of {total} events; "
                                "narrow the time range or raise max_results to see more.)")
        if fail_note:
            result_lines.append(fail_note.strip())

        return "\n".join(result_lines)
    except Exception as e:
        return _error('listing events', e)

def create_event(summary, start_time, end_time, description=None, location=None,
                 attendees=None, calendar_id='primary', time_zone=None):
    """Creates a calendar event. start_time/end_time are RFC3339 timestamps
    (e.g. 2026-07-05T14:00:00) or all-day dates (2026-07-05). Naive timestamps
    are interpreted in time_zone (defaults to the system local zone)."""
    try:
        if not summary or not str(summary).strip():
            raise CalendarInputError("summary (event title) is required.")
        if not start_time or not end_time:
            raise CalendarInputError("both start_time and end_time are required "
                                     "(or use gcal_quick_add for natural-language phrases).")
        tz = time_zone or local_tz_name()
        start_obj, end_obj = _event_span(start_time, end_time, tz)
        calendar_id = calendar_id or 'primary'

        event_body = {
            'summary': summary,
            'start': start_obj,
            'end': end_obj,
        }
        if description:
            event_body['description'] = description
        if location:
            event_body['location'] = location
        if attendees:
            event_body['attendees'] = [{'email': email} for email in _to_list(attendees)]

        service = get_calendar_service()
        created_event = _execute(service.events().insert(calendarId=calendar_id, body=event_body))
        link = created_event.get('htmlLink', '')
        return (
            f"Successfully created event: '{summary}' in calendar '{calendar_id}'\n"
            f"Start: {start_time} ({tz})\n"
            f"End: {end_time}\n"
            f"Event ID: {created_event.get('id', '')}\n"
            f"Event Link: {link}"
        )
    except Exception as e:
        return _error('creating event', e)


def _shifted_end(existing, start_time, tz):
    """When only the start of an event moves, compute the new end that keeps
    the original duration. Returns an end object, or None if unknown."""
    if not isinstance(existing, dict):
        return None
    old_s, old_e = existing.get('start') or {}, existing.get('end') or {}
    new_start = _parse_dt(start_time, tz, 'start_time')
    new_all = not isinstance(new_start, datetime.datetime)
    if 'date' in old_s and 'date' in old_e:
        if not new_all:
            raise CalendarInputError("this is an all-day event; to make it a timed event pass both "
                                     "start_time and end_time.")
        days = datetime.date.fromisoformat(old_e['date']) - datetime.date.fromisoformat(old_s['date'])
        return {'date': (new_start + days).isoformat()}
    if 'dateTime' in old_s and 'dateTime' in old_e:
        if new_all:
            raise CalendarInputError("this is a timed event; to make it all-day pass both start_time "
                                     "and end_time as dates.")
        dur = (_parse_dt(old_e['dateTime'], tz, 'end') - _parse_dt(old_s['dateTime'], tz, 'start'))
        if _has_offset(start_time):
            return _event_time((new_start + dur).isoformat(), tz)
        naive = datetime.datetime.fromisoformat(_clean(start_time)) + dur  # wall-clock, DST-safe
        return _event_time(naive.isoformat(), tz)
    return None


def update_event(event_id, summary=None, start_time=None, end_time=None,
                 description=None, location=None, attendees=None,
                 calendar_id='primary', time_zone=None):
    """Reschedule or modify an existing event. Only the fields you pass are
    changed (patch semantics). Use gcal_list_events first to get the event_id."""
    try:
        if not event_id or not str(event_id).strip():
            raise CalendarInputError("event_id is required (get it from gcal_list_events).")
        tz = time_zone or local_tz_name()
        calendar_id = calendar_id or 'primary'

        patch = {}
        if summary is not None:
            patch['summary'] = summary
        if description is not None:
            patch['description'] = description
        if location is not None:
            patch['location'] = location
        if attendees:
            patch['attendees'] = [{'email': e} for e in _to_list(attendees)]

        if not patch and not start_time and not end_time:
            return "Error: nothing to update — provide at least one field to change."

        service = get_calendar_service()
        if start_time and end_time:
            patch['start'], patch['end'] = _event_span(start_time, end_time, tz)
            # Patch merges start/end objects, so when switching between all-day
            # and timed the old key must be nulled or Google rejects having both.
            for key in ('start', 'end'):
                patch[key].setdefault('dateTime' if 'date' in patch[key] else 'date', None)
        elif start_time:
            _parse_dt(start_time, tz, 'start_time')  # validate
            patch['start'] = _event_time(str(start_time), tz)
            # Keep the original duration; otherwise moving the start past the
            # old end makes Google reject the event (end before start).
            try:
                existing = _execute(service.events().get(calendarId=calendar_id, eventId=event_id))
            except HttpError:
                raise
            except Exception:
                existing = None
            new_end = _shifted_end(existing, start_time, tz)
            if new_end:
                patch['end'] = new_end
        elif end_time:
            _parse_dt(end_time, tz, 'end_time')  # validate
            patch['end'] = _event_time(str(end_time), tz)

        updated = _execute(service.events().patch(
            calendarId=calendar_id, eventId=event_id, body=patch))
        changed = ', '.join(sorted(patch.keys()))
        return (
            f"Successfully updated event '{updated.get('summary', event_id)}' "
            f"(ID: {event_id}) in calendar '{calendar_id}'.\n"
            f"Changed: {changed}\n"
            f"Event Link: {updated.get('htmlLink', '')}"
        )
    except Exception as e:
        return _error('updating event', e)


def delete_event(event_id, calendar_id='primary'):
    """Cancel/delete an event by its ID. Use gcal_list_events to find the ID."""
    try:
        if not event_id or not str(event_id).strip():
            raise CalendarInputError("event_id is required (get it from gcal_list_events).")
        calendar_id = calendar_id or 'primary'
        service = get_calendar_service()
        try:
            _execute(service.events().delete(calendarId=calendar_id, eventId=event_id))
        except HttpError as e:
            if _http_status(e) == 410:
                return f"Event (ID: {event_id}) was already deleted from calendar '{calendar_id}'; nothing to do."
            raise
        return f"Successfully deleted event (ID: {event_id}) from calendar '{calendar_id}'."
    except Exception as e:
        return _error('deleting event', e)


def quick_add(text, calendar_id='primary'):
    """Create an event from a natural-language phrase using Google's parser,
    e.g. 'Lunch with Sam tomorrow at 12pm' or 'Dentist July 9 3-4pm'."""
    try:
        if not text or not str(text).strip():
            raise CalendarInputError("text is required, e.g. 'Lunch with Sam tomorrow 1pm'.")
        service = get_calendar_service()
        created = _execute(service.events().quickAdd(calendarId=calendar_id or 'primary', text=text))
        start = created.get('start', {})
        when = start.get('dateTime', start.get('date', '?'))
        return (
            f"Created event: '{created.get('summary', text)}'\n"
            f"When: {when}\n"
            f"Event ID: {created.get('id', '')}\n"
            f"Event Link: {created.get('htmlLink', '')}"
        )
    except Exception as e:
        return _error('in quick_add', e)


def check_availability(time_min, time_max, calendar_ids=None, time_zone=None):
    """Checks free/busy availability across one or more calendars. Busy slots
    from all calendars are merged (overlaps collapsed) and free gaps listed."""
    try:
        if not time_min or not time_max:
            raise CalendarInputError("both time_min and time_max are required.")
        tz = time_zone or local_tz_name()
        time_min = _rfc3339_bound(time_min, tz, 'time_min')
        time_max = _rfc3339_bound(time_max, tz, 'time_max', end=True)
        win_start = _parse_dt(time_min, tz, 'time_min')
        win_end = _parse_dt(time_max, tz, 'time_max')
        if win_end <= win_start:
            raise CalendarInputError(f"time_max ({time_max}) must be after time_min ({time_min}).")

        service = get_calendar_service()
        ids = _to_list(calendar_ids)
        if [i.lower() for i in ids] == ['all']:
            ids = _resolve_calendar_ids('all', _calendar_list(service)[1], 'primary')
        calendar_ids = ids or ['primary']

        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": tz,
            "items": [{"id": cid} for cid in calendar_ids]
        }

        freebusy_result = _execute(service.freebusy().query(body=body))
        calendars = freebusy_result.get('calendars', {})

        result_lines = [f"Availability/Busy times from {time_min} to {time_max}:"]
        intervals, failed = [], []
        for cid in list(calendar_ids) + [c for c in calendars if c not in calendar_ids]:
            info = calendars.get(cid)
            if info is None:
                failed.append(cid)
                result_lines.append(f"Calendar '{cid}': no data returned (unknown availability)")
                continue
            errors = info.get('errors') or []
            if errors:
                failed.append(cid)
                reasons = ', '.join(err.get('reason', '?') for err in errors)
                result_lines.append(f"Calendar '{cid}': could not check availability ({reasons}) "
                                    "- treat as unknown, NOT free")
                continue
            busy_slots = info.get('busy', [])
            if not busy_slots:
                result_lines.append(f"Calendar '{cid}': No busy slots (Fully Available)")
            else:
                result_lines.append(f"Calendar '{cid}' busy during:")
                for idx, slot in enumerate(busy_slots, 1):
                    result_lines.append(f"  [{idx}] {slot['start']} to {slot['end']}")
                    intervals.append((_parse_dt(slot['start'], tz, 'busy'), slot['start'],
                                      _parse_dt(slot['end'], tz, 'busy'), slot['end']))

        if len(failed) == len(calendar_ids):
            return "\n".join(result_lines)

        # Merge overlapping/adjacent busy intervals across calendars.
        merged = []
        for s, s_str, e, e_str in sorted(intervals, key=lambda x: x[0]):
            if merged and s <= merged[-1][2]:
                if e > merged[-1][2]:
                    merged[-1][2], merged[-1][3] = e, e_str
            else:
                merged.append([s, s_str, e, e_str])
        if len(calendar_ids) > 1 and merged:
            result_lines.append("Combined busy (all calendars, overlaps merged):")
            for idx, (_, s_str, _, e_str) in enumerate(merged, 1):
                result_lines.append(f"  [{idx}] {s_str} to {e_str}")

        free, cur, cur_str = [], win_start, time_min
        for s, s_str, e, e_str in merged:
            if s > cur:
                free.append((cur_str, s_str))
            if e > cur:
                cur, cur_str = e, e_str
        if win_end > cur:
            free.append((cur_str, time_max))
        note = " (excluding calendars that could not be checked)" if failed else ""
        if free:
            result_lines.append(f"Free slots{note}:")
            for idx, (s_str, e_str) in enumerate(free, 1):
                result_lines.append(f"  [{idx}] {s_str} to {e_str}")
        else:
            result_lines.append(f"Free slots{note}: none in this window")

        return "\n".join(result_lines)
    except Exception as e:
        return _error('checking availability', e)

def main():
    if len(sys.argv) < 2:
        print("Usage: gcal.py [auth | list | create | availability]")
        sys.exit(1)
        
    action = sys.argv[1]
    if action == "auth":
        try:
            get_calendar_service(interactive=True)
            print("Authentication setup complete!")
        except Exception as e:
            print(f"Authentication failed: {e}", file=sys.stderr)
            sys.exit(1)
    elif action == "list":
        time_min = sys.argv[2] if len(sys.argv) > 2 else None
        time_max = sys.argv[3] if len(sys.argv) > 3 else None
        calendar_ids = sys.argv[4].split(",") if len(sys.argv) > 4 else None
        print(list_events(time_min, time_max, calendar_ids=calendar_ids))
    elif action == "create":
        if len(sys.argv) < 5:
            print("Usage: gcal.py create <summary> <start_time> <end_time> [description] [location] [calendar_id]")
            sys.exit(1)
        summary = sys.argv[2]
        start_time = sys.argv[3]
        end_time = sys.argv[4]
        desc = sys.argv[5] if len(sys.argv) > 5 else None
        loc = sys.argv[6] if len(sys.argv) > 6 else None
        calendar_id = sys.argv[7] if len(sys.argv) > 7 else 'primary'
        print(create_event(summary, start_time, end_time, desc, loc, calendar_id=calendar_id))
    elif action == "availability":
        if len(sys.argv) < 4:
            print("Usage: gcal.py availability <time_min> <time_max>")
            sys.exit(1)
        time_min = sys.argv[2]
        time_max = sys.argv[3]
        print(check_availability(time_min, time_max))
    elif action == "quickadd":
        if len(sys.argv) < 3:
            print("Usage: gcal.py quickadd <natural language text> [calendar_id]")
            sys.exit(1)
        text = sys.argv[2]
        calendar_id = sys.argv[3] if len(sys.argv) > 3 else 'primary'
        print(quick_add(text, calendar_id))
    elif action == "delete":
        if len(sys.argv) < 3:
            print("Usage: gcal.py delete <event_id> [calendar_id]")
            sys.exit(1)
        event_id = sys.argv[2]
        calendar_id = sys.argv[3] if len(sys.argv) > 3 else 'primary'
        print(delete_event(event_id, calendar_id))
    elif action == "update":
        if len(sys.argv) < 3:
            print("Usage: gcal.py update <event_id> [summary] [start_time] [end_time] [calendar_id]")
            sys.exit(1)
        event_id = sys.argv[2]
        summary = sys.argv[3] if len(sys.argv) > 3 else None
        start_time = sys.argv[4] if len(sys.argv) > 4 else None
        end_time = sys.argv[5] if len(sys.argv) > 5 else None
        calendar_id = sys.argv[6] if len(sys.argv) > 6 else 'primary'
        print(update_event(event_id, summary=summary, start_time=start_time,
                           end_time=end_time, calendar_id=calendar_id))
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)

if __name__ == "__main__":
    main()
