"""Himyar Economy — shared helpers."""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional

import discord

from .strings import StringBag

DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")
UNIT_SECONDS = {
    "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
    "day": 86400, "days": 86400, "d": 86400,
    "week": 604800, "weeks": 604800, "wk": 604800, "wks": 604800, "w": 604800,
}


class DurationError(ValueError):
    pass


def parse_duration(text: str, minimum: int = 60, maximum: int = 365 * 86400) -> int:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        raise DurationError("Tell me how long — for example `7d`, `24h`, or `1w`.")
    total, found, consumed = 0.0, False, 0
    for match in DURATION_RE.finditer(cleaned):
        if match.start() > consumed + 1:
            break
        unit = match.group(2)
        if unit not in UNIT_SECONDS:
            break
        total += float(match.group(1)) * UNIT_SECONDS[unit]
        found, consumed = True, match.end()
    if not found:
        raise DurationError(
            f"I couldn't read “{text.strip()}” as a length of time. Try `7d`, `24h`, or `1w`."
        )
    seconds = int(total)
    if seconds < minimum:
        raise DurationError(f"That's too short — the minimum is {human_duration(minimum)}.")
    if seconds > maximum:
        raise DurationError(f"That's too long — the maximum is {human_duration(maximum)}.")
    return seconds


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    parts = []
    for label, size in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count, seconds = divmod(seconds, size)
            parts.append(f"{count} {label}{'s' if count != 1 else ''}")
    if not parts:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    return " ".join(parts[:2])


def ts(moment: dt.datetime, style: str = "R") -> str:
    """Discord dynamic timestamp — every viewer sees their own local time."""
    return f"<t:{int(moment.timestamp())}:{style}>"


def in_seconds(seconds: int) -> str:
    return ts(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=max(0, int(seconds))), "R")


def coins(amount: int, settings: dict) -> str:
    emoji = settings.get("currency_emoji") or "🪙"
    name = settings.get("currency_name") or "Coins"
    return f"{emoji} **{int(amount):,}** {name}"


def base_embed(settings: dict, title: str | None = None,
               description: str | None = None) -> discord.Embed:
    color = settings.get("embed_color") or 0x1E90FF
    return discord.Embed(title=title, description=description,
                         color=discord.Color(int(color)),
                         timestamp=discord.utils.utcnow())


def ok_embed(settings: dict, description: str, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=f"✅ {description}",
                         color=discord.Color(0x2ECC71))


def err_embed(description: str, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=f"❌ {description}",
                         color=discord.Color(0xE74C3C))


def is_staff(member: discord.Member, settings: dict) -> bool:
    perms = getattr(member, "guild_permissions", None)
    if perms and (perms.manage_guild or perms.administrator):
        return True
    role_id = settings.get("staff_role_id")
    if not role_id:
        return False
    return any(r.id == int(role_id) for r in getattr(member, "roles", []))


def account_age_days(user: discord.abc.User) -> float:
    created = getattr(user, "created_at", None)
    if created is None:
        return 0.0
    return (discord.utils.utcnow() - created).total_seconds() / 86400.0


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def medal(position: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(position, f"`#{position}`")


async def safe_respond(interaction: discord.Interaction, *args, **kwargs) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(*args, **kwargs)
        else:
            await interaction.response.send_message(*args, **kwargs)
    except discord.HTTPException:
        pass


def bag_from(overrides: dict) -> StringBag:
    return StringBag(overrides)
