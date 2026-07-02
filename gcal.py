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
            time_min = datetime.datetime.utcnow().isoformat() + 'Z'
            
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

def create_event(summary, start_time, end_time, description=None, location=None, attendees=None, calendar_id='primary'):
    """Creates a calendar event."""
    try:
        service = get_calendar_service()
        
        event_body = {
            'summary': summary,
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
        }
        if description:
            event_body['description'] = description
        if location:
            event_body['location'] = location
        if attendees:
            event_body['attendees'] = [{'email': email.strip()} for email in attendees if email.strip()]

        created_event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        link = created_event.get('htmlLink', '')
        return (
            f"Successfully created event: '{summary}' in calendar '{calendar_id}'\n"
            f"Start: {start_time}\n"
            f"End: {end_time}\n"
            f"Event Link: {link}"
        )
    except Exception as e:
        return f"Error creating event: {e}"

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
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)

if __name__ == "__main__":
    main()
