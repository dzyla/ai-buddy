"""Google Calendar tool handlers. gcal.py (next to ai_mcp.py) is imported lazily per call so
the Google client libraries are only needed when a gcal_* tool actually runs."""

def h_gcal_list_events(arguments):
    try:
        import gcal
        result = gcal.list_events(
            time_min=arguments.get("time_min"),
            time_max=arguments.get("time_max"),
            max_results=arguments.get("max_results", 20),
            calendar_ids=arguments.get("calendar_ids")
        )
        return result
    except Exception as e:
        return f"Error in gcal_list_events: {e}"


def h_gcal_create_event(arguments):
    try:
        import gcal
        result = gcal.create_event(
            summary=arguments.get("summary"),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
            description=arguments.get("description"),
            location=arguments.get("location"),
            attendees=arguments.get("attendees"),
            calendar_id=arguments.get("calendar_id", 'primary'),
            time_zone=arguments.get("time_zone"),
        )
        return result
    except Exception as e:
        return f"Error in gcal_create_event: {e}"


def h_gcal_check_availability(arguments):
    try:
        import gcal
        result = gcal.check_availability(
            time_min=arguments.get("time_min"),
            time_max=arguments.get("time_max"),
            calendar_ids=arguments.get("calendar_ids")
        )
        return result
    except Exception as e:
        return f"Error in gcal_check_availability: {e}"


def h_gcal_quick_add(arguments):
    try:
        import gcal
        result = gcal.quick_add(
            text=arguments.get("text", ""),
            calendar_id=arguments.get("calendar_id", 'primary')
        )
        return result
    except Exception as e:
        return f"Error in gcal_quick_add: {e}"


def h_gcal_update_event(arguments):
    try:
        import gcal
        result = gcal.update_event(
            event_id=arguments.get("event_id"),
            summary=arguments.get("summary"),
            start_time=arguments.get("start_time"),
            end_time=arguments.get("end_time"),
            description=arguments.get("description"),
            location=arguments.get("location"),
            attendees=arguments.get("attendees"),
            time_zone=arguments.get("time_zone"),
            calendar_id=arguments.get("calendar_id", 'primary')
        )
        return result
    except Exception as e:
        return f"Error in gcal_update_event: {e}"


def h_gcal_delete_event(arguments):
    try:
        import gcal
        result = gcal.delete_event(
            event_id=arguments.get("event_id"),
            calendar_id=arguments.get("calendar_id", 'primary')
        )
        return result
    except Exception as e:
        return f"Error in gcal_delete_event: {e}"
