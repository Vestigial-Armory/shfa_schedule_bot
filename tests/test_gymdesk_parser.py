from shfa_schedule_bot.gymdesk import GymdeskPublicScheduleClient


def test_parse_data_event_info_minimal():
    payload = {
        "html": """
        <div class="schedule-event" data-test-id="schedule-event" data-event-id="117514"
          attr-date="2026-07-07"
          data-event-info="{&quot;id&quot;:117514,&quot;start&quot;:&quot;18:30:00&quot;,&quot;duration&quot;:120,&quot;title&quot;:&quot;Fundamentals of Italian Rapier&quot;,&quot;scheduled&quot;:&quot;2026-07-07&quot;,&quot;ts&quot;:&quot;2026-07-07 18:30:00&quot;,&quot;book_limit&quot;:20,&quot;booked&quot;:2,&quot;waitlisted&quot;:0,&quot;bookable&quot;:1,&quot;booking_enabled&quot;:1,&quot;canceled&quot;:0,&quot;is_recurring&quot;:1,&quot;rrule&quot;:&quot;FREQ=WEEKLY;BYDAY=TU&quot;,&quot;repeat_days&quot;:[&quot;TU&quot;],&quot;sport_id&quot;:3995,&quot;schedule_id&quot;:1889,&quot;instructors&quot;:{&quot;5426&quot;:{&quot;id&quot;:&quot;5426&quot;,&quot;name&quot;:&quot;Arthur Henry&quot;,&quot;photo&quot;:&quot;instructor5426-thumb.jpg&quot;}}}">
        </div>
        """
    }
    client = GymdeskPublicScheduleClient("https://sachema.com", "1889", "America/Los_Angeles")
    sessions = client.parse_payload(payload)
    assert len(sessions) == 1
    session = sessions[0]
    assert session.external_id == "117514"
    assert session.title == "Fundamentals of Italian Rapier"
    assert session.capacity == 20
    assert session.booked == 2
    assert session.instructors[0].name == "Arthur Henry"
    assert session.rrule == "FREQ=WEEKLY;BYDAY=TU"
