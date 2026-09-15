"""
Himyar Economy — earning, wallets, banking and robbery.

/balance /daily /work /pay /deposit /withdraw /rob /rich

Coins trickle in from chatting and voice, same shape as XP. Every movement goes
through the database's transfer helpers so both sides happen under one lock.
"""

from __future__ import annotations

import datetime as dt
import logging
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import economy, util, views
from core.db import InsufficientFunds, parse_date, parse_ts, today_key, utcnow
from core.strings import WORK_FLAVOUR, StringBag

log = logging.getLogger(__name__)

VOICE_TICK_SECONDS = 60


class Wallet(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._chat_cooldowns: dict[tuple[int, int], float] = {}

    async def cog_load(self) -> None:
        self.voice_tick.start()

    async def cog_unload(self) -> None:
        self.voice_tick.cancel()

    async def context(self, guild_id: int) -> tuple[dict, StringBag]:
        settings = await self.bot.db.get_guild(guild_id)
        bag = StringBag(await self.bot.db.get_strings(guild_id))
        return settings, bag

    async def log_event(self, guild: discord.Guild, settings: dict,
                        embed: discord.Embed) -> None:
        channel_id = settings.get("log_channel_id")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    async def role_multiplier(self, guild_id: int, user_id: int) -> float:
        """Best role multiplier for this member, 1.0 if they have none."""
        mults = await self.bot.db.get_role_multipliers(guild_id)
        if not mults:
            return 1.0
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        if member is None:
            return 1.0
        return economy.best_multiplier([r.id for r in member.roles], mults)

    async def award(self, guild_id: int, user_id: int, amount: int, kind: str,
                    settings: dict) -> int:
        """Give coins that count toward the daily cap. Returns what was paid.

        The role bonus is applied before the daily cap, so a boosted member
        reaches the same ceiling sooner rather than earning past it.
        """
        multiplier = await self.role_multiplier(guild_id, user_id)
        if multiplier != 1.0:
            amount = int(round(max(0, int(amount)) * multiplier))
        day = today_key()
        already = await self.bot.db.earned_today(guild_id, user_id, day)
        amount = economy.apply_daily_cap(amount, already, int(settings.get("daily_earn_cap") or 0))
        if amount <= 0:
            return 0
        await self.bot.db.credit(guild_id, user_id, amount, kind=kind,
                                 counts_as_earned=True, day=day)
        return amount

    # ─── passive earning ──────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        settings = await self.bot.db.get_guild(message.guild.id)
        if not settings.get("chat_enabled"):
            return

        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        cooldown = int(settings.get("chat_cooldown") or 0)
        if cooldown and now < self._chat_cooldowns.get(key, 0.0):
            return
        self._chat_cooldowns[key] = now + cooldown

        low = int(settings.get("chat_min") or 1)
        high = max(low, int(settings.get("chat_max") or 3))
        await self.award(message.guild.id, message.author.id,
                         random.randint(low, high), "chat", settings)

    @tasks.loop(seconds=VOICE_TICK_SECONDS)
    async def voice_tick(self) -> None:
        """Pay for a minute of voice. Reads live voice states rather than tracking
        sessions, so a restart mid-call costs nobody anything."""
        for guild in list(self.bot.guilds):
            try:
                settings = await self.bot.db.get_guild(guild.id)
                if not settings.get("voice_enabled"):
                    continue
                per_minute = int(settings.get("voice_per_minute") or 0)
                if per_minute <= 0:
                    continue
                afk_id = guild.afk_channel.id if guild.afk_channel else None
                for channel in guild.voice_channels:
                    if afk_id is not None and channel.id == afk_id:
                        continue
                    humans = [m for m in channel.members if not m.bot]
                    if len(humans) < 2:
                        continue          # no paying people to sit alone
                    for member in humans:
                        state = member.voice
                        if state is None or state.self_mute or state.self_deaf:
                            continue
                        await self.award(guild.id, member.id, per_minute, "voice", settings)
            except Exception:
                log.exception("Voice earning pass failed for guild %s", guild.id)

    @voice_tick.before_loop
    async def before_voice(self) -> None:
        await self.bot.wait_until_ready()

    # ─── /balance ─────────────────────────────────────────────────────────────
    @app_commands.command(name="balance", description="Check your coins")
    @app_commands.guild_only()
    @app_commands.describe(member="Whose balance to check (defaults to you)")
    async def balance(self, interaction: discord.Interaction,
                      member: Optional[discord.Member] = None) -> None:
        settings, bag = await self.context(interaction.guild.id)
        target = member or interaction.user
        await interaction.response.defer(thinking=True)

        record = await self.bot.db.get_member(interaction.guild.id, target.id)
        wallet = int(record.get("wallet") or 0)
        bank = int(record.get("bank") or 0)
        rank = await self.bot.db.rank_of(interaction.guild.id, target.id)
        total_members = await self.bot.db.rich_count(interaction.guild.id)

        embed = util.base_embed(settings, title=bag.get("balance_title", name=target.display_name))
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Wallet", value=util.coins(wallet, settings))
        if settings.get("bank_enabled"):
            embed.add_field(name="Bank", value=util.coins(bank, settings))
            embed.add_field(name="Total", value=util.coins(wallet + bank, settings))
        embed.add_field(
            name="Rank",
            value=(f"**#{rank}** of {total_members}" if rank else "unranked"),
        )
        streak = int(record.get("streak") or 0)
        if streak:
            embed.add_field(name="Daily streak", value=f"{streak} day(s)")
        embed.add_field(name="Earned all-time",
                        value=util.coins(int(record.get("total_earned") or 0), settings))
        if settings.get("bank_enabled") and wallet > 0 and settings.get("rob_enabled"):
            embed.set_footer(text="Only your wallet can be robbed — /deposit keeps it safe.")
        await interaction.followup.send(embed=embed)

    # ─── /daily ───────────────────────────────────────────────────────────────
    @app_commands.command(name="daily", description="Claim your daily coins")
    @app_commands.guild_only()
    async def daily(self, interaction: discord.Interaction) -> None:
        settings, bag = await self.context(interaction.guild.id)
        await interaction.response.defer(thinking=True)

        record = await self.bot.db.get_member(interaction.guild.id, interaction.user.id)
        today = utcnow().date()
        last = parse_date(record.get("last_daily"))
        can_claim, new_streak = economy.streak_after_claim(
            last, today, int(record.get("streak") or 0)
        )
        if not can_claim:
            tomorrow = dt.datetime.combine(
                today + dt.timedelta(days=1), dt.time.min, tzinfo=dt.timezone.utc
            )
            return await interaction.followup.send(
                embed=util.err_embed(bag.get("daily_wait", when=util.ts(tomorrow, "R")))
            )

        payout = economy.daily_payout(
            int(settings.get("daily_amount") or 0), new_streak,
            int(settings.get("daily_streak_bonus") or 0),
            int(settings.get("daily_streak_cap") or 0),
        )
        paid = await self.award(interaction.guild.id, interaction.user.id, payout, "daily", settings)
        best = max(int(record.get("best_streak") or 0), new_streak)
        await self.bot.db.update_member(
            interaction.guild.id, interaction.user.id,
            last_daily=today, streak=new_streak, best_streak=best,
        )

        embed = util.ok_embed(
            settings, bag.get("daily_claimed", amount=util.coins(paid, settings),
                              streak=new_streak)
        )
        if paid < payout:
            embed.add_field(name="Note", value=bag.get("earn_capped"), inline=False)
        if new_streak > 1:
            bonus = payout - int(settings.get("daily_amount") or 0)
            if bonus > 0:
                embed.add_field(name="Streak bonus", value=util.coins(bonus, settings))
        await interaction.followup.send(embed=embed)

    # ─── /work ────────────────────────────────────────────────────────────────
    @app_commands.command(name="work", description="Do a job for some coins")
    @app_commands.guild_only()
    async def work(self, interaction: discord.Interaction) -> None:
        settings, bag = await self.context(interaction.guild.id)
        if not settings.get("work_enabled"):
            return await interaction.response.send_message(
                embed=util.err_embed("Working is turned off on this server."), ephemeral=True
            )
        await interaction.response.defer(thinking=True)

        record = await self.bot.db.get_member(interaction.guild.id, interaction.user.id)
        remaining = economy.cooldown_remaining(
            parse_ts(record.get("last_work")), int(settings.get("work_cooldown") or 0)
        )
        if remaining > 0:
            return await interaction.followup.send(
                embed=util.err_embed(bag.get("work_wait", when=util.in_seconds(remaining)))
            )

        low = int(settings.get("work_min") or 0)
        high = max(low, int(settings.get("work_max") or 0))
        paid = await self.award(interaction.guild.id, interaction.user.id,
                                random.randint(low, high), "work", settings)
        await self.bot.db.update_member(
            interaction.guild.id, interaction.user.id, last_work=utcnow()
        )
        embed = util.ok_embed(
            settings,
            bag.get("work_done", flavour=random.choice(WORK_FLAVOUR),
                    amount=util.coins(paid, settings)),
        )
        if paid == 0:
            embed = util.err_embed(bag.get("earn_capped"))
        await interaction.followup.send(embed=embed)

    # ─── /pay ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="pay", description="Send coins to another member")
    @app_commands.guild_only()
    @app_commands.describe(member="Who to pay", amount="How many coins")
    async def pay(self, interaction: discord.Interaction, member: discord.Member,
                  amount: app_commands.Range[int, 1, 100_000_000]) -> None:
        settings, bag = await self.context(interaction.guild.id)
        if not settings.get("pay_enabled"):
            return await interaction.response.send_message(
                embed=util.err_embed("Transfers are turned off on this server."), ephemeral=True
            )
        await interaction.response.defer(thinking=True)

        record = await self.bot.db.get_member(interaction.guild.id, interaction.user.id)
        plan = economy.plan_transfer(
            int(amount), int(record.get("wallet") or 0),
            account_age_days=util.account_age_days(interaction.user),
            min_account_age_days=int(settings.get("pay_min_account_days") or 0),
            sent_today=await self.bot.db.paid_today(interaction.guild.id, interaction.user.id),
            daily_limit=int(settings.get("pay_daily_limit") or 0),
            tax_percent=float(settings.get("pay_tax_percent") or 0),
            same_person=(member.id == interaction.user.id),
            recipient_is_bot=member.bot,
        )
        if not plan.ok:
            return await interaction.followup.send(embed=util.err_embed(plan.reason))

        moved = await self.bot.db.transfer(
            interaction.guild.id, interaction.user.id, member.id,
            plan.debit, plan.credit, kind="pay",
            note=f"{interaction.user} -> {member}",
        )
        if not moved:
            return await interaction.followup.send(
                embed=util.err_embed("That didn't go through — check your balance and try again.")
            )

        embed = util.ok_embed(
            settings, bag.get("pay_sent", amount=util.coins(plan.credit, settings),
                              target=member.mention)
        )
        if plan.tax:
            embed.add_field(name="Transfer fee", value=util.coins(plan.tax, settings))
        await interaction.followup.send(embed=embed)

        try:
            await member.send(
                embed=util.base_embed(
                    settings,
                    description=bag.get("pay_received", sender=interaction.user.display_name,
                                        amount=util.coins(plan.credit, settings),
                                        guild=interaction.guild.name),
                )
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

        log_embed = util.base_embed(settings, title="💸 Transfer")
        log_embed.add_field(name="From", value=f"{interaction.user.mention}\n`{interaction.user.id}`")
        log_embed.add_field(name="To", value=f"{member.mention}\n`{member.id}`")
        log_embed.add_field(name="Amount", value=util.coins(plan.credit, settings))
        await self.log_event(interaction.guild, settings, log_embed)

    # ─── /deposit and /withdraw ───────────────────────────────────────────────
    @app_commands.command(name="deposit", description="Move coins into the bank, safe from robbery")
    @app_commands.guild_only()
    @app_commands.describe(amount="How many coins, or leave blank for everything")
    async def deposit(self, interaction: discord.Interaction,
                      amount: Optional[app_commands.Range[int, 1, 100_000_000]] = None) -> None:
        settings, bag = await self.context(interaction.guild.id)
        if not settings.get("bank_enabled"):
            return await interaction.response.send_message(
                embed=util.err_embed("The bank is turned off on this server."), ephemeral=True
            )
        await interaction.response.defer(thinking=True)
        record = await self.bot.db.get_member(interaction.guild.id, interaction.user.id)
        move = int(amount) if amount is not None else int(record.get("wallet") or 0)
        if move <= 0:
            return await interaction.followup.send(
                embed=util.err_embed("Your wallet is empty.")
            )
        if not await self.bot.db.move_bank(interaction.guild.id, interaction.user.id, move, True):
            return await interaction.followup.send(
                embed=util.err_embed("You don't have that much in your wallet.")
            )
        await interaction.followup.send(
            embed=util.ok_embed(settings,
                                bag.get("deposit_done", amount=util.coins(move, settings)))
        )

    @app_commands.command(name="withdraw", description="Move coins out of the bank")
    @app_commands.guild_only()
    @app_commands.describe(amount="How many coins, or leave blank for everything")
    async def withdraw(self, interaction: discord.Interaction,
                       amount: Optional[app_commands.Range[int, 1, 100_000_000]] = None) -> None:
        settings, bag = await self.context(interaction.guild.id)
        if not settings.get("bank_enabled"):
            return await interaction.response.send_message(
                embed=util.err_embed("The bank is turned off on this server."), ephemeral=True
            )
        await interaction.response.defer(thinking=True)
        record = await self.bot.db.get_member(interaction.guild.id, interaction.user.id)
        move = int(amount) if amount is not None else int(record.get("bank") or 0)
        if move <= 0:
            return await interaction.followup.send(
                embed=util.err_embed("Your bank is empty.")
            )
        if not await self.bot.db.move_bank(interaction.guild.id, interaction.user.id, move, False):
            return await interaction.followup.send(
                embed=util.err_embed("You don't have that much in the bank.")
            )
        await interaction.followup.send(
            embed=util.ok_embed(settings,
                                bag.get("withdraw_done", amount=util.coins(move, settings)))
        )

    # ─── /rob ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="rob", description="Try to rob another member's wallet")
    @app_commands.guild_only()
    @app_commands.describe(member="Who to rob")
    async def rob(self, interaction: discord.Interaction, member: discord.Member) -> None:
        settings, bag = await self.context(interaction.guild.id)
        if not settings.get("rob_enabled"):
            return await interaction.response.send_message(
                embed=util.err_embed("Robbery is turned off on this server."), ephemeral=True
            )
        await interaction.response.defer(thinking=True)

        robber = await self.bot.db.get_member(interaction.guild.id, interaction.user.id)
        victim = await self.bot.db.get_member(interaction.guild.id, member.id)

        remaining = economy.cooldown_remaining(
            parse_ts(robber.get("last_rob")), int(settings.get("rob_cooldown") or 0)
        )
        if remaining > 0:
            return await interaction.followup.send(
                embed=util.err_embed(bag.get("rob_wait", when=util.in_seconds(remaining)))
            )

        protect = economy.cooldown_remaining(
            parse_ts(victim.get("last_robbed")), int(settings.get("rob_victim_protect") or 0)
        )
        if protect > 0:
            return await interaction.followup.send(
                embed=util.err_embed(bag.get("rob_protected", victim=member.display_name))
            )

        result = economy.plan_robbery(
            robber_wallet=int(robber.get("wallet") or 0),
            victim_wallet=int(victim.get("wallet") or 0),
            success_percent=float(settings.get("rob_success_percent") or 0),
            max_percent=float(settings.get("rob_max_percent") or 0),
            fine_percent=float(settings.get("rob_fine_percent") or 0),
            min_victim_wallet=int(settings.get("rob_min_victim_wallet") or 0),
            same_person=(member.id == interaction.user.id),
            victim_is_bot=member.bot,
        )
        if not result.ok:
            return await interaction.followup.send(embed=util.err_embed(result.reason))

        # The cooldown applies whether it worked or not, so failure still costs.
        await self.bot.db.update_member(
            interaction.guild.id, interaction.user.id, last_rob=utcnow()
        )

        if result.success:
            moved = await self.bot.db.transfer(
                interaction.guild.id, member.id, interaction.user.id,
                result.taken, result.taken, kind="rob",
                note=f"{interaction.user} robbed {member}",
            )
            if not moved:
                return await interaction.followup.send(
                    embed=util.err_embed("They moved their coins before you got there.")
                )
            await self.bot.db.update_member(
                interaction.guild.id, member.id, last_robbed=utcnow()
            )
            text = bag.get("rob_success", robber=interaction.user.mention,
                           victim=member.mention, amount=util.coins(result.taken, settings))
            embed = util.base_embed(settings, title="🦝 Robbery", description=text)
        else:
            if result.fine > 0:
                try:
                    await self.bot.db.debit(
                        interaction.guild.id, interaction.user.id, result.fine,
                        kind="rob_fine", note="caught robbing", allow_partial=True,
                    )
                except InsufficientFunds:
                    pass
            text = bag.get("rob_failed", robber=interaction.user.mention,
                           victim=member.mention, amount=util.coins(result.fine, settings))
            embed = util.base_embed(settings, title="🚨 Caught", description=text)

        await interaction.followup.send(embed=embed)

        log_embed = util.base_embed(
            settings, title="🦝 Robbery " + ("succeeded" if result.success else "failed")
        )
        log_embed.add_field(name="Robber", value=interaction.user.mention)
        log_embed.add_field(name="Target", value=member.mention)
        log_embed.add_field(
            name="Amount",
            value=util.coins(result.taken if result.success else result.fine, settings),
        )
        await self.log_event(interaction.guild, settings, log_embed)

    # ─── /rich ────────────────────────────────────────────────────────────────
    @app_commands.command(name="rich", description="The richest members on this server")
    @app_commands.guild_only()
    async def rich(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        total = await self.bot.db.rich_count(interaction.guild.id)
        if total == 0:
            settings, bag = await self.context(interaction.guild.id)
            return await interaction.followup.send(
                embed=util.base_embed(settings, description="Nobody has any coins yet.")
            )
        pages = max(1, (total + 9) // 10)
        embed = await self.render_rich(interaction.guild, 0)
        view = views.PagerView(self.render_rich, 0, pages, interaction.user.id)
        await interaction.followup.send(embed=embed, view=view)

    async def render_rich(self, guild: discord.Guild, page: int) -> discord.Embed:
        settings, bag = await self.context(guild.id)
        offset = max(0, page) * 10
        rows = await self.bot.db.rich_list(guild.id, 10, offset)
        total = await self.bot.db.rich_count(guild.id)

        lines = []
        for index, row in enumerate(rows):
            position = offset + index + 1
            member = guild.get_member(int(row["user_id"]))
            name = member.display_name if member else f"Member {row['user_id']}"
            holdings = int(row.get("wallet") or 0) + int(row.get("bank") or 0)
            lines.append(f"{util.medal(position)} **{util.truncate(name, 26)}** — "
                         f"{util.coins(holdings, settings)}")

        embed = util.base_embed(
            settings, title=bag.get("rich_title", guild=guild.name),
            description="\n".join(lines) if lines else "Nobody has any coins yet.",
        )
        circulating = await self.bot.db.total_in_circulation(guild.id)
        embed.set_footer(
            text=f"Page {page + 1} of {max(1, (total + 9) // 10)} · "
                 f"{circulating:,} in circulation"
        )
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Wallet(bot))
