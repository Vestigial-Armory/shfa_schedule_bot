# shfa_schedule_bot

Discord bot for SHFA instructor coverage and semi-automated schedule operations.

The bot treats the public Gymdesk schedule as a read-only source and handles staffing inside Discord. When a Gymdesk write is needed, the bot posts an owner action item with the exact change to make. An owner manually performs the change in Gymdesk, then clicks **Mark Done in Gymdesk**. The bot records that internally and can optionally announce it.

## Current capabilities

- Polls `https://sachema.com/schedule/getevents` using `schedule_id=1889`.
- Parses Gymdesk event cards from the JSON response's `html` field.
- Extracts event ID, date/time, duration, title, instructor IDs/names, capacity, booked count, waitlist count, recurring rule, and cancellation flags from `data-event-info`.
- Stores normalized sessions in SQLite.
- Maps Gymdesk instructor names to Discord members.
- Sends instructor confirmation reminders before class.
- Lets instructors confirm availability or request a sub.
- Lets eligible instructors accept sub requests.
- Escalates unstaffed classes to instructors and optionally owners.
- Creates owner-mediated Gymdesk change requests for adding events/classes or cancelling sessions/series.
- Supports daily, weekly, and monthly schedule output.
- Stores settings per Discord server.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Edit `.env` and set `DISCORD_TOKEN`.

In the Discord Developer Portal, enable these bot intents:

- Server Members Intent
- Message Content Intent is not required for slash-command operation

Invite the bot with scopes:

- `bot`
- `applications.commands`

Suggested bot permissions:

- Send Messages
- Use Slash Commands
- Mention Everyone only if you intend to let it mention roles
- Read Message History

Run:

```bash
shfa-schedule-bot
```

Or:

```bash
python -m shfa_schedule_bot.bot
```

## First-run configuration

Run these slash commands as an owner/admin:

```text
/settings_set key:owner_role_id value:<role id>
/settings_set key:instructor_role_id value:<role id>
/settings_set key:confirmation_channel_id value:<channel id>
/settings_set key:sub_requests_channel_id value:<channel id>
/settings_set key:owner_channel_id value:<channel id>
/settings_set key:schedule_updates_channel_id value:<channel id>
/settings_set key:reminder_hours value:24
/settings_set key:escalation_hours value:4
/settings_set key:owner_escalation_enabled value:true
```

Map Gymdesk instructors to Discord members:

```text
/instructor_map gymdesk_name:"John Smith" member:@jsmith
```

Force a schedule sync:

```text
/sync_schedule
```

Show schedule:

```text
/schedule period:week date:2026-07-05
```

## Important limitation

This bot does not write to Gymdesk directly. Gymdesk changes are owner-mediated:

1. Bot posts the exact requested change in the owner channel.
2. Owner performs the change manually in Gymdesk.
3. Owner clicks **Mark Done in Gymdesk**.
4. Bot records and optionally announces the completed change.

## Development

```bash
pip install -e '.[dev]'
ruff check .
pytest
```
