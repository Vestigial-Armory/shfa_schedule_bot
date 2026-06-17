from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .gymdesk import GymdeskSession


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (guild_id, key)
                );

                CREATE TABLE IF NOT EXISTS instructor_map (
                    guild_id TEXT NOT NULL,
                    gymdesk_name TEXT NOT NULL,
                    discord_user_id TEXT NOT NULL,
                    PRIMARY KEY (guild_id, gymdesk_name)
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    guild_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    scheduled_date TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    instructors_json TEXT NOT NULL,
                    capacity INTEGER,
                    booked INTEGER,
                    waitlisted INTEGER,
                    bookable INTEGER NOT NULL,
                    booking_enabled INTEGER NOT NULL,
                    canceled INTEGER NOT NULL,
                    recurring INTEGER NOT NULL,
                    rrule TEXT,
                    repeat_days_json TEXT NOT NULL,
                    program_id INTEGER,
                    schedule_id INTEGER,
                    staffing_status TEXT NOT NULL DEFAULT 'pending',
                    assigned_user_id TEXT,
                    confirmed_by TEXT,
                    confirmed_at TEXT,
                    sub_requested_by TEXT,
                    sub_accepted_by TEXT,
                    reminder_sent_at TEXT,
                    escalation_sent_at TEXT,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, session_key)
                );

                CREATE TABLE IF NOT EXISTS change_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    request_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    owner_completed_by TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    announced_at TEXT
                );
                """
            )

    def get_setting(self, guild_id: int, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE guild_id=? AND key=?",
                (str(guild_id), key),
            ).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, guild_id: int, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(guild_id, key, value) VALUES(?, ?, ?) "
                "ON CONFLICT(guild_id, key) DO UPDATE SET value=excluded.value",
                (str(guild_id), key, value),
            )

    def list_settings(self, guild_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    "SELECT key, value FROM settings WHERE guild_id=? ORDER BY key",
                    (str(guild_id),),
                )
            )

    def map_instructor(self, guild_id: int, gymdesk_name: str, discord_user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO instructor_map(guild_id, gymdesk_name, discord_user_id) VALUES(?, ?, ?) "
                "ON CONFLICT(guild_id, gymdesk_name) DO UPDATE SET discord_user_id=excluded.discord_user_id",
                (str(guild_id), gymdesk_name, str(discord_user_id)),
            )

    def get_instructor_user_id(self, guild_id: int, gymdesk_name: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT discord_user_id FROM instructor_map WHERE guild_id=? AND gymdesk_name=?",
                (str(guild_id), gymdesk_name),
            ).fetchone()
        return str(row["discord_user_id"]) if row else None

    def upsert_sessions(self, guild_id: int, sessions: list[GymdeskSession]) -> None:
        now = datetime.utcnow().isoformat()
        with self.connect() as conn:
            for session in sessions:
                assigned_user_id = None
                if session.instructors:
                    assigned_user_id = self.get_instructor_user_id(guild_id, session.instructors[0].name)

                conn.execute(
                    """
                    INSERT INTO sessions(
                        guild_id, session_key, external_id, title, start_at, end_at, scheduled_date,
                        duration_minutes, instructors_json, capacity, booked, waitlisted, bookable,
                        booking_enabled, canceled, recurring, rrule, repeat_days_json, program_id,
                        schedule_id, assigned_user_id, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, session_key) DO UPDATE SET
                        title=excluded.title,
                        start_at=excluded.start_at,
                        end_at=excluded.end_at,
                        scheduled_date=excluded.scheduled_date,
                        duration_minutes=excluded.duration_minutes,
                        instructors_json=excluded.instructors_json,
                        capacity=excluded.capacity,
                        booked=excluded.booked,
                        waitlisted=excluded.waitlisted,
                        bookable=excluded.bookable,
                        booking_enabled=excluded.booking_enabled,
                        canceled=excluded.canceled,
                        recurring=excluded.recurring,
                        rrule=excluded.rrule,
                        repeat_days_json=excluded.repeat_days_json,
                        program_id=excluded.program_id,
                        schedule_id=excluded.schedule_id,
                        assigned_user_id=COALESCE(sessions.assigned_user_id, excluded.assigned_user_id),
                        last_seen_at=excluded.last_seen_at
                    """,
                    (
                        str(guild_id),
                        session.session_key,
                        session.external_id,
                        session.title,
                        session.start_at.isoformat(),
                        session.end_at.isoformat(),
                        session.scheduled_date,
                        session.duration_minutes,
                        json.dumps([asdict(i) for i in session.instructors]),
                        session.capacity,
                        session.booked,
                        session.waitlisted,
                        int(session.bookable),
                        int(session.booking_enabled),
                        int(session.canceled),
                        int(session.recurring),
                        session.rrule,
                        json.dumps(list(session.repeat_days)),
                        session.program_id,
                        session.schedule_id,
                        assigned_user_id,
                        now,
                    ),
                )

    def find_sessions(self, guild_id: int, start_iso: str, end_iso: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT * FROM sessions
                    WHERE guild_id=? AND start_at >= ? AND start_at < ?
                    ORDER BY start_at
                    """,
                    (str(guild_id), start_iso, end_iso),
                )
            )

    def get_session(self, guild_id: int, session_key: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM sessions WHERE guild_id=? AND session_key=?",
                (str(guild_id), session_key),
            ).fetchone()

    def mark_reminder_sent(self, guild_id: int, session_key: str) -> None:
        self._set_session_fields(guild_id, session_key, reminder_sent_at=datetime.utcnow().isoformat())

    def mark_escalation_sent(self, guild_id: int, session_key: str) -> None:
        self._set_session_fields(guild_id, session_key, escalation_sent_at=datetime.utcnow().isoformat())

    def confirm_session(self, guild_id: int, session_key: str, user_id: int) -> None:
        self._set_session_fields(
            guild_id,
            session_key,
            staffing_status="confirmed",
            confirmed_by=str(user_id),
            confirmed_at=datetime.utcnow().isoformat(),
        )

    def request_sub(self, guild_id: int, session_key: str, user_id: int) -> None:
        self._set_session_fields(
            guild_id,
            session_key,
            staffing_status="sub_requested",
            sub_requested_by=str(user_id),
            confirmed_by=None,
            confirmed_at=None,
        )

    def accept_sub(self, guild_id: int, session_key: str, user_id: int) -> None:
        self._set_session_fields(
            guild_id,
            session_key,
            staffing_status="sub_confirmed",
            sub_accepted_by=str(user_id),
            assigned_user_id=str(user_id),
            confirmed_by=str(user_id),
            confirmed_at=datetime.utcnow().isoformat(),
        )

    def create_change_request(
        self, guild_id: int, request_type: str, details: dict[str, Any], requested_by: int
    ) -> int:
        now = datetime.utcnow().isoformat()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO change_requests(guild_id, request_type, status, details_json, requested_by, created_at)
                VALUES (?, ?, 'awaiting_owner_action', ?, ?, ?)
                """,
                (str(guild_id), request_type, json.dumps(details), str(requested_by), now),
            )
            return int(cur.lastrowid)

    def complete_change_request(self, guild_id: int, change_id: int, owner_id: int) -> None:
        now = datetime.utcnow().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE change_requests
                SET status='owner_marked_done', owner_completed_by=?, completed_at=?
                WHERE guild_id=? AND id=?
                """,
                (str(owner_id), now, str(guild_id), change_id),
            )

    def reject_change_request(self, guild_id: int, change_id: int, owner_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE change_requests
                SET status='rejected', owner_completed_by=?, completed_at=?
                WHERE guild_id=? AND id=?
                """,
                (str(owner_id), datetime.utcnow().isoformat(), str(guild_id), change_id),
            )

    def get_change_request(self, guild_id: int, change_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM change_requests WHERE guild_id=? AND id=?",
                (str(guild_id), change_id),
            ).fetchone()

    def _set_session_fields(self, guild_id: int, session_key: str, **fields: object) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key}=?" for key in fields)
        values = list(fields.values())
        values.extend([str(guild_id), session_key])
        with self.connect() as conn:
            conn.execute(
                f"UPDATE sessions SET {assignments} WHERE guild_id=? AND session_key=?",
                values,
            )
