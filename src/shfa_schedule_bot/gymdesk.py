from __future__ import annotations

import html as html_lib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class GymdeskInstructor:
    gymdesk_id: str
    name: str
    photo: str | None = None


@dataclass(frozen=True)
class GymdeskSession:
    session_key: str
    external_id: str
    title: str
    start_at: datetime
    end_at: datetime
    scheduled_date: str
    duration_minutes: int
    instructors: tuple[GymdeskInstructor, ...]
    capacity: int | None
    booked: int | None
    waitlisted: int | None
    bookable: bool
    booking_enabled: bool
    canceled: bool
    recurring: bool
    rrule: str | None
    repeat_days: tuple[str, ...]
    program_id: int | None
    schedule_id: int | None


class GymdeskPublicScheduleClient:
    def __init__(self, base_url: str, schedule_id: str, timezone_name: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.schedule_id = schedule_id
        self.tz = ZoneInfo(timezone_name)

    async def fetch_week_payload(self, anchor_date: str, direction: str = "next") -> dict:
        url = f"{self.base_url}/schedule/getevents"
        params = {
            "date": anchor_date,
            "schedule_id": self.schedule_id,
            "direction": direction,
        }
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base_url}/schedule",
            "User-Agent": "Mozilla/5.0 shfa-schedule-bot/0.1",
        }
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()

    async def fetch_weeks(self, start_date: datetime, weeks: int) -> list[GymdeskSession]:
        sessions: dict[str, GymdeskSession] = {}
        for offset in range(weeks):
            anchor = (start_date + timedelta(days=offset * 7)).date().isoformat()
            payload = await self.fetch_week_payload(anchor)
            for session in self.parse_payload(payload):
                sessions[session.session_key] = session
        return sorted(sessions.values(), key=lambda item: item.start_at)

    def parse_payload(self, payload: dict) -> list[GymdeskSession]:
        soup = BeautifulSoup(payload.get("html", ""), "html.parser")
        parsed: list[GymdeskSession] = []

        for element in soup.select('.schedule-event[data-event-info]'):
            raw = element.get("data-event-info")
            if not raw:
                continue
            try:
                info = json.loads(html_lib.unescape(raw))
            except json.JSONDecodeError:
                continue

            ts = info.get("ts")
            if not ts:
                continue

            start_at = datetime.fromisoformat(ts).replace(tzinfo=self.tz)
            duration = int(info.get("duration") or 0)
            end_at = start_at + timedelta(minutes=duration)
            external_id = str(info.get("id"))
            scheduled = str(info.get("scheduled") or info.get("date") or start_at.date().isoformat())
            start_text = str(info.get("start") or start_at.time().isoformat())
            session_key = f"gymdesk:{external_id}:{scheduled}:{start_text}"

            instructors = tuple(
                GymdeskInstructor(
                    gymdesk_id=str(instructor_id),
                    name=str(data.get("name") or ""),
                    photo=data.get("photo"),
                )
                for instructor_id, data in (info.get("instructors") or {}).items()
            )

            parsed.append(
                GymdeskSession(
                    session_key=session_key,
                    external_id=external_id,
                    title=str(info.get("title") or "Untitled"),
                    start_at=start_at,
                    end_at=end_at,
                    scheduled_date=scheduled,
                    duration_minutes=duration,
                    instructors=instructors,
                    capacity=_maybe_int(info.get("book_limit")),
                    booked=_maybe_int(info.get("booked")),
                    waitlisted=_maybe_int(info.get("waitlisted")),
                    bookable=bool(info.get("bookable")),
                    booking_enabled=bool(info.get("booking_enabled")),
                    canceled=bool(info.get("canceled") or info.get("cancel")),
                    recurring=bool(info.get("is_recurring") or info.get("recurring")),
                    rrule=info.get("rrule"),
                    repeat_days=tuple(info.get("repeat_days") or []),
                    program_id=_maybe_int(info.get("sport_id")),
                    schedule_id=_maybe_int(info.get("schedule_id")),
                )
            )

        return parsed


def _maybe_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
