"""
Himyar Economy — configuration, staff tools and the audit log.

No dashboard, no website. Every knob is a slash command, and every coin that
moves is recorded so staff can prove what happened.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core import util, views
from core.db import parse_ts
from core.strings import DEFAULT_STRINGS, PLACEHOLDERS, StringBag
from core.trivia_bank import BUILTIN_QUESTIONS

log = logging.getLogger(__name__)

MANAGE = discord.Permissions(manage_guild=True)
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_color(raw: str) -> Optional[int]:
    match = HEX_RE.match((raw or "").strip())
    if match:
        return int(match.group(1), 16)
    named = {"blurple": 0x5865F2, "green": 0x2ECC71, "red": 0xE74C3C, "blue": 0x1E90FF,
             "gold": 0xF1C40F, "purple": 0x9B59B6, "black": 0x2B2D31, "white": 0xFFFFFF,
             "orange": 0xE67E22, "teal": 0x1ABC9C, "pink": 0xE91E63}
    return named.get((raw or "").strip().lower())


class MessageModal(discord.ui.Modal, title="Edit message"):
    def __init__(self, cog: "Config", key: str, current: str):
        super().__init__(timeout=600)
        self.cog = cog
        self.key = key
        self.field = discord.ui.TextInput(
            label=util.truncate(key, 45), style=discord.TextStyle.paragraph,
            default=current[:4000], max_length=2000, required=True,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.bot.db.set_string(interaction.guild.id, self.key, str(self.field.value))
        settings = await self.cog.bot.db.get_guild(interaction.guild.id)
        await interaction.response.send_message(
            embed=util.ok_embed(settings, f"`{self.key}` updated."), ephemeral=True
        )


class Config(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    config = app_commands.Group(name="config", description="Configure the economy",
                                guild_only=True, default_permissions=MANAGE)
    messages_group = app_commands.Group(name="messages", description="Edit any text the bot sends",
                                        parent=config)
    multiplier_group = app_commands.Group(name="multiplier", description="Bonus coins for roles",
                                          parent=config)
    eco = app_commands.Group(name="eco", description="Staff economy tools",
                             guild_only=True, default_permissions=MANAGE)

    async def guard(self, interaction: discord.Interaction) -> Optional[dict]:
        settings = await self.bot.db.get_guild(interaction.guild.id)
        if not util.is_staff(interaction.user, settings):
            bag = StringBag(await self.bot.db.get_strings(interaction.guild.id))
            await interaction.response.send_message(
                embed=util.err_embed(bag.get("staff_only")), ephemeral=True
            )
            return None
        return settings

    async def key_autocomplete(self, interaction: discord.Interaction, current: str):
        current = (current or "").lower()
        return [app_commands.Choice(name=k, value=k)
                for k in DEFAULT_STRINGS if current in k.lower()][:25]

    # ─── /setup ───────────────────────────────────────────────────────────────
    @app_commands.command(name="setup", description="Set up the economy on this server")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        staff_role="Role allowed to use staff commands (server managers always can)",
        log_channel="Where every coin movement is logged",
        currency_name="What the currency is called (default: Coins)",
        currency_emoji="Emoji shown next to amounts (default: 🪙)",
        start_balance="Coins a member starts with",
    )
    async def setup_command(
        self, interaction: discord.Interaction,
        staff_role: Optional[discord.Role] = None,
        log_channel: Optional[discord.TextChannel] = None,
        currency_name: Optional[str] = None,
        currency_emoji: Optional[str] = None,
        start_balance: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
    ) -> None:
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)

        warnings = []
        if log_channel is not None:
            perms = log_channel.permissions_for(guild.me)
            if not (perms.send_messages and perms.embed_links):
                warnings.append(f"I can't post in {log_channel.mention}.")
        if not guild.me.guild_permissions.manage_roles:
            warnings.append("I don't have **Manage Roles**, so shop role items won't work.")

        updates = {
            "staff_role_id": staff_role.id if staff_role else None,
            "log_channel_id": log_channel.id if log_channel else None,
            "setup_complete": 1,
        }
        if currency_name:
            updates["currency_name"] = currency_name.strip()[:30]
        if currency_emoji:
            updates["currency_emoji"] = currency_emoji.strip()[:10]
        if start_balance is not None:
            updates["start_balance"] = int(start_balance)
        await self.bot.db.update_guild(guild.id, **updates)
        settings = await self.bot.db.get_guild(guild.id)

        embed = util.base_embed(
            settings, title="✅ Himyar Economy is set up",
            description="Members start earning right away. Nothing here is shared with "
                        "any other server.",
        )
        embed.add_field(name="Currency",
                        value=f"{settings['currency_emoji']} {settings['currency_name']}")
        embed.add_field(name="Staff role",
                        value=staff_role.mention if staff_role else "*managers only*")
        embed.add_field(name="Audit log",
                        value=log_channel.mention if log_channel else "*not set*")
        embed.add_field(
            name="Earning",
            value=f"{settings['chat_min']}–{settings['chat_max']} per message\n"
                  f"{settings['voice_per_minute']} per voice minute\n"
                  f"{settings['daily_amount']} daily · {settings['work_min']}–{settings['work_max']} work",
        )
        embed.add_field(
            name="Robbery",
            value=f"{settings['rob_success_percent']:g}% success\n"
                  f"takes up to {settings['rob_max_percent']:g}% of wallet\n"
                  f"{util.human_duration(settings['rob_cooldown'])} cooldown",
        )
        embed.add_field(name="Daily earn cap", value=f"{settings['daily_earn_cap']:,}")
        if warnings:
            embed.add_field(name="⚠️", value="\n".join(f"• {w}" for w in warnings), inline=False)
        embed.add_field(
            name="Next steps",
            value="`/shop add` — give members something worth buying\n"
                  "`/config robbery` — tune it before members find the edges\n"
                  "`/trivia add` — your own questions on top of the built-in bank\n"
                  "`/config view` — everything at a glance",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /config view ─────────────────────────────────────────────────────────
    @config.command(name="view", description="Show every setting for this server")
    async def config_view(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)
        s = await self.bot.db.get_guild(guild.id)
        circulating = await self.bot.db.total_in_circulation(guild.id)
        holders = await self.bot.db.rich_count(guild.id)
        items = await self.bot.db.get_items(guild.id, enabled_only=False)
        custom_q = await self.bot.db.count_custom_questions(guild.id)

        def role(key: str) -> str:
            value = s.get(key)
            if not value:
                return "*not set*"
            found = guild.get_role(int(value))
            return found.mention if found else f"`{value}` *(deleted)*"

        def channel(key: str) -> str:
            value = s.get(key)
            if not value:
                return "*not set*"
            found = guild.get_channel(int(value))
            return found.mention if found else f"`{value}` *(deleted)*"

        embed = util.base_embed(settings=s, title="⚙️ Economy configuration")
        embed.add_field(name="Currency", value=f"{s['currency_emoji']} {s['currency_name']}")
        embed.add_field(name="Staff role", value=role("staff_role_id"))
        embed.add_field(name="Audit log", value=channel("log_channel_id"))
        embed.add_field(
            name="Passive earning",
            value=("chat: " + ("on" if s["chat_enabled"] else "**off**")
                   + f" ({s['chat_min']}–{s['chat_max']}, {s['chat_cooldown']}s)\n"
                   + "voice: " + ("on" if s["voice_enabled"] else "**off**")
                   + f" ({s['voice_per_minute']}/min)"),
        )
        embed.add_field(
            name="Daily & work",
            value=f"daily {s['daily_amount']} (+{s['daily_streak_bonus']}/day, "
                  f"cap {s['daily_streak_cap']})\n"
                  + ("work " + ("on" if s["work_enabled"] else "**off**")
                     + f" {s['work_min']}–{s['work_max']} every "
                       f"{util.human_duration(s['work_cooldown'])}"),
        )
        embed.add_field(name="Daily earn cap",
                        value=f"{s['daily_earn_cap']:,}" if s["daily_earn_cap"] else "unlimited")
        embed.add_field(
            name="Transfers",
            value=("on" if s["pay_enabled"] else "**off**")
                  + f"\nmin account age {s['pay_min_account_days']}d"
                  + f"\ndaily limit {s['pay_daily_limit']:,}"
                  + (f"\nfee {s['pay_tax_percent']:g}%" if s["pay_tax_percent"] else ""),
        )
        embed.add_field(
            name="Robbery",
            value=("on" if s["rob_enabled"] else "**off**")
                  + f"\n{s['rob_success_percent']:g}% success, "
                    f"≤{s['rob_max_percent']:g}% of wallet"
                  + f"\nfine {s['rob_fine_percent']:g}%"
                  + f"\ncooldown {util.human_duration(s['rob_cooldown'])}"
                  + f"\nvictim safe {util.human_duration(s['rob_victim_protect'])}",
        )
        embed.add_field(
            name="Games",
            value=("on" if s["games_enabled"] else "**off**")
                  + f"\ntrivia {s['trivia_reward']} · guess {s['guess_cost']} entry"
                  + f"\nheist {s['heist_base']} base, {s['heist_min_players']} needed"
                  + f"\n{len(BUILTIN_QUESTIONS)} built-in + {custom_q} custom questions",
        )
        embed.add_field(name="Bank", value="on" if s["bank_enabled"] else "**off**")
        embed.add_field(name="Shop items", value=str(len(items)))
        embed.add_field(name="In circulation",
                        value=f"{circulating:,} across {holders} members")
        mults = await self.bot.db.get_role_multipliers(guild.id)
        embed.add_field(
            name="Role multipliers",
            value=("\n".join(f"<@&{rid}> → ×{m:g}" for rid, m in mults.items())
                   if mults else "*none — `/config multiplier add`*"),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /config multiplier … ─────────────────────────────────────────────────
    @multiplier_group.command(name="add", description="Give a role bonus coins")
    @app_commands.describe(role="The role to reward",
                           multiplier="e.g. 1.5 for 50% more coins")
    async def multiplier_add(self, interaction: discord.Interaction, role: discord.Role,
                             multiplier: app_commands.Range[float, 1.1, 10.0]) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        mults = await self.bot.db.get_role_multipliers(interaction.guild.id)
        mults[role.id] = float(multiplier)
        await self.bot.db.set_role_multipliers(interaction.guild.id, mults)
        await interaction.response.send_message(
            embed=util.ok_embed(
                settings,
                f"{role.mention} now earns **×{multiplier:g}** coins.\n\n"
                "Applies to chat, voice, `/daily` and `/work`. Games, robbery and "
                "transfers are untouched, so nobody can multiply coins that came "
                "out of another member's wallet.\n"
                "If someone holds two boosted roles, the higher one wins.",
            ),
            ephemeral=True,
        )

    @multiplier_group.command(name="remove", description="Remove a role's bonus coins")
    @app_commands.describe(role="The role to stop rewarding")
    async def multiplier_remove(self, interaction: discord.Interaction,
                                role: discord.Role) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        mults = await self.bot.db.get_role_multipliers(interaction.guild.id)
        if role.id not in mults:
            return await interaction.response.send_message(
                embed=util.err_embed(f"{role.mention} has no multiplier."), ephemeral=True
            )
        mults.pop(role.id, None)
        await self.bot.db.set_role_multipliers(interaction.guild.id, mults)
        await interaction.response.send_message(
            embed=util.ok_embed(settings, f"{role.mention} earns at the normal rate again."),
            ephemeral=True,
        )

    @multiplier_group.command(name="list", description="Roles with bonus coins")
    async def multiplier_list(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        mults = await self.bot.db.get_role_multipliers(interaction.guild.id)
        if not mults:
            return await interaction.response.send_message(
                embed=util.base_embed(
                    settings=settings,
                    description="No role multipliers yet. `/config multiplier add` to "
                                "reward boosters or VIPs.",
                ),
                ephemeral=True,
            )
        embed = util.base_embed(settings=settings, title="⚡ Role multipliers")
        embed.description = "\n".join(f"<@&{rid}> → ×{m:g}" for rid, m in mults.items())
        embed.set_footer(text="Highest wins · chat, voice, /daily and /work only")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── /config earning ──────────────────────────────────────────────────────
    @config.command(name="earning", description="Chat, voice, daily and work settings")
    @app_commands.describe(
        chat="Earn coins from messages", chat_min="Lowest per message",
        chat_max="Highest per message", chat_cooldown="Seconds between message rewards",
        voice="Earn coins from voice", voice_per_minute="Coins per minute in a call",
        daily="Coins from /daily", daily_bonus="Extra per streak day",
        daily_cap="Most the streak bonus can add",
        work="Allow /work", work_min="Lowest /work payout", work_max="Highest /work payout",
        work_cooldown="Seconds between /work uses",
        earn_cap="Most one member can earn per day from all sources (0 = unlimited)",
    )
    async def config_earning(
        self, interaction: discord.Interaction,
        chat: Optional[bool] = None,
        chat_min: Optional[app_commands.Range[int, 0, 10_000]] = None,
        chat_max: Optional[app_commands.Range[int, 0, 10_000]] = None,
        chat_cooldown: Optional[app_commands.Range[int, 0, 3600]] = None,
        voice: Optional[bool] = None,
        voice_per_minute: Optional[app_commands.Range[int, 0, 1000]] = None,
        daily: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
        daily_bonus: Optional[app_commands.Range[int, 0, 100_000]] = None,
        daily_cap: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
        work: Optional[bool] = None,
        work_min: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
        work_max: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
        work_cooldown: Optional[app_commands.Range[int, 0, 86400]] = None,
        earn_cap: Optional[app_commands.Range[int, 0, 10_000_000]] = None,
    ) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        mapping = {
            "chat_enabled": None if chat is None else int(chat),
            "chat_min": chat_min, "chat_max": chat_max, "chat_cooldown": chat_cooldown,
            "voice_enabled": None if voice is None else int(voice),
            "voice_per_minute": voice_per_minute,
            "daily_amount": daily, "daily_streak_bonus": daily_bonus,
            "daily_streak_cap": daily_cap,
            "work_enabled": None if work is None else int(work),
            "work_min": work_min, "work_max": work_max, "work_cooldown": work_cooldown,
            "daily_earn_cap": earn_cap,
        }
        updates = {k: int(v) for k, v in mapping.items() if v is not None}
        if not updates:
            return await interaction.response.send_message(
                embed=util.err_embed("Give me at least one thing to change."), ephemeral=True
            )
        low = updates.get("chat_min", settings["chat_min"])
        high = updates.get("chat_max", settings["chat_max"])
        if low > high:
            return await interaction.response.send_message(
                embed=util.err_embed(f"Chat minimum ({low}) can't be above maximum ({high})."),
                ephemeral=True,
            )
        low = updates.get("work_min", settings["work_min"])
        high = updates.get("work_max", settings["work_max"])
        if low > high:
            return await interaction.response.send_message(
                embed=util.err_embed(f"Work minimum ({low}) can't be above maximum ({high})."),
                ephemeral=True,
            )
        await self.bot.db.update_guild(interaction.guild.id, **updates)
        await interaction.response.send_message(
            embed=util.ok_embed(settings, "Earning updated. `/config view` to check."),
            ephemeral=True,
        )

    # ─── /config transfers and robbery ────────────────────────────────────────
    @config.command(name="transfers", description="/pay settings and anti-alt guards")
    @app_commands.describe(
        enabled="Allow /pay", min_account_days="Minimum Discord account age to send",
        daily_limit="Most one member can send per day (0 = unlimited)",
        fee_percent="Percentage taken out of each transfer",
        bank="Allow /deposit and /withdraw",
    )
    async def config_transfers(
        self, interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        min_account_days: Optional[app_commands.Range[int, 0, 365]] = None,
        daily_limit: Optional[app_commands.Range[int, 0, 100_000_000]] = None,
        fee_percent: Optional[app_commands.Range[float, 0, 50]] = None,
        bank: Optional[bool] = None,
    ) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        updates: dict = {}
        if enabled is not None:
            updates["pay_enabled"] = int(enabled)
        if min_account_days is not None:
            updates["pay_min_account_days"] = int(min_account_days)
        if daily_limit is not None:
            updates["pay_daily_limit"] = int(daily_limit)
        if fee_percent is not None:
            updates["pay_tax_percent"] = float(fee_percent)
        if bank is not None:
            updates["bank_enabled"] = int(bank)
        if not updates:
            return await interaction.response.send_message(
                embed=util.err_embed("Give me at least one thing to change."), ephemeral=True
            )
        await self.bot.db.update_guild(interaction.guild.id, **updates)
        note = ""
        if updates.get("pay_min_account_days") == 0:
            note = ("\n\n⚠️ With no minimum account age, alt accounts can funnel coins "
                    "into one balance.")
        await interaction.response.send_message(
            embed=util.ok_embed(settings, "Transfer settings updated." + note), ephemeral=True
        )

    @config.command(name="robbery", description="Tune /rob — this is the one that causes arguments")
    @app_commands.describe(
        enabled="Allow /rob",
        success_percent="Chance a robbery works",
        max_percent="Most it can take, as a share of the victim's wallet",
        fine_percent="What a failed robbery costs the robber",
        cooldown_hours="Hours between robbery attempts",
        victim_protect_hours="Hours a victim can't be robbed again",
        min_wallet="Victim must be carrying at least this much",
    )
    async def config_robbery(
        self, interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        success_percent: Optional[app_commands.Range[float, 0, 100]] = None,
        max_percent: Optional[app_commands.Range[float, 1, 100]] = None,
        fine_percent: Optional[app_commands.Range[float, 0, 100]] = None,
        cooldown_hours: Optional[app_commands.Range[float, 0, 168]] = None,
        victim_protect_hours: Optional[app_commands.Range[float, 0, 168]] = None,
        min_wallet: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
    ) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        updates: dict = {}
        if enabled is not None:
            updates["rob_enabled"] = int(enabled)
        if success_percent is not None:
            updates["rob_success_percent"] = float(success_percent)
        if max_percent is not None:
            updates["rob_max_percent"] = float(max_percent)
        if fine_percent is not None:
            updates["rob_fine_percent"] = float(fine_percent)
        if cooldown_hours is not None:
            updates["rob_cooldown"] = int(float(cooldown_hours) * 3600)
        if victim_protect_hours is not None:
            updates["rob_victim_protect"] = int(float(victim_protect_hours) * 3600)
        if min_wallet is not None:
            updates["rob_min_victim_wallet"] = int(min_wallet)
        if not updates:
            return await interaction.response.send_message(
                embed=util.err_embed("Give me at least one thing to change."), ephemeral=True
            )
        await self.bot.db.update_guild(interaction.guild.id, **updates)

        fresh = await self.bot.db.get_guild(interaction.guild.id)
        note = ""
        if float(fresh["rob_success_percent"]) >= 60 or float(fresh["rob_max_percent"]) >= 50:
            note = ("\n\n⚠️ That's aggressive. High success rates plus a big share of the "
                    "wallet is where robbery stops being a game and starts being a "
                    "complaint in your tickets.")
        await interaction.response.send_message(
            embed=util.ok_embed(
                settings,
                f"Robbery: **{fresh['rob_success_percent']:g}%** success, takes up to "
                f"**{fresh['rob_max_percent']:g}%** of a wallet, fine "
                f"**{fresh['rob_fine_percent']:g}%**, cooldown "
                f"**{util.human_duration(fresh['rob_cooldown'])}**." + note,
            ),
            ephemeral=True,
        )

    @config.command(name="games", description="Game settings")
    @app_commands.describe(
        enabled="Allow games", trivia_reward="Coins for a correct trivia answer",
        trivia_seconds="How long a trivia round runs",
        guess_cost="Cost per guess", guess_max="Highest number in guess-the-number",
        duels="Allow duels", duel_max_stake="Biggest stake in a duel",
        heist_base="Base heist payout per person",
        heist_min_players="People needed for a heist to go ahead",
        heist_cooldown_hours="Hours between heists",
    )
    async def config_games(
        self, interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        trivia_reward: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
        trivia_seconds: Optional[app_commands.Range[int, 10, 300]] = None,
        guess_cost: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
        guess_max: Optional[app_commands.Range[int, 10, 10_000]] = None,
        duels: Optional[bool] = None,
        duel_max_stake: Optional[app_commands.Range[int, 0, 100_000_000]] = None,
        heist_base: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
        heist_min_players: Optional[app_commands.Range[int, 2, 50]] = None,
        heist_cooldown_hours: Optional[app_commands.Range[float, 0, 168]] = None,
    ) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        mapping = {
            "games_enabled": None if enabled is None else int(enabled),
            "trivia_reward": trivia_reward, "trivia_seconds": trivia_seconds,
            "guess_cost": guess_cost, "guess_max": guess_max,
            "duel_enabled": None if duels is None else int(duels),
            "duel_max_stake": duel_max_stake,
            "heist_base": heist_base, "heist_min_players": heist_min_players,
        }
        updates = {k: int(v) for k, v in mapping.items() if v is not None}
        if heist_cooldown_hours is not None:
            updates["heist_cooldown"] = int(float(heist_cooldown_hours) * 3600)
        if not updates:
            return await interaction.response.send_message(
                embed=util.err_embed("Give me at least one thing to change."), ephemeral=True
            )
        await self.bot.db.update_guild(interaction.guild.id, **updates)
        await interaction.response.send_message(
            embed=util.ok_embed(settings, "Game settings updated."), ephemeral=True
        )

    @config.command(name="currency", description="Name, emoji, colour and starting balance")
    @app_commands.describe(name="What the currency is called", emoji="Emoji shown next to amounts",
                           start_balance="Coins a new member starts with",
                           color="Hex like #1E90FF, or a name like blurple")
    async def config_currency(
        self, interaction: discord.Interaction,
        name: Optional[str] = None, emoji: Optional[str] = None,
        start_balance: Optional[app_commands.Range[int, 0, 1_000_000]] = None,
        color: Optional[str] = None,
    ) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        updates: dict = {}
        if name:
            updates["currency_name"] = name.strip()[:30]
        if emoji:
            updates["currency_emoji"] = emoji.strip()[:10]
        if start_balance is not None:
            updates["start_balance"] = int(start_balance)
        if color:
            parsed = parse_color(color)
            if parsed is None:
                return await interaction.response.send_message(
                    embed=util.err_embed("That colour didn't parse. Try `#1E90FF`."),
                    ephemeral=True,
                )
            updates["embed_color"] = parsed
        if not updates:
            return await interaction.response.send_message(
                embed=util.err_embed("Give me at least one thing to change."), ephemeral=True
            )
        await self.bot.db.update_guild(interaction.guild.id, **updates)
        fresh = await self.bot.db.get_guild(interaction.guild.id)
        await interaction.response.send_message(
            embed=util.ok_embed(
                fresh, f"Currency is now {fresh['currency_emoji']} **{fresh['currency_name']}**."
            ),
            ephemeral=True,
        )

    # ─── /eco staff tools ─────────────────────────────────────────────────────
    @eco.command(name="give", description="Give a member coins")
    @app_commands.describe(member="Who", amount="How many", reason="Why (recorded in the log)")
    async def eco_give(self, interaction: discord.Interaction, member: discord.Member,
                       amount: app_commands.Range[int, 1, 100_000_000],
                       reason: Optional[str] = None) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.db.credit(
            interaction.guild.id, member.id, int(amount), kind="staff_give",
            note=f"by {interaction.user}: {reason or 'no reason given'}",
            target_id=interaction.user.id,
        )
        await interaction.followup.send(
            embed=util.ok_embed(
                settings, f"Gave {util.coins(int(amount), settings)} to {member.mention}."
            ),
            ephemeral=True,
        )
        await self.audit(interaction.guild, settings, "💰 Staff grant", interaction.user,
                         member, int(amount), reason)

    @eco.command(name="take", description="Take coins from a member")
    @app_commands.describe(member="Who", amount="How many", reason="Why (recorded in the log)")
    async def eco_take(self, interaction: discord.Interaction, member: discord.Member,
                       amount: app_commands.Range[int, 1, 100_000_000],
                       reason: Optional[str] = None) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        taken = await self.bot.db.debit(
            interaction.guild.id, member.id, int(amount), kind="staff_take",
            note=f"by {interaction.user}: {reason or 'no reason given'}",
            target_id=interaction.user.id, allow_partial=True,
        )
        await interaction.followup.send(
            embed=util.ok_embed(
                settings,
                f"Took {util.coins(taken, settings)} from {member.mention}."
                + ("\n*(That was everything in their wallet.)*" if taken < int(amount) else ""),
            ),
            ephemeral=True,
        )
        await self.audit(interaction.guild, settings, "💸 Staff removal", interaction.user,
                         member, taken, reason)

    @eco.command(name="reset", description="Reset one member's coins, or everyone's")
    @app_commands.describe(member="Leave blank to reset the whole server")
    async def eco_reset(self, interaction: discord.Interaction,
                        member: Optional[discord.Member] = None) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        if member is not None:
            await self.bot.db.reset_member(interaction.guild.id, member.id)
            return await interaction.response.send_message(
                embed=util.ok_embed(settings, f"Reset **{member.display_name}** to zero."),
                ephemeral=True,
            )
        holders = await self.bot.db.rich_count(interaction.guild.id)
        circulating = await self.bot.db.total_in_circulation(interaction.guild.id)
        view = views.ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️ Reset every balance?",
                description=f"This wipes **{circulating:,}** coins held by **{holders}** "
                            "members, plus their streaks and cooldowns. Shop items and the "
                            "audit log are kept.\n\nThis cannot be undone.",
                color=discord.Color(0xE74C3C),
            ),
            view=view, ephemeral=True,
        )
        await view.wait()
        if not view.value:
            return await interaction.followup.send(
                embed=util.base_embed(settings, description="Cancelled."), ephemeral=True
            )
        cleared = await self.bot.db.reset_all_members(interaction.guild.id)
        await interaction.followup.send(
            embed=util.ok_embed(settings, f"Reset **{cleared}** members."), ephemeral=True
        )

    @eco.command(name="audit", description="Recent coin movements")
    @app_commands.describe(member="Filter to one member", limit="How many entries (default 15)")
    async def eco_audit(self, interaction: discord.Interaction,
                        member: Optional[discord.Member] = None,
                        limit: Optional[app_commands.Range[int, 1, 30]] = 15) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await self.bot.db.recent_transactions(
            interaction.guild.id, int(limit or 15),
            user_id=member.id if member else None,
        )
        if not rows:
            return await interaction.followup.send(
                embed=util.base_embed(settings, description="Nothing recorded yet."),
                ephemeral=True,
            )
        lines = []
        for row in rows:
            when = parse_ts(row.get("created_at"))
            amount = int(row["amount"])
            sign = "+" if amount > 0 else ""
            lines.append(
                f"{util.ts(when, 'R') if when else ''} <@{row['user_id']}> "
                f"**{sign}{amount:,}** `{row['kind']}`"
                + (f" → <@{row['target_id']}>" if row.get("target_id") else "")
            )
        await interaction.followup.send(
            embed=util.base_embed(
                settings, title="📒 Audit log",
                description=util.truncate("\n".join(lines), 4000),
            ),
            ephemeral=True,
        )

    async def audit(self, guild: discord.Guild, settings: dict, title: str,
                    actor: discord.abc.User, target: discord.abc.User,
                    amount: int, reason: Optional[str]) -> None:
        channel_id = settings.get("log_channel_id")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        embed = util.base_embed(settings, title=title)
        embed.add_field(name="Staff", value=actor.mention)
        embed.add_field(name="Member", value=target.mention)
        embed.add_field(name="Amount", value=util.coins(amount, settings))
        if reason:
            embed.add_field(name="Reason", value=util.truncate(reason, 1024), inline=False)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ─── /config messages … ───────────────────────────────────────────────────
    @messages_group.command(name="list", description="Every message you can rewrite")
    async def messages_list(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        bag = StringBag(await self.bot.db.get_strings(interaction.guild.id))
        keys = list(DEFAULT_STRINGS)
        for index, chunk in enumerate([keys[i:i + 12] for i in range(0, len(keys), 12)]):
            embed = util.base_embed(
                settings, title="✏️ Editable messages" + ("" if index == 0 else " (cont.)"),
                description=("`/config messages set key:<name>` opens an editor.\n"
                             "✏️ = rewritten here." if index == 0 else None),
            )
            for key in chunk:
                marker = "✏️ " if bag.is_custom(key) else ""
                embed.add_field(
                    name=f"{marker}{key}",
                    value=util.truncate(
                        f"{bag.raw(key)}\n\n*Placeholders:* `{PLACEHOLDERS.get(key, '—')}`", 1024),
                    inline=False,
                )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @messages_group.command(name="set", description="Rewrite one of the bot's messages")
    @app_commands.describe(key="Which message to rewrite")
    async def messages_set(self, interaction: discord.Interaction, key: str) -> None:
        settings = await self.bot.db.get_guild(interaction.guild.id)
        if not util.is_staff(interaction.user, settings):
            return await interaction.response.send_message(
                embed=util.err_embed("Staff only."), ephemeral=True
            )
        if key not in DEFAULT_STRINGS:
            return await interaction.response.send_message(
                embed=util.err_embed(f"`{key}` isn't a message key."), ephemeral=True
            )
        overrides = await self.bot.db.get_strings(interaction.guild.id)
        await interaction.response.send_modal(
            MessageModal(self, key, StringBag(overrides).raw(key))
        )

    @messages_set.autocomplete("key")
    async def set_key_ac(self, interaction: discord.Interaction, current: str):
        return await self.key_autocomplete(interaction, current)

    @messages_group.command(name="reset", description="Put one message back to the default")
    @app_commands.describe(key="Which message to reset")
    async def messages_reset(self, interaction: discord.Interaction, key: str) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        if key not in DEFAULT_STRINGS:
            return await interaction.response.send_message(
                embed=util.err_embed(f"`{key}` isn't a message key."), ephemeral=True
            )
        await self.bot.db.reset_string(interaction.guild.id, key)
        await interaction.response.send_message(
            embed=util.ok_embed(settings, f"`{key}` is back to the default."), ephemeral=True
        )

    @messages_reset.autocomplete("key")
    async def reset_key_ac(self, interaction: discord.Interaction, current: str):
        return await self.key_autocomplete(interaction, current)

    @config.command(name="reset", description="Wipe this server's economy entirely")
    async def config_reset(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        view = views.ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️ Reset everything?",
                description="This deletes this server's settings, **every balance**, the "
                            "shop, purchase history, custom trivia questions and the audit "
                            "log. It cannot be undone.",
                color=discord.Color(0xE74C3C),
            ),
            view=view, ephemeral=True,
        )
        await view.wait()
        if not view.value:
            return await interaction.followup.send(
                embed=util.base_embed(settings, description="Cancelled."), ephemeral=True
            )
        await self.bot.db.wipe_guild(interaction.guild.id)
        await interaction.followup.send(
            embed=util.ok_embed(settings, "Wiped. Run `/setup` to start again."), ephemeral=True
        )

    # ─── /help and greeting ───────────────────────────────────────────────────
    @app_commands.command(name="help", description="How to use Himyar Economy")
    @app_commands.guild_only()
    async def help_command(self, interaction: discord.Interaction) -> None:
        s = await self.bot.db.get_guild(interaction.guild.id)
        embed = util.base_embed(
            settings=s, title=f"{s['currency_emoji']} Himyar Economy",
            description=f"Earn and spend **{s['currency_name']}**. Configured entirely "
                        "inside Discord.",
        )
        embed.add_field(
            name="Earning",
            value="Chatting and voice pay automatically\n"
                  "`/daily` — once a day, with a streak bonus\n"
                  "`/work` — a job on a cooldown",
            inline=False,
        )
        embed.add_field(
            name="Your coins",
            value="`/balance` · `/rich`\n"
                  "`/deposit` · `/withdraw` — the bank is safe from robbery\n"
                  "`/pay` — send coins to someone\n"
                  "`/rob` — risky, and it costs you if you're caught",
            inline=False,
        )
        embed.add_field(
            name="Games",
            value="`/game trivia` — first correct answer wins\n"
                  "`/game guess` — guess the number, winner takes the pot\n"
                  "`/game duel` — challenge someone, both stake the same\n"
                  "`/game heist` — everyone who joins gets paid, more people means more each",
            inline=False,
        )
        embed.add_field(
            name="Spending",
            value="`/shop view` · `/shop buy` · `/shop inventory`",
            inline=False,
        )
        embed.add_field(
            name="Staff",
            value="`/setup` · `/config view` · `/config earning` · `/config robbery`\n"
                  "`/shop add` · `/shop pending` · `/shop fulfil`\n"
                  "`/eco give` · `/eco take` · `/eco reset` · `/eco audit`\n"
                  "`/trivia add` — your own questions",
            inline=False,
        )
        embed.set_footer(text="Part of the Himyar bot suite · himyar.org")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.bot.db.get_guild(guild.id)
        embed = discord.Embed(
            title="👋 Thanks for adding Himyar Economy",
            description=(
                "Members start earning coins right away. Run **`/setup`** to tune it.\n\n"
                "• `/balance` `/daily` `/work` — the basics\n"
                "• `/game trivia` `/game heist` — things to do together\n"
                "• `/shop add` — give coins somewhere to go\n"
                "• `/help` — everything else"
            ),
            color=discord.Color(0x1E90FF),
        )
        target = guild.system_channel
        if target is None or not target.permissions_for(guild.me).send_messages:
            target = next(
                (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
                None,
            )
        if target is not None:
            try:
                await target.send(embed=embed)
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Config(bot))
