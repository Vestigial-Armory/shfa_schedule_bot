from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .config import Config, load_config
from .gymdesk import GymdeskPublicScheduleClient
from .store import Store

SETTING_KEYS = {
    "owner_role_id",
    "instructor_role_id",
    "confirmation_channel_id",
    "sub_requests_channel_id",
    "owner_channel_id",
    "schedule_updates_channel_id",
    "reminder_hours",
    "escalation_hours",
    "owner_escalation_enabled",
    "timezone",
    "sync_weeks",
}


class ScheduleBot(commands.Bot):
    def __init__(self, config: Config, store: Store) -> None:
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.config = config
        self.store = store
        self.schedule_client = GymdeskPublicScheduleClient(
            config.gymdesk_base_url,
            config.gymdesk_schedule_id,
            config.default_timezone,
        )

    async def setup_hook(self) -> None:
        self.add_view(OwnerChangeView(self.store))
        await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user}")
        if not sync_loop.is_running():
            sync_loop.start(self)
        if not reminder_loop.is_running():
            reminder_loop.start(self)


def guild_required(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:
        raise RuntimeError("This command must be used in a server.")
    return interaction.guild_id


def is_truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def has_configured_role(interaction: discord.Interaction, store: Store, setting_key: str) -> bool:
    if interaction.guild_id is None or not isinstance(interaction.user, discord.Member):
        return False
    role_id = store.get_setting(interaction.guild_id, setting_key)
    if not role_id:
        return interaction.user.guild_permissions.manage_guild
    return any(str(role.id) == role_id for role in interaction.user.roles)


def can_manage_bot(interaction: discord.Interaction, store: Store) -> bool:
    return has_configured_role(interaction, store, "owner_role_id")


def can_instruct(interaction: discord.Interaction, store: Store) -> bool:
    return has_configured_role(interaction, store, "instructor_role_id") or can_manage_bot(
        interaction, store
    )


async def require_owner(interaction: discord.Interaction, store: Store) -> bool:
    if can_manage_bot(interaction, store):
        return True
    await interaction.response.send_message("You do not have permission for that.", ephemeral=True)
    return False


async def require_instructor(interaction: discord.Interaction, store: Store) -> bool:
    if can_instruct(interaction, store):
        return True
    await interaction.response.send_message("You do not have permission for that.", ephemeral=True)
    return False


@tasks.loop(minutes=15)
async def sync_loop(bot: ScheduleBot) -> None:
    for guild in bot.guilds:
        await sync_for_guild(bot, guild.id)


@tasks.loop(minutes=5)
async def reminder_loop(bot: ScheduleBot) -> None:
    for guild in bot.guilds:
        await send_due_reminders(bot, guild)


async def sync_for_guild(bot: ScheduleBot, guild_id: int) -> int:
    tz_name = bot.store.get_setting(guild_id, "timezone", bot.config.default_timezone)
    weeks = int(bot.store.get_setting(guild_id, "sync_weeks", str(bot.config.sync_weeks)) or 6)
    client = GymdeskPublicScheduleClient(
        bot.config.gymdesk_base_url,
        bot.config.gymdesk_schedule_id,
        tz_name or bot.config.default_timezone,
    )
    sessions = await client.fetch_weeks(datetime.now(ZoneInfo(tz_name or bot.config.default_timezone)), weeks)
    bot.store.upsert_sessions(guild_id, sessions)
    return len(sessions)


async def send_due_reminders(bot: ScheduleBot, guild: discord.Guild) -> None:
    tz_name = bot.store.get_setting(guild.id, "timezone", bot.config.default_timezone)
    tz = ZoneInfo(tz_name or bot.config.default_timezone)
    now = datetime.now(tz)
    horizon = now + timedelta(days=2)
    rows = bot.store.find_sessions(guild.id, now.isoformat(), horizon.isoformat())

    reminder_hours = float(bot.store.get_setting(guild.id, "reminder_hours", "24") or 24)
    escalation_hours = float(bot.store.get_setting(guild.id, "escalation_hours", "4") or 4)

    for row in rows:
        start = datetime.fromisoformat(row["start_at"])
        if row["canceled"]:
            continue

        if not row["reminder_sent_at"] and now >= start - timedelta(hours=reminder_hours):
            await post_confirmation_request(bot, guild, row)
            bot.store.mark_reminder_sent(guild.id, row["session_key"])

        if (
            not row["escalation_sent_at"]
            and row["staffing_status"] not in {"confirmed", "sub_confirmed"}
            and now >= start - timedelta(hours=escalation_hours)
        ):
            await post_unstaffed_escalation(bot, guild, row)
            bot.store.mark_escalation_sent(guild.id, row["session_key"])


async def post_confirmation_request(bot: ScheduleBot, guild: discord.Guild, row) -> None:
    channel = get_text_channel(bot, guild, "confirmation_channel_id")
    if channel is None:
        return

    assigned = f"<@{row['assigned_user_id']}>" if row["assigned_user_id"] else "unmapped instructor"
    instructors = format_instructors(row)
    await channel.send(
        f"{assigned} please confirm availability for **{row['title']}**\n"
        f"Time: {format_session_time(row)}\n"
        f"Gymdesk instructor(s): {instructors}",
        view=ConfirmView(bot.store, row["session_key"]),
    )


async def post_unstaffed_escalation(bot: ScheduleBot, guild: discord.Guild, row) -> None:
    sub_channel = get_text_channel(bot, guild, "sub_requests_channel_id")
    instructor_role_id = bot.store.get_setting(guild.id, "instructor_role_id")
    role_mention = f"<@&{instructor_role_id}>" if instructor_role_id else "Instructors"
    if sub_channel:
        await sub_channel.send(
            f"{role_mention} **No confirmed instructor** for **{row['title']}**\n"
            f"Time: {format_session_time(row)}\n"
            f"Click below to take this class.",
            view=SubRequestView(bot.store, row["session_key"]),
        )

    if is_truthy(bot.store.get_setting(guild.id, "owner_escalation_enabled", "true")):
        owner_channel = get_text_channel(bot, guild, "owner_channel_id")
        if owner_channel:
            await owner_channel.send(
                f"**Owner alert:** No confirmed instructor for **{row['title']}**\n"
                f"Time: {format_session_time(row)}\n"
                "Should a manual Gymdesk cancellation be requested?",
                view=OwnerCancelPromptView(bot.store, row["session_key"]),
            )


def get_text_channel(bot: ScheduleBot, guild: discord.Guild, setting_key: str) -> discord.TextChannel | None:
    channel_id = bot.store.get_setting(guild.id, setting_key)
    if not channel_id:
        return None
    channel = guild.get_channel(int(channel_id))
    return channel if isinstance(channel, discord.TextChannel) else None


def format_instructors(row) -> str:
    try:
        instructors = json.loads(row["instructors_json"])
    except Exception:
        instructors = []
    names = [item.get("name") for item in instructors if item.get("name")]
    return ", ".join(names) if names else "none listed"


def format_session_time(row) -> str:
    start = datetime.fromisoformat(row["start_at"])
    end = datetime.fromisoformat(row["end_at"])
    return f"{start:%a %b %-d, %-I:%M %p}–{end:%-I:%M %p}"


class ConfirmView(discord.ui.View):
    def __init__(self, store: Store, session_key: str) -> None:
        super().__init__(timeout=None)
        self.store = store
        self.session_key = session_key

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_instructor(interaction, self.store):
            return
        guild_id = guild_required(interaction)
        self.store.confirm_session(guild_id, self.session_key, interaction.user.id)
        await interaction.response.send_message("Confirmed.", ephemeral=True)

    @discord.ui.button(label="Need Sub", style=discord.ButtonStyle.danger)
    async def need_sub(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_instructor(interaction, self.store):
            return
        guild_id = guild_required(interaction)
        self.store.request_sub(guild_id, self.session_key, interaction.user.id)
        await interaction.response.send_message("Sub request opened.", ephemeral=True)
        channel_id = self.store.get_setting(guild_id, "sub_requests_channel_id")
        if channel_id and interaction.guild:
            channel = interaction.guild.get_channel(int(channel_id))
            row = self.store.get_session(guild_id, self.session_key)
            if isinstance(channel, discord.TextChannel) and row:
                await channel.send(
                    f"Sub needed for **{row['title']}**\nTime: {format_session_time(row)}",
                    view=SubRequestView(self.store, self.session_key),
                )


class SubRequestView(discord.ui.View):
    def __init__(self, store: Store, session_key: str) -> None:
        super().__init__(timeout=None)
        self.store = store
        self.session_key = session_key

    @discord.ui.button(label="Take Sub", style=discord.ButtonStyle.primary)
    async def take_sub(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_instructor(interaction, self.store):
            return
        guild_id = guild_required(interaction)
        self.store.accept_sub(guild_id, self.session_key, interaction.user.id)
        await interaction.response.send_message("You are now recorded as the sub.", ephemeral=True)
        await interaction.message.reply(f"Covered by <@{interaction.user.id}>.")


class OwnerCancelPromptView(discord.ui.View):
    def __init__(self, store: Store, session_key: str) -> None:
        super().__init__(timeout=None)
        self.store = store
        self.session_key = session_key

    @discord.ui.button(label="Request Manual Gymdesk Cancel", style=discord.ButtonStyle.danger)
    async def request_cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_owner(interaction, self.store):
            return
        guild_id = guild_required(interaction)
        row = self.store.get_session(guild_id, self.session_key)
        if not row:
            await interaction.response.send_message("Session not found.", ephemeral=True)
            return
        details = {
            "session_key": self.session_key,
            "title": row["title"],
            "time": format_session_time(row),
            "external_id": row["external_id"],
            "instruction": "Cancel or hide this exact session manually in Gymdesk.",
        }
        change_id = self.store.create_change_request(guild_id, "cancel_session", details, interaction.user.id)
        await interaction.response.send_message(
            f"Created owner action item #{change_id}.", ephemeral=True
        )
        await interaction.channel.send(format_change_request_message(change_id, "cancel_session", details), view=OwnerChangeView(self.store, change_id))

    @discord.ui.button(label="Keep Searching", style=discord.ButtonStyle.secondary)
    async def keep_searching(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_owner(interaction, self.store):
            return
        await interaction.response.send_message("No cancellation request created.", ephemeral=True)


class OwnerChangeView(discord.ui.View):
    def __init__(self, store: Store, change_id: int | None = None) -> None:
        super().__init__(timeout=None)
        self.store = store
        self.change_id = change_id

    @discord.ui.button(
        label="Mark Done in Gymdesk", style=discord.ButtonStyle.success, custom_id="owner_change_done"
    )
    async def mark_done(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_owner(interaction, self.store):
            return
        change_id = self.change_id or parse_change_id(interaction.message.content if interaction.message else "")
        if change_id is None:
            await interaction.response.send_message("Could not determine change request ID.", ephemeral=True)
            return
        guild_id = guild_required(interaction)
        self.store.complete_change_request(guild_id, change_id, interaction.user.id)
        await interaction.response.send_message("Marked done in Gymdesk.", ephemeral=True)
        await maybe_announce_change(interaction, self.store, change_id)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.secondary, custom_id="owner_change_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_owner(interaction, self.store):
            return
        change_id = self.change_id or parse_change_id(interaction.message.content if interaction.message else "")
        if change_id is None:
            await interaction.response.send_message("Could not determine change request ID.", ephemeral=True)
            return
        self.store.reject_change_request(guild_required(interaction), change_id, interaction.user.id)
        await interaction.response.send_message("Rejected.", ephemeral=True)


def parse_change_id(text: str) -> int | None:
    marker = "Change request #"
    if marker not in text:
        return None
    rest = text.split(marker, 1)[1]
    digits = ""
    for char in rest:
        if char.isdigit():
            digits += char
        else:
            break
    return int(digits) if digits else None


def format_change_request_message(change_id: int, request_type: str, details: dict) -> str:
    lines = [f"**Change request #{change_id}**", f"Type: `{request_type}`"]
    for key, value in details.items():
        lines.append(f"**{key}:** {value}")
    lines.append("\nOwner action required: make this change manually in Gymdesk, then click below.")
    return "\n".join(lines)


async def maybe_announce_change(interaction: discord.Interaction, store: Store, change_id: int) -> None:
    if not interaction.guild:
        return
    row = store.get_change_request(interaction.guild.id, change_id)
    channel_id = store.get_setting(interaction.guild.id, "schedule_updates_channel_id")
    if not row or not channel_id:
        return
    channel = interaction.guild.get_channel(int(channel_id))
    if isinstance(channel, discord.TextChannel):
        details = json.loads(row["details_json"])
        await channel.send(
            f"Schedule update completed in Gymdesk: **{row['request_type']}**\n"
            + "\n".join(f"**{k}:** {v}" for k, v in details.items() if k != "instruction")
        )


def add_commands(bot: ScheduleBot) -> None:
    @bot.tree.command(name="settings_view", description="View bot settings for this server")
    async def settings_view(interaction: discord.Interaction) -> None:
        if not await require_owner(interaction, bot.store):
            return
        rows = bot.store.list_settings(guild_required(interaction))
        body = "\n".join(f"`{row['key']}` = `{row['value']}`" for row in rows) or "No settings."
        await interaction.response.send_message(body, ephemeral=True)

    @bot.tree.command(name="settings_set", description="Set a bot setting")
    @app_commands.describe(key="Setting key", value="Setting value")
    async def settings_set(interaction: discord.Interaction, key: str, value: str) -> None:
        if not await require_owner(interaction, bot.store):
            return
        if key not in SETTING_KEYS:
            await interaction.response.send_message(
                "Unknown setting. Valid keys: " + ", ".join(sorted(SETTING_KEYS)), ephemeral=True
            )
            return
        bot.store.set_setting(guild_required(interaction), key, value)
        await interaction.response.send_message(f"Set `{key}`.", ephemeral=True)

    @bot.tree.command(name="instructor_map", description="Map a Gymdesk instructor name to a Discord member")
    async def instructor_map(
        interaction: discord.Interaction, gymdesk_name: str, member: discord.Member
    ) -> None:
        if not await require_owner(interaction, bot.store):
            return
        bot.store.map_instructor(guild_required(interaction), gymdesk_name, member.id)
        await interaction.response.send_message(
            f"Mapped `{gymdesk_name}` to {member.mention}.", ephemeral=True
        )

    @bot.tree.command(name="sync_schedule", description="Fetch latest schedule from Gymdesk public schedule")
    async def sync_schedule(interaction: discord.Interaction) -> None:
        if not await require_owner(interaction, bot.store):
            return
        await interaction.response.defer(ephemeral=True)
        count = await sync_for_guild(bot, guild_required(interaction))
        await interaction.followup.send(f"Synced {count} schedule sessions.", ephemeral=True)

    @bot.tree.command(name="schedule", description="Show schedule from bot database")
    @app_commands.describe(period="day, week, or month", date="Anchor date as YYYY-MM-DD")
    async def schedule(
        interaction: discord.Interaction,
        period: Literal["day", "week", "month"],
        date: str,
    ) -> None:
        guild_id = guild_required(interaction)
        tz_name = bot.store.get_setting(guild_id, "timezone", bot.config.default_timezone)
        tz = ZoneInfo(tz_name or bot.config.default_timezone)
        start = datetime.fromisoformat(date).replace(tzinfo=tz)
        if period == "day":
            end = start + timedelta(days=1)
        elif period == "week":
            end = start + timedelta(days=7)
        else:
            end = start + timedelta(days=31)
        rows = bot.store.find_sessions(guild_id, start.isoformat(), end.isoformat())
        chunks = format_schedule_rows(rows)
        await interaction.response.send_message(chunks[0] if chunks else "No sessions found.", ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    @bot.tree.command(name="need_sub", description="Request a substitute for a session key")
    async def need_sub(interaction: discord.Interaction, session_key: str) -> None:
        if not await require_instructor(interaction, bot.store):
            return
        guild_id = guild_required(interaction)
        row = bot.store.get_session(guild_id, session_key)
        if not row:
            await interaction.response.send_message("Session not found.", ephemeral=True)
            return
        bot.store.request_sub(guild_id, session_key, interaction.user.id)
        await interaction.response.send_message("Sub request opened.", ephemeral=True)
        channel = get_text_channel(bot, interaction.guild, "sub_requests_channel_id") if interaction.guild else None
        if channel:
            await channel.send(
                f"Sub needed for **{row['title']}**\nTime: {format_session_time(row)}",
                view=SubRequestView(bot.store, session_key),
            )

    @bot.tree.command(name="gymdesk_change", description="Create an owner-mediated Gymdesk change request")
    @app_commands.describe(
        request_type="add_event, add_recurring, cancel_session, or cancel_series",
        details="Plain-English details for the owner to enter in Gymdesk",
        announce="Whether to announce when marked done",
    )
    async def gymdesk_change(
        interaction: discord.Interaction,
        request_type: Literal["add_event", "add_recurring", "cancel_session", "cancel_series"],
        details: str,
        announce: bool = True,
    ) -> None:
        if not await require_owner(interaction, bot.store):
            return
        guild_id = guild_required(interaction)
        detail_obj = {"details": details, "announce": announce}
        change_id = bot.store.create_change_request(guild_id, request_type, detail_obj, interaction.user.id)
        await interaction.response.send_message(f"Created change request #{change_id}.", ephemeral=True)
        owner_channel = get_text_channel(bot, interaction.guild, "owner_channel_id") if interaction.guild else None
        if owner_channel:
            await owner_channel.send(
                format_change_request_message(change_id, request_type, detail_obj),
                view=OwnerChangeView(bot.store, change_id),
            )


def format_schedule_rows(rows) -> list[str]:
    if not rows:
        return []
    lines = ["**Schedule**"]
    for row in rows:
        status = row["staffing_status"]
        instructor = f"<@{row['assigned_user_id']}>" if row["assigned_user_id"] else format_instructors(row)
        cap = ""
        if row["capacity"] is not None:
            cap = f" — bookings {row['booked'] or 0}/{row['capacity']}"
        lines.append(
            f"`{row['session_key']}`\n"
            f"**{row['title']}** — {format_session_time(row)}\n"
            f"Instructor: {instructor} — status: `{status}`{cap}"
        )
    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 2 > 1900:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def main() -> None:
    config = load_config()
    store = Store(config.db_path)
    store.init()
    bot = ScheduleBot(config, store)
    add_commands(bot)
    bot.run(config.discord_token)


if __name__ == "__main__":
    main()
