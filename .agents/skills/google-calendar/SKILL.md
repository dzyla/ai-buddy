---
name: google-calendar
description: CRITICAL — when checking schedules, viewing upcoming events, or managing Google Calendar: Guidelines on timestamps, timezones, and authentication setup.
---

# Google Calendar Skill

Use this skill when the user asks to check their schedule, view upcoming events, add new events, or check availability.

## Available Tools

- `gcal_list_events`: Lists events between `time_min` and `time_max` (ISO 8601, e.g. `2026-07-01T00:00:00-06:00`; a bare date like `2026-07-01` also works and, as `time_max`, means the end of that day). Each line ends with `event_id: ... | calendar_id: ...` — use those exact values for update/delete.
- `gcal_create_event`: Creates an event with `summary`, `start_time`, `end_time`, and optional `description`, `location`, `attendees`, `calendar_id`.
- `gcal_quick_add`: Creates an event from a natural-language phrase ("Dentist next Tuesday 3pm"); Google parses the time.
- `gcal_update_event`: Patch an event (only fields you pass change). Moving only `start_time` keeps the original duration.
- `gcal_delete_event`: Deletes an event by `event_id` (+ `calendar_id` if not primary).
- `gcal_check_availability`: Busy slots per calendar between `time_min` and `time_max`, plus merged busy times and the free gaps. A calendar reported as "could not check" is UNKNOWN, not free.

---

## Timestamps & Timezones

> [!IMPORTANT]
> Always check the **Local Time** and timezone offset in the **Host System Context** before calling calendar tools.

- Use ISO 8601 date-times; an explicit offset or Z is optional (e.g. `2026-07-01T14:30:00-06:00` or `2026-07-01T14:30:00`).
- If the user asks for "today", calculate the date range from `00:00:00` to `23:59:59` on the current local date, applying the correct local timezone offset.
- Naive times (no offset, e.g. `2026-07-01T14:30:00`) are interpreted in the user's local timezone with the correct DST offset for that date, so you do not have to compute the offset yourself.
- Always verify that the end time of an event is after the start time.
- **All-day events:** pass bare dates. Google's all-day end date is **exclusive**: a 3-day trip on Jul 5–7 is `start_time=2026-07-05`, `end_time=2026-07-08`. A single day can be given as start == end (it is converted automatically). `gcal_list_events` shows all-day ranges with the inclusive last day.
- Do not mix a bare date with a date-time in the same event.

---

## Handling Errors

Tool errors are short strings starting with `Error ...` and usually include a hint. Read it and fix the call instead of retrying blindly:
- `HTTP 404`: wrong `event_id`/`calendar_id` — call `gcal_list_events` and use the IDs it prints.
- `HTTP 403`/`429`: rate limit (already retried) or no write access to that calendar.
- `token is expired or revoked`: tell the user to run `python3 ~/.local/bin/gcal.py auth` in a terminal (the tool never opens a browser itself). Apps left in Google's "Testing" mode get refresh tokens that expire after 7 days.

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
