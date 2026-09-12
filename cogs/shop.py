"""
Himyar Economy — the shop.

Three kinds of item: a permanent role, a temporary role that expires on its own,
and a custom item that staff fulfil by hand. Custom purchases are logged so
nothing bought is quietly forgotten.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import util, views
from core.db import InsufficientFunds, parse_ts, utcnow
from core.strings import StringBag

log = logging.getLogger(__name__)

EXPIRY_TICK_MINUTES = 5


class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.expiry_tick.start()

    async def cog_unload(self) -> None:
        self.expiry_tick.cancel()

    async def context(self, guild_id: int) -> tuple[dict, StringBag]:
        settings = await self.bot.db.get_guild(guild_id)
        bag = StringBag(await self.bot.db.get_strings(guild_id))
        return settings, bag

    shop = app_commands.Group(name="shop", description="Buy things with your coins",
                              guild_only=True)

    # ─── /shop view ───────────────────────────────────────────────────────────
    @shop.command(name="view", description="See what's for sale")
    async def shop_view(self, interaction: discord.Interaction) -> None:
        settings, bag = await self.context(interaction.guild.id)
        await interaction.response.defer(thinking=True)
        items = await self.bot.db.get_items(interaction.guild.id)
        if not items:
            return await interaction.followup.send(
                embed=util.base_embed(settings, description=bag.get("shop_empty"))
            )

        record = await self.bot.db.get_member(interaction.guild.id, interaction.user.id)
        wallet = int(record.get("wallet") or 0)

        embed = util.base_embed(
            settings, title=bag.get("shop_title", guild=interaction.guild.name),
            description=f"You have {util.coins(wallet, settings)}.\n"
                        "Buy with `/shop buy id:<number>`.",
        )
        for item in items[:25]:
            bits = [util.coins(int(item["price"]), settings)]
            if item["kind"] == "temp_role" and item.get("duration"):
                bits.append(f"lasts {util.human_duration(int(item['duration']))}")
            if int(item.get("stock") or -1) >= 0:
                bits.append(f"{item['stock']} left")
            affordable = "" if wallet >= int(item["price"]) else "  *(can't afford)*"
            name = f"`#{item['id']}` {item['name']}{affordable}"
            value = " · ".join(bits)
            if item.get("description"):
                value += f"\n{util.truncate(item['description'], 150)}"
            embed.add_field(name=util.truncate(name, 256), value=value, inline=False)
        await interaction.followup.send(embed=embed)

    # ─── /shop buy ────────────────────────────────────────────────────────────
    @shop.command(name="buy", description="Buy something from the shop")
    @app_commands.describe(id="The item number from /shop view")
    async def shop_buy(self, interaction: discord.Interaction, id: int) -> None:
        settings, bag = await self.context(interaction.guild.id)
        await interaction.response.defer(thinking=True)

        item = await self.bot.db.get_item(int(id))
        if not item or item["guild_id"] != interaction.guild.id or not item.get("enabled"):
            return await interaction.followup.send(
                embed=util.err_embed("That item isn't in this server's shop.")
            )

        price = int(item["price"])
        record = await self.bot.db.get_member(interaction.guild.id, interaction.user.id)
        wallet = int(record.get("wallet") or 0)
        if wallet < price:
            return await interaction.followup.send(
                embed=util.err_embed(
                    bag.get("buy_poor", amount=util.coins(price, settings),
                            balance=util.coins(wallet, settings))
                )
            )

        role: Optional[discord.Role] = None
        if item["kind"] in ("role", "temp_role"):
            role = interaction.guild.get_role(int(item["role_id"] or 0))
            if role is None:
                return await interaction.followup.send(
                    embed=util.err_embed("That role no longer exists — tell staff.")
                )
            if role in interaction.user.roles and item["kind"] == "role":
                return await interaction.followup.send(embed=util.err_embed(bag.get("buy_owned")))
            if role >= interaction.guild.me.top_role:
                return await interaction.followup.send(
                    embed=util.err_embed(
                        "That role sits above mine, so I can't give it out. Tell staff to "
                        "move my role higher in **Server Settings → Roles**."
                    )
                )

        # Take stock before taking money, so a sold-out race never charges anyone.
        if not await self.bot.db.take_stock(int(item["id"])):
            return await interaction.followup.send(embed=util.err_embed(bag.get("buy_stock")))

        try:
            await self.bot.db.debit(
                interaction.guild.id, interaction.user.id, price,
                kind="shop", note=f"bought {item['name']}",
            )
        except InsufficientFunds:
            if int(item.get("stock") or -1) >= 0:      # hand the unit back
                await self.bot.db.update_item(int(item["id"]),
                                              stock=int(item["stock"]))
            return await interaction.followup.send(embed=util.err_embed(bag.get("not_enough")))

        granted = True
        if role is not None:
            try:
                await interaction.user.add_roles(
                    role, reason=f"Himyar Economy: bought {item['name']}"
                )
            except discord.HTTPException:
                granted = False

        if not granted:
            # Refund rather than take coins for something they didn't receive.
            await self.bot.db.credit(
                interaction.guild.id, interaction.user.id, price,
                kind="refund", note=f"could not grant {item['name']}",
            )
            return await interaction.followup.send(
                embed=util.err_embed(
                    "I couldn't give you that role, so you haven't been charged. Tell staff."
                )
            )

        if item["kind"] == "temp_role" and role is not None:
            expires = utcnow() + dt.timedelta(seconds=int(item.get("duration") or 0))
            await self.bot.db.add_temp_role(
                interaction.guild.id, interaction.user.id, role.id, expires
            )

        await self.bot.db.record_purchase(interaction.guild.id, interaction.user.id, item)

        embed = util.ok_embed(
            settings, bag.get("buy_done", item=item["name"],
                              amount=util.coins(price, settings))
        )
        if item["kind"] == "temp_role":
            expires = utcnow() + dt.timedelta(seconds=int(item.get("duration") or 0))
            embed.add_field(name="Expires", value=util.ts(expires, "R"))
        if item["kind"] == "custom":
            embed.add_field(
                name="What happens next",
                value="Staff have been notified and will sort this out for you.",
                inline=False,
            )
        await interaction.followup.send(embed=embed)

        log_embed = util.base_embed(settings, title="🛒 Purchase")
        log_embed.add_field(name="Member", value=interaction.user.mention)
        log_embed.add_field(name="Item", value=item["name"])
        log_embed.add_field(name="Price", value=util.coins(price, settings))
        if item["kind"] == "custom":
            log_embed.add_field(name="Action needed",
                                value="Fulfil this manually, then `/shop fulfil`.",
                                inline=False)
        channel_id = settings.get("log_channel_id")
        if channel_id:
            channel = interaction.guild.get_channel(int(channel_id))
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(embed=log_embed)
                except discord.HTTPException:
                    pass

    # ─── /shop inventory ──────────────────────────────────────────────────────
    @shop.command(name="inventory", description="What you've bought")
    async def shop_inventory(self, interaction: discord.Interaction) -> None:
        settings, bag = await self.context(interaction.guild.id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        purchases = await self.bot.db.get_purchases(interaction.guild.id, interaction.user.id)
        temps = await self.bot.db.member_temp_roles(interaction.guild.id, interaction.user.id)

        if not purchases and not temps:
            return await interaction.followup.send(
                embed=util.base_embed(settings, description="You haven't bought anything yet."),
                ephemeral=True,
            )
        embed = util.base_embed(settings, title="🎒 Your purchases")
        if temps:
            lines = []
            for row in temps:
                role = interaction.guild.get_role(int(row["role_id"]))
                expires = parse_ts(row["expires_at"])
                lines.append(f"{role.mention if role else 'deleted role'} — expires "
                             f"{util.ts(expires, 'R') if expires else 'soon'}")
            embed.add_field(name="Active temporary roles", value="\n".join(lines), inline=False)
        if purchases:
            lines = [
                f"**{p['item_name']}** — {util.coins(int(p['price']), settings)}"
                + ("  *(awaiting staff)*" if p["kind"] == "custom" and not p["fulfilled"] else "")
                for p in purchases[:15]
            ]
            embed.add_field(name="History", value="\n".join(lines), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── staff: item management ───────────────────────────────────────────────
    async def guard(self, interaction: discord.Interaction) -> Optional[dict]:
        settings = await self.bot.db.get_guild(interaction.guild.id)
        if not util.is_staff(interaction.user, settings):
            bag = StringBag(await self.bot.db.get_strings(interaction.guild.id))
            await interaction.response.send_message(
                embed=util.err_embed(bag.get("staff_only")), ephemeral=True
            )
            return None
        return settings

    @shop.command(name="add", description="Add something to the shop (staff)")
    @app_commands.describe(
        name="What it's called", price="Cost in coins",
        kind="What the member gets",
        role="The role to give, for role items",
        duration="How long a temporary role lasts — 7d, 24h",
        description="Shown under the name in the shop",
        stock="How many can be sold (leave blank for unlimited)",
    )
    @app_commands.choices(kind=[
        app_commands.Choice(name="Permanent role", value="role"),
        app_commands.Choice(name="Temporary role", value="temp_role"),
        app_commands.Choice(name="Custom item (staff fulfil by hand)", value="custom"),
    ])
    async def shop_add(
        self, interaction: discord.Interaction, name: str,
        price: app_commands.Range[int, 1, 100_000_000],
        kind: app_commands.Choice[str],
        role: Optional[discord.Role] = None,
        duration: Optional[str] = None,
        description: Optional[str] = None,
        stock: Optional[app_commands.Range[int, 1, 100_000]] = None,
    ) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return

        if kind.value in ("role", "temp_role"):
            if role is None:
                return await interaction.response.send_message(
                    embed=util.err_embed("Pick a role for a role item."), ephemeral=True
                )
            if role >= interaction.guild.me.top_role:
                return await interaction.response.send_message(
                    embed=util.err_embed(
                        f"{role.mention} sits above my own role, so I couldn't hand it out. "
                        "Drag my role higher in **Server Settings → Roles** first."
                    ),
                    ephemeral=True,
                )
            if role.managed:
                return await interaction.response.send_message(
                    embed=util.err_embed(
                        f"{role.mention} is managed by an integration — nobody can assign it."
                    ),
                    ephemeral=True,
                )

        seconds = 0
        if kind.value == "temp_role":
            if not duration:
                return await interaction.response.send_message(
                    embed=util.err_embed("Temporary roles need a duration, e.g. `7d`."),
                    ephemeral=True,
                )
            try:
                seconds = util.parse_duration(duration, minimum=300)
            except util.DurationError as exc:
                return await interaction.response.send_message(
                    embed=util.err_embed(str(exc)), ephemeral=True
                )

        item_id = await self.bot.db.add_item(
            interaction.guild.id, name.strip()[:100], int(price),
            kind=kind.value, role_id=role.id if role else None,
            duration=seconds, description=(description or "").strip()[:300] or None,
            stock=int(stock) if stock is not None else -1,
        )
        await interaction.response.send_message(
            embed=util.ok_embed(
                settings,
                f"Added **{name}** as item `#{item_id}` for {util.coins(int(price), settings)}."
                + (f"\nLasts {util.human_duration(seconds)}." if seconds else "")
                + (f"\nStock: {stock}." if stock is not None else ""),
            ),
            ephemeral=True,
        )

    @shop.command(name="remove", description="Remove a shop item (staff)")
    @app_commands.describe(id="The item number")
    async def shop_remove(self, interaction: discord.Interaction, id: int) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        item = await self.bot.db.get_item(int(id))
        if not item or item["guild_id"] != interaction.guild.id:
            return await interaction.response.send_message(
                embed=util.err_embed("No such item in this server's shop."), ephemeral=True
            )
        await self.bot.db.delete_item(int(id))
        await interaction.response.send_message(
            embed=util.ok_embed(settings, f"Removed **{item['name']}** from the shop."),
            ephemeral=True,
        )

    @shop.command(name="edit", description="Change a shop item (staff)")
    @app_commands.describe(id="The item number", price="New price",
                           name="New name", description="New description",
                           enabled="Show or hide it", stock="New stock (-1 for unlimited)")
    async def shop_edit(
        self, interaction: discord.Interaction, id: int,
        price: Optional[app_commands.Range[int, 1, 100_000_000]] = None,
        name: Optional[str] = None, description: Optional[str] = None,
        enabled: Optional[bool] = None,
        stock: Optional[app_commands.Range[int, -1, 100_000]] = None,
    ) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        item = await self.bot.db.get_item(int(id))
        if not item or item["guild_id"] != interaction.guild.id:
            return await interaction.response.send_message(
                embed=util.err_embed("No such item."), ephemeral=True
            )
        updates: dict = {}
        if price is not None:
            updates["price"] = int(price)
        if name is not None:
            updates["name"] = name.strip()[:100]
        if description is not None:
            updates["description"] = description.strip()[:300] or None
        if enabled is not None:
            updates["enabled"] = int(enabled)
        if stock is not None:
            updates["stock"] = int(stock)
        if not updates:
            return await interaction.response.send_message(
                embed=util.err_embed("Give me at least one thing to change."), ephemeral=True
            )
        await self.bot.db.update_item(int(id), **updates)
        await interaction.response.send_message(
            embed=util.ok_embed(settings, f"Updated item `#{id}`."), ephemeral=True
        )

    @shop.command(name="pending", description="Custom purchases waiting to be fulfilled (staff)")
    async def shop_pending(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await self.bot.db.get_purchases(interaction.guild.id, pending_only=True)
        if not rows:
            return await interaction.followup.send(
                embed=util.base_embed(settings, description="Nothing waiting."), ephemeral=True
            )
        lines = [
            f"`#{p['id']}` <@{p['user_id']}> — **{p['item_name']}** "
            f"({util.coins(int(p['price']), settings)})"
            for p in rows[:20]
        ]
        await interaction.followup.send(
            embed=util.base_embed(
                settings, title="📦 Awaiting fulfilment",
                description="\n".join(lines) + "\n\nMark one done with `/shop fulfil id:<number>`.",
            ),
            ephemeral=True,
        )

    @shop.command(name="fulfil", description="Mark a custom purchase as done (staff)")
    @app_commands.describe(id="The purchase number from /shop pending")
    async def shop_fulfil(self, interaction: discord.Interaction, id: int) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        if await self.bot.db.fulfil_purchase(int(id)):
            await interaction.response.send_message(
                embed=util.ok_embed(settings, f"Purchase `#{id}` marked as fulfilled."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=util.err_embed("That purchase doesn't exist, or was already fulfilled."),
                ephemeral=True,
            )

    # ─── temporary role expiry ────────────────────────────────────────────────
    @tasks.loop(minutes=EXPIRY_TICK_MINUTES)
    async def expiry_tick(self) -> None:
        try:
            expired = await self.bot.db.expired_temp_roles()
        except Exception:
            log.exception("Could not read expired temporary roles")
            return
        for row in expired:
            try:
                guild = self.bot.get_guild(int(row["guild_id"]))
                if guild is None:
                    await self.bot.db.delete_temp_role(int(row["id"]))
                    continue
                member = guild.get_member(int(row["user_id"]))
                role = guild.get_role(int(row["role_id"]))
                if member is not None and role is not None and role in member.roles:
                    try:
                        await member.remove_roles(
                            role, reason="Himyar Economy: temporary role expired"
                        )
                    except discord.HTTPException:
                        log.warning("Could not remove expired role %s from %s",
                                    role.id, member.id)
                await self.bot.db.delete_temp_role(int(row["id"]))
            except Exception:
                log.exception("Temporary role expiry failed for row %s", row.get("id"))

    @expiry_tick.before_loop
    async def before_expiry(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Shop(bot))
