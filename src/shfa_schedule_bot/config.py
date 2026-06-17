from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    discord_token: str
    db_path: Path
    default_timezone: str
    gymdesk_base_url: str
    gymdesk_schedule_id: str
    sync_weeks: int


def load_config() -> Config:
    load_dotenv()
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required")

    return Config(
        discord_token=token,
        db_path=Path(os.environ.get("BOT_DB_PATH", "./shfa_schedule_bot.sqlite3")),
        default_timezone=os.environ.get("DEFAULT_TIMEZONE", "America/Los_Angeles"),
        gymdesk_base_url=os.environ.get("GYMDESK_BASE_URL", "https://sachema.com").rstrip("/"),
        gymdesk_schedule_id=os.environ.get("GYMDESK_SCHEDULE_ID", "1889"),
        sync_weeks=int(os.environ.get("SYNC_WEEKS", "6")),
    )
