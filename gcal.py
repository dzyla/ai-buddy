#!/usr/bin/env python3
import os
import sys
import json
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar']

CONFIG_DIR = os.path.expanduser('~/.config/ai')
TOKEN_PATH = os.path.join(CONFIG_DIR, 'gcal_token.json')
CREDS_PATH = os.path.join(CONFIG_DIR, 'gcal_credentials.json')


def _utc_now_iso():
    """Current UTC time as an RFC3339 'Z' string (timezone-aware; utcnow() is
    deprecated in Python 3.12+)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def local_tz_name():
    """Best-effort IANA name of the system timezone (e.g. 'America/New_York').
    Falls back to 'UTC'. Used so naive datetimes passed to create/update events
    are interpreted in the user's local zone rather than rejected by Google."""
    tz = os.environ.get('TZ')
    if tz:
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


def _event_time(dt_str, time_zone):
    """Build a Google Calendar start/end object. All-day dates (YYYY-MM-DD, no
    'T') use the 'date' field; timestamps use 'dateTime' + an explicit timeZone
    so naive values are unambiguous."""
    if dt_str and len(dt_str) == 10 and 'T' not in dt_str:
        return {'date': dt_str}
    return {'dateTime': dt_str, 'timeZone': time_zone}

def get_calendar_service():
    """Initializes and returns the Google Calendar API service."""
    creds = None
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
                with open(TOKEN_PATH, 'w') as token_file:
                    token_file.write(creds.to_json())
            except Exception as e:
                print(f"Warning: Failed to refresh token: {e}", file=sys.stderr)
                creds = None
                
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
            
            # Run local server flow
            print("Starting authentication flow. A browser window should open shortly.", file=sys.stderr)
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
            
            # Save the credentials for the next run
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(TOKEN_PATH, 'w') as token_file:
                token_file.write(creds.to_json())
            print("Authentication successful! Token saved.", file=sys.stderr)

    return build('calendar', 'v3', credentials=creds)

def list_events(time_min=None, time_max=None, max_results=20, calendar_ids=None):
    """Lists calendar events across one or more calendars."""
    try:
        service = get_calendar_service()
        
        # Default timeMin to current time if not provided
        if not time_min:
            time_min = _utc_now_iso()
            
        # Default timeMax to 7 days from now if not provided
        if not time_max:
            dt_min = datetime.datetime.fromisoformat(time_min.replace('Z', '+00:00'))
            time_max = (dt_min + datetime.timedelta(days=7)).isoformat()
            if not time_max.endswith('Z') and '+' not in time_max and '-' not in time_max:
                time_max += 'Z'

        # Fetch list of user calendars to find summaries and check selection status
        cal_summaries = {}
        selected_ids = []
        try:
            cal_list = service.calendarList().list().execute()
            for item in cal_list.get('items', []):
                cal_summaries[item['id']] = item.get('summary', item['id'])
                if item.get('selected'):
                    selected_ids.append(item['id'])
        except Exception as ex:
            print(f"Warning: Failed to fetch calendar list: {ex}", file=sys.stderr)

        # Default to all selected calendars if calendar_ids is not provided or set to 'all'
        if not calendar_ids:
            calendar_ids = selected_ids if selected_ids else ['primary']
        elif isinstance(calendar_ids, str):
            if calendar_ids.strip().lower() == 'all':
                calendar_ids = selected_ids if selected_ids else ['primary']
            else:
                calendar_ids = [calendar_ids]

        all_events = []
        for cid in calendar_ids:
            try:
                events_result = service.events().list(
                    calendarId=cid,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                for item in events_result.get('items', []):
                    item['_calendar_id'] = cid
                    all_events.append(item)
            except Exception as e:
                # Silently skip calendars we lack access to (like public holidays with restricted permissions)
                pass

        if not all_events:
            return f"No events found between {time_min} and {time_max} across calendars: {', '.join([cal_summaries.get(cid, cid) for cid in calendar_ids])}."

        # Sort all events chronologically by start time
        def get_start_time(event):
            start = event['start'].get('dateTime', event['start'].get('date'))
            return start
        all_events.sort(key=get_start_time)

        result_lines = [f"Schedule from {time_min} to {time_max}:"]
        for idx, event in enumerate(all_events, 1):
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            summary = event.get('summary', '(No Title)')
            cid = event.get('_calendar_id', 'primary')
            cal_name = cal_summaries.get(cid, cid)
            loc = f" | Location: {event['location']}" if 'location' in event else ""
            desc = f"\n    Description: {event['description']}" if 'description' in event else ""
            result_lines.append(f"[{idx}] {start} to {end} - {summary} (Calendar: {cal_name}){loc}{desc}")
            
        return "\n".join(result_lines)
    except Exception as e:
        return f"Error listing events: {e}"

def create_event(summary, start_time, end_time, description=None, location=None,
                 attendees=None, calendar_id='primary', time_zone=None):
    """Creates a calendar event. start_time/end_time are RFC3339 timestamps
    (e.g. 2026-07-05T14:00:00) or all-day dates (2026-07-05). Naive timestamps
    are interpreted in time_zone (defaults to the system local zone)."""
    try:
        service = get_calendar_service()
        tz = time_zone or local_tz_name()

        event_body = {
            'summary': summary,
            'start': _event_time(start_time, tz),
            'end': _event_time(end_time, tz),
        }
        if description:
            event_body['description'] = description
        if location:
            event_body['location'] = location
        if attendees:
            if isinstance(attendees, str):
                attendees = [a for a in attendees.split(',')]
            event_body['attendees'] = [{'email': email.strip()} for email in attendees if email.strip()]

        created_event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        link = created_event.get('htmlLink', '')
        return (
            f"Successfully created event: '{summary}' in calendar '{calendar_id}'\n"
            f"Start: {start_time} ({tz})\n"
            f"End: {end_time}\n"
            f"Event ID: {created_event.get('id', '')}\n"
            f"Event Link: {link}"
        )
    except Exception as e:
        return f"Error creating event: {e}"


def update_event(event_id, summary=None, start_time=None, end_time=None,
                 description=None, location=None, attendees=None,
                 calendar_id='primary', time_zone=None):
    """Reschedule or modify an existing event. Only the fields you pass are
    changed (patch semantics). Use gcal_list_events first to get the event_id."""
    try:
        service = get_calendar_service()
        tz = time_zone or local_tz_name()

        patch = {}
        if summary is not None:
            patch['summary'] = summary
        if description is not None:
            patch['description'] = description
        if location is not None:
            patch['location'] = location
        if start_time:
            patch['start'] = _event_time(start_time, tz)
        if end_time:
            patch['end'] = _event_time(end_time, tz)
        if attendees:
            if isinstance(attendees, str):
                attendees = [a for a in attendees.split(',')]
            patch['attendees'] = [{'email': e.strip()} for e in attendees if e.strip()]

        if not patch:
            return "Error: nothing to update — provide at least one field to change."

        updated = service.events().patch(
            calendarId=calendar_id, eventId=event_id, body=patch).execute()
        changed = ', '.join(sorted(patch.keys()))
        return (
            f"Successfully updated event '{updated.get('summary', event_id)}' "
            f"(ID: {event_id}) in calendar '{calendar_id}'.\n"
            f"Changed: {changed}\n"
            f"Event Link: {updated.get('htmlLink', '')}"
        )
    except Exception as e:
        return f"Error updating event: {e}"


def delete_event(event_id, calendar_id='primary'):
    """Cancel/delete an event by its ID. Use gcal_list_events to find the ID."""
    try:
        service = get_calendar_service()
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return f"Successfully deleted event (ID: {event_id}) from calendar '{calendar_id}'."
    except Exception as e:
        return f"Error deleting event: {e}"


def quick_add(text, calendar_id='primary'):
    """Create an event from a natural-language phrase using Google's parser,
    e.g. 'Lunch with Sam tomorrow at 12pm' or 'Dentist July 9 3-4pm'."""
    try:
        service = get_calendar_service()
        created = service.events().quickAdd(calendarId=calendar_id, text=text).execute()
        start = created.get('start', {})
        when = start.get('dateTime', start.get('date', '?'))
        return (
            f"Created event: '{created.get('summary', text)}'\n"
            f"When: {when}\n"
            f"Event ID: {created.get('id', '')}\n"
            f"Event Link: {created.get('htmlLink', '')}"
        )
    except Exception as e:
        return f"Error in quick_add: {e}"

def check_availability(time_min, time_max, calendar_ids=None):
    """Checks free/busy availability."""
    try:
        service = get_calendar_service()
        
        if not calendar_ids:
            calendar_ids = ['primary']
            
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": cid} for cid in calendar_ids]
        }
        
        freebusy_result = service.freebusy().query(body=body).execute()
        calendars = freebusy_result.get('calendars', {})
        
        result_lines = [f"Availability/Busy times from {time_min} to {time_max}:"]
        for cid, info in calendars.items():
            busy_slots = info.get('busy', [])
            if not busy_slots:
                result_lines.append(f"Calendar '{cid}': No busy slots (Fully Available)")
            else:
                result_lines.append(f"Calendar '{cid}' busy during:")
                for idx, slot in enumerate(busy_slots, 1):
                    result_lines.append(f"  [{idx}] {slot['start']} to {slot['end']}")
                    
        return "\n".join(result_lines)
    except Exception as e:
        return f"Error checking availability: {e}"

def main():
    if len(sys.argv) < 2:
        print("Usage: gcal.py [auth | list | create | availability]")
        sys.exit(1)
        
    action = sys.argv[1]
    if action == "auth":
        try:
            get_calendar_service()
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
