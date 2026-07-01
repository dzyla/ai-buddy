---
name: google-calendar
description: Add, view, and check schedules and availability using Google Calendar integration.
---

# Google Calendar Skill

Use this skill when the user asks to check their schedule, view upcoming events, add new events, or check availability.

## Available Tools

- `gcal_list_events`: Lists events between `time_min` and `time_max` (both ISO 8601 strings, e.g. `2026-07-01T00:00:00-06:00`).
- `gcal_create_event`: Creates a new event with `summary`, `start_time`, `end_time` (ISO 8601 strings with timezone offset), and optional `description`, `location`, and `attendees`.
- `gcal_check_availability`: Queries busy time slots between `time_min` and `time_max` to determine availability.

---

## Timestamps & Timezones

> [!IMPORTANT]
> Always check the **Local Time** and timezone offset in the **Host System Context** before calling calendar tools.

- Google Calendar API requires date-times to be formatted as ISO 8601 strings with a timezone offset or Z for UTC (e.g. `2026-07-01T14:30:00-06:00`).
- If the user asks for "today", calculate the date range from `00:00:00` to `23:59:59` on the current local date, applying the correct local timezone offset.
- Always verify that the end time of an event is after the start time.

---

## Handling Authentication Setup

If the Google Calendar tool returns an error indicating that credentials or tokens are missing:
1. Explain clearly to the user that they need to place their Google OAuth desktop credentials file at `~/.config/ai/gcal_credentials.json`.
2. Give them the step-by-step instructions:
   - Go to [Google Cloud Console](https://console.cloud.google.com/).
   - Create a project (e.g. "AI Buddy") and enable the **Google Calendar API**.
   - Configure the **OAuth Consent Screen** (User Type: External, Status: Testing, Scopes: `.../auth/calendar`, add their own email to Test Users).
   - Go to **Credentials** -> **Create Credentials** -> **OAuth client ID**. Select **Desktop app** and create.
   - Download the JSON client secret file, rename it to `gcal_credentials.json`, and place it in `~/.config/ai/gcal_credentials.json`.
   - Run `python3 ~/.local/bin/gcal.py auth` in the terminal to authorize.
