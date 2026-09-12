"""
Himyar Economy — the games.

Trivia, guess the number, duels and the co-op heist. No house games and no
betting framing: members either compete against each other or work together.

Every game with a stake has a row in the database recording who paid what, so a
restart mid-game refunds everyone rather than keeping their entry fees. Payouts
go through finish_game(), which only succeeds once — a game can never pay twice.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core import economy, util, views
from core.db import InsufficientFunds, parse_ts, utcnow
from core.strings import StringBag
from core.trivia_bank import BUILTIN_QUESTIONS

log = logging.getLogger(__name__)
_rng = random.SystemRandom()


class Games(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._guess_targets: dict[int, int] = {}      # game_id -> answer

    async def context(self, guild_id: int) -> tuple[dict, StringBag]:
        settings = await self.bot.db.get_guild(guild_id)
        bag = StringBag(await self.bot.db.get_strings(guild_id))
        return settings, bag

    async def games_open(self, interaction: discord.Interaction) -> Optional[tuple[dict, StringBag]]:
        settings, bag = await self.context(interaction.guild.id)
        if not settings.get("games_enabled"):
            await interaction.response.send_message(
                embed=util.err_embed(bag.get("games_disabled")), ephemeral=True
            )
            return None
        return settings, bag

    # ─── crash recovery ───────────────────────────────────────────────────────
    async def refund_unfinished(self) -> int:
        """Refund every stake in a game that was still running when we stopped."""
        refunded = 0
        for game in await self.bot.db.unfinished_games():
            if not await self.bot.db.finish_game(int(game["id"]), status="refunded"):
                continue
            for player in await self.bot.db.game_players(int(game["id"])):
                stake = int(player.get("stake") or 0)
                if stake > 0:
                    await self.bot.db.credit(
                        int(game["guild_id"]), int(player["user_id"]), stake,
                        kind="refund", note=f"{game['kind']} interrupted",
                    )
                    refunded += 1
        if refunded:
            log.info("Refunded %d stake(s) from games interrupted by a restart", refunded)
        return refunded

    games = app_commands.Group(name="game", description="Play for coins", guild_only=True)

    # ─── trivia ───────────────────────────────────────────────────────────────
    @games.command(name="trivia", description="Start a trivia round — first correct answer wins")
    async def trivia(self, interaction: discord.Interaction) -> None:
        opened = await self.games_open(interaction)
        if opened is None:
            return
        settings, bag = opened

        existing = await self.bot.db.active_game(
            interaction.guild.id, "trivia", interaction.channel_id
        )
        if existing:
            return await interaction.response.send_message(
                embed=util.err_embed("A trivia round is already running here."), ephemeral=True
            )

        questions = await self.bot.db.get_questions(interaction.guild.id)
        if not questions:
            return await interaction.response.send_message(
                embed=util.err_embed("No trivia questions are available."), ephemeral=True
            )
        question = _rng.choice(questions)

        # Shuffle the options so the right answer isn't always first.
        options = list(question["answers"])
        correct_text = options[int(question["correct"])]
        _rng.shuffle(options)
        correct_index = options.index(correct_text)

        reward = int(settings.get("trivia_reward") or 0)
        seconds = int(settings.get("trivia_seconds") or 30)
        await interaction.response.defer(thinking=True)

        game_id = await self.bot.db.create_game(
            interaction.guild.id, interaction.channel_id, "trivia",
            {"correct": correct_index, "answer": correct_text},
            ends_at=utcnow() + dt.timedelta(seconds=seconds),
        )

        embed = util.base_embed(
            settings,
            title=bag.get("trivia_question", amount=util.coins(reward, settings)),
            description=f"**{question['question']}**",
        )
        embed.set_footer(text=f"{seconds} seconds · category: {question.get('category', 'general')}")

        view = views.TriviaView(game_id, options, self.on_trivia_answer, timeout=seconds)
        message = await interaction.followup.send(embed=embed, view=view)
        await self.bot.db.update_game(game_id, message_id=message.id)

        await asyncio.sleep(seconds)
        if await self.bot.db.finish_game(game_id, status="expired"):
            for child in view.children:
                child.disabled = True
            view.finished = True
            try:
                await message.edit(view=view)
                await message.reply(
                    embed=util.base_embed(
                        settings, description=bag.get("trivia_nobody", answer=correct_text)
                    )
                )
            except discord.HTTPException:
                pass

    async def on_trivia_answer(self, interaction: discord.Interaction,
                               view: views.TriviaView, index: int) -> None:
        game = await self.bot.db.get_game(view.game_id)
        if not game or game.get("status") != "running":
            return await interaction.response.send_message(
                "This round is already over.", ephemeral=True
            )
        settings, bag = await self.context(interaction.guild.id)
        state = game.get("state") or {}

        if index != int(state.get("correct", -1)):
            return await interaction.response.send_message(
                "❌ Not that one — but someone else might still get it.", ephemeral=True
            )

        # finish_game only succeeds once, so two people answering at the same
        # instant can never both be paid.
        if not await self.bot.db.finish_game(view.game_id):
            return await interaction.response.send_message(
                "Someone beat you to it by a fraction of a second.", ephemeral=True
            )

        view.finished = True
        for child in view.children:
            child.disabled = True

        reward = int(settings.get("trivia_reward") or 0)
        wallet_cog = self.bot.get_cog("Wallet")
        paid = reward
        if wallet_cog is not None:
            paid = await wallet_cog.award(interaction.guild.id, interaction.user.id,
                                          reward, "trivia", settings)
        else:
            await self.bot.db.credit(interaction.guild.id, interaction.user.id,
                                     reward, kind="trivia")

        await interaction.response.edit_message(view=view)
        await interaction.followup.send(
            embed=util.base_embed(
                settings,
                description=bag.get("trivia_correct", user=interaction.user.mention,
                                    amount=util.coins(paid, settings),
                                    answer=state.get("answer", "")),
            )
        )

    # ─── guess the number ─────────────────────────────────────────────────────
    @games.command(name="guess", description="Start a guess-the-number round")
    async def guess(self, interaction: discord.Interaction) -> None:
        opened = await self.games_open(interaction)
        if opened is None:
            return
        settings, bag = opened

        if await self.bot.db.active_game(interaction.guild.id, "guess", interaction.channel_id):
            return await interaction.response.send_message(
                embed=util.err_embed("A round is already running in this channel."),
                ephemeral=True,
            )

        cost = int(settings.get("guess_cost") or 0)
        ceiling = max(2, int(settings.get("guess_max") or 100))
        seconds = int(settings.get("guess_seconds") or 120)
        await interaction.response.defer(thinking=True)

        game_id = await self.bot.db.create_game(
            interaction.guild.id, interaction.channel_id, "guess", {"max": ceiling},
            ends_at=utcnow() + dt.timedelta(seconds=seconds),
        )
        self._guess_targets[game_id] = _rng.randint(1, ceiling)

        embed = util.base_embed(
            settings, title="🔢 Guess the number",
            description=bag.get("guess_started", max=ceiling,
                                amount=util.coins(cost, settings)),
        )
        embed.set_footer(text=f"Type a number in this channel · {seconds} seconds · "
                              "each guess costs coins and adds to the pot")
        message = await interaction.followup.send(embed=embed)
        await self.bot.db.update_game(game_id, message_id=message.id)

        await asyncio.sleep(seconds)
        target = self._guess_targets.pop(game_id, None)
        if await self.bot.db.finish_game(game_id, status="expired"):
            pot = int((await self.bot.db.get_game(game_id) or {}).get("pot") or 0)
            try:
                await message.reply(
                    embed=util.base_embed(
                        settings,
                        description=bag.get("guess_expired", number=target)
                        + (f"\nThe pot of {util.coins(pot, settings)} goes unclaimed."
                           if pot else ""),
                    )
                )
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Guesses are plain numbers typed in the channel, so anyone can join in
        without learning a command."""
        if message.guild is None or message.author.bot:
            return
        content = (message.content or "").strip()
        if not content.isdigit():
            return
        game = await self.bot.db.active_game(message.guild.id, "guess", message.channel.id)
        if not game:
            return

        settings, bag = await self.context(message.guild.id)
        game_id = int(game["id"])
        target = self._guess_targets.get(game_id)
        if target is None:
            return

        guess = int(content)
        ceiling = int((game.get("state") or {}).get("max") or 100)
        if not 1 <= guess <= ceiling:
            return

        cost = int(settings.get("guess_cost") or 0)
        if cost > 0:
            try:
                await self.bot.db.debit(message.guild.id, message.author.id, cost,
                                        kind="guess", note="guess entry")
            except InsufficientFunds:
                try:
                    await message.reply(
                        embed=util.err_embed(
                            f"You need {util.coins(cost, settings)} to guess."
                        ),
                        delete_after=10,
                    )
                except discord.HTTPException:
                    pass
                return
            await self.bot.db.update_game(game_id, pot=int(game.get("pot") or 0) + cost)

        hint = economy.guess_hint(guess, target)
        if hint != "correct":
            try:
                await message.add_reaction("🔼" if hint == "higher" else "🔽")
            except discord.HTTPException:
                pass
            return

        if not await self.bot.db.finish_game(game_id):
            return
        self._guess_targets.pop(game_id, None)

        fresh = await self.bot.db.get_game(game_id)
        pot = int((fresh or {}).get("pot") or 0)
        await self.bot.db.credit(message.guild.id, message.author.id, pot,
                                 kind="guess_win", note="guessed correctly")
        try:
            await message.reply(
                embed=util.base_embed(
                    settings,
                    description=bag.get("guess_won", user=message.author.mention,
                                        number=target, amount=util.coins(pot, settings)),
                )
            )
        except discord.HTTPException:
            pass

    # ─── duels ────────────────────────────────────────────────────────────────
    @games.command(name="duel", description="Challenge someone to rock-paper-scissors")
    @app_commands.describe(member="Who to challenge", stake="Coins each of you puts up")
    async def duel(self, interaction: discord.Interaction, member: discord.Member,
                   stake: app_commands.Range[int, 1, 100_000_000]) -> None:
        opened = await self.games_open(interaction)
        if opened is None:
            return
        settings, bag = opened
        if not settings.get("duel_enabled"):
            return await interaction.response.send_message(
                embed=util.err_embed("Duels are turned off on this server."), ephemeral=True
            )
        if member.bot or member.id == interaction.user.id:
            return await interaction.response.send_message(
                embed=util.err_embed("Pick another member to duel."), ephemeral=True
            )
        cap = int(settings.get("duel_max_stake") or 0)
        if cap and int(stake) > cap:
            return await interaction.response.send_message(
                embed=util.err_embed(
                    f"The most you can stake here is {util.coins(cap, settings)}."
                ),
                ephemeral=True,
            )

        challenger = await self.bot.db.get_member(interaction.guild.id, interaction.user.id)
        opponent = await self.bot.db.get_member(interaction.guild.id, member.id)
        if int(challenger.get("wallet") or 0) < int(stake):
            return await interaction.response.send_message(
                embed=util.err_embed(bag.get("not_enough")), ephemeral=True
            )
        if int(opponent.get("wallet") or 0) < int(stake):
            return await interaction.response.send_message(
                embed=util.err_embed(f"{member.display_name} can't cover that stake."),
                ephemeral=True,
            )

        await interaction.response.defer(thinking=True)
        game_id = await self.bot.db.create_game(
            interaction.guild.id, interaction.channel_id, "duel",
            {"a": interaction.user.id, "b": member.id, "stake": int(stake)},
        )

        embed = util.base_embed(
            settings, title="⚔️ Duel",
            description=bag.get("duel_challenge", challenger=interaction.user.mention,
                                opponent=member.mention,
                                amount=util.coins(int(stake), settings)),
        )
        embed.set_footer(text="Rock, paper, scissors. Both players pick privately.")
        view = views.DuelView(game_id, member.id, self.on_duel_accept, self.on_duel_decline)
        message = await interaction.followup.send(content=member.mention, embed=embed, view=view)
        await self.bot.db.update_game(game_id, message_id=message.id)

    async def on_duel_decline(self, interaction: discord.Interaction,
                              view: views.DuelView) -> None:
        settings, bag = await self.context(interaction.guild.id)
        await self.bot.db.finish_game(view.game_id, status="declined")
        await interaction.response.edit_message(
            embed=util.base_embed(
                settings, title="⚔️ Duel declined",
                description=bag.get("duel_declined", opponent=interaction.user.mention),
            ),
            view=view,
        )

    async def on_duel_accept(self, interaction: discord.Interaction,
                             view: views.DuelView) -> None:
        game = await self.bot.db.get_game(view.game_id)
        if not game or game.get("status") != "running":
            return await interaction.response.send_message(
                "That duel is no longer open.", ephemeral=True
            )
        settings, bag = await self.context(interaction.guild.id)
        state = game.get("state") or {}
        stake = int(state.get("stake") or 0)

        # Take both stakes up front so neither side can spend them mid-duel.
        try:
            await self.bot.db.debit(interaction.guild.id, int(state["a"]), stake,
                                    kind="duel", note="duel stake")
        except InsufficientFunds:
            await self.bot.db.finish_game(view.game_id, status="cancelled")
            return await interaction.response.send_message(
                embed=util.err_embed("The challenger can't cover the stake any more."),
            )
        try:
            await self.bot.db.debit(interaction.guild.id, int(state["b"]), stake,
                                    kind="duel", note="duel stake")
        except InsufficientFunds:
            await self.bot.db.credit(interaction.guild.id, int(state["a"]), stake,
                                     kind="refund", note="duel cancelled")
            await self.bot.db.finish_game(view.game_id, status="cancelled")
            return await interaction.response.send_message(
                embed=util.err_embed("You can't cover that stake any more."), ephemeral=True
            )

        await self.bot.db.join_game(view.game_id, int(state["a"]), stake)
        await self.bot.db.join_game(view.game_id, int(state["b"]), stake)

        await interaction.response.edit_message(
            embed=util.base_embed(
                settings, title="⚔️ Duel accepted",
                description="Both of you have been sent a private picker. "
                            "First to choose doesn't matter — nobody sees the other's pick.",
            ),
            view=view,
        )
        for user_id in (int(state["a"]), int(state["b"])):
            member = interaction.guild.get_member(user_id)
            if member is None:
                continue
            try:
                await member.send(
                    embed=util.base_embed(
                        settings, title="⚔️ Your duel pick",
                        description=f"Stake: {util.coins(stake, settings)} on "
                                    f"**{interaction.guild.name}**",
                    ),
                    view=views.ChoiceView(view.game_id, user_id, self.on_duel_choice),
                )
            except (discord.Forbidden, discord.HTTPException):
                # If we can't DM them, fall back to a random pick so the other
                # player isn't left hanging with their coins locked up.
                await self.bot.db.set_player_data(
                    view.game_id, user_id, {"choice": _rng.choice(["rock", "paper", "scissors"])}
                )
        await self.maybe_resolve_duel(interaction.guild, view.game_id)

    async def on_duel_choice(self, interaction: discord.Interaction,
                             view: views.ChoiceView, choice: str) -> None:
        await self.bot.db.set_player_data(view.game_id, interaction.user.id, {"choice": choice})
        await interaction.response.edit_message(
            content=f"You picked **{choice}**. Waiting for the other player…", view=view
        )
        game = await self.bot.db.get_game(view.game_id)
        if game:
            guild = self.bot.get_guild(int(game["guild_id"]))
            if guild is not None:
                await self.maybe_resolve_duel(guild, view.game_id)

    async def maybe_resolve_duel(self, guild: discord.Guild, game_id: int) -> None:
        game = await self.bot.db.get_game(game_id)
        if not game or game.get("status") != "running":
            return
        players = await self.bot.db.game_players(game_id)
        if len(players) < 2:
            return
        choices = {int(p["user_id"]): (p.get("data") or {}).get("choice") for p in players}
        if any(c is None for c in choices.values()):
            return

        state = game.get("state") or {}
        a_id, b_id = int(state["a"]), int(state["b"])
        stake = int(state.get("stake") or 0)
        if not await self.bot.db.finish_game(game_id):
            return

        settings, bag = await self.context(guild.id)
        winner_index = economy.duel_winner(choices[a_id], choices[b_id])
        channel = guild.get_channel(int(game["channel_id"]))

        if winner_index is None:
            for user_id in (a_id, b_id):
                await self.bot.db.credit(guild.id, user_id, stake,
                                         kind="refund", note="duel drawn")
            text = bag.get("duel_draw", a=f"<@{a_id}>", b=f"<@{b_id}>")
        else:
            winner_id = a_id if winner_index == 0 else b_id
            loser_id = b_id if winner_index == 0 else a_id
            await self.bot.db.credit(guild.id, winner_id, stake * 2,
                                     kind="duel_win", note="won a duel")
            text = bag.get("duel_won", winner=f"<@{winner_id}>", loser=f"<@{loser_id}>",
                           amount=util.coins(stake * 2, settings))

        detail = " · ".join(f"<@{uid}> picked **{c}**" for uid, c in choices.items())
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(
                    embed=util.base_embed(settings, title="⚔️ Duel result",
                                          description=f"{text}\n\n{detail}")
                )
            except discord.HTTPException:
                pass

    # ─── co-op heist ──────────────────────────────────────────────────────────
    @games.command(name="heist", description="Start a heist — everyone who joins gets paid")
    async def heist(self, interaction: discord.Interaction) -> None:
        opened = await self.games_open(interaction)
        if opened is None:
            return
        settings, bag = opened

        remaining = economy.cooldown_remaining(
            parse_ts(settings.get("last_heist")), int(settings.get("heist_cooldown") or 0)
        )
        if remaining > 0:
            return await interaction.response.send_message(
                embed=util.err_embed(
                    f"The crew is still lying low. Next heist {util.in_seconds(remaining)}."
                ),
                ephemeral=True,
            )
        if await self.bot.db.active_game(interaction.guild.id, "heist"):
            return await interaction.response.send_message(
                embed=util.err_embed("A heist is already being planned."), ephemeral=True
            )

        needed = int(settings.get("heist_min_players") or 3)
        seconds = int(settings.get("heist_seconds") or 90)
        await interaction.response.defer(thinking=True)
        await self.bot.db.update_guild(interaction.guild.id, last_heist=utcnow())

        game_id = await self.bot.db.create_game(
            interaction.guild.id, interaction.channel_id, "heist", {"needed": needed},
            ends_at=utcnow() + dt.timedelta(seconds=seconds),
        )
        closes = utcnow() + dt.timedelta(seconds=seconds)
        embed = util.base_embed(
            settings, title="🧨 Heist forming",
            description=bag.get("heist_started", needed=needed, when=util.ts(closes, "R")),
        )
        embed.set_footer(text="No entry fee. The more people who join, the more everyone gets.")
        view = views.HeistView(game_id, self.on_heist_join, timeout=seconds)
        message = await interaction.followup.send(embed=embed, view=view)
        await self.bot.db.update_game(game_id, message_id=message.id)

        await asyncio.sleep(seconds)
        view.closed = True
        await self.resolve_heist(interaction.guild, game_id, message, view)

    async def on_heist_join(self, interaction: discord.Interaction,
                            view: views.HeistView) -> None:
        game = await self.bot.db.get_game(view.game_id)
        if not game or game.get("status") != "running":
            return await interaction.response.send_message(
                "This heist has already gone ahead.", ephemeral=True
            )
        joined = await self.bot.db.join_game(view.game_id, interaction.user.id, 0)
        players = await self.bot.db.game_players(view.game_id)
        if not joined:
            return await interaction.response.send_message(
                "You're already in on this one.", ephemeral=True
            )
        await interaction.response.send_message(
            f"🧨 You're in. **{len(players)}** on the crew so far.", ephemeral=True
        )

    async def resolve_heist(self, guild: discord.Guild, game_id: int,
                            message: discord.Message, view: views.HeistView) -> None:
        if not await self.bot.db.finish_game(game_id):
            return
        settings, bag = await self.context(guild.id)
        players = await self.bot.db.game_players(game_id)
        needed = int(settings.get("heist_min_players") or 3)

        for child in view.children:
            child.disabled = True
        try:
            await message.edit(view=view)
        except discord.HTTPException:
            pass

        if len(players) < needed:
            try:
                await message.reply(
                    embed=util.base_embed(
                        settings, title="🧨 Heist called off",
                        description=bag.get("heist_failed")
                        + f"\nOnly {len(players)} of {needed} turned up.",
                    )
                )
            except discord.HTTPException:
                pass
            return

        each = economy.heist_payout(
            int(settings.get("heist_base") or 0), len(players),
            float(settings.get("heist_bonus_percent") or 0),
        )
        wallet_cog = self.bot.get_cog("Wallet")
        for player in players:
            if wallet_cog is not None:
                await wallet_cog.award(guild.id, int(player["user_id"]), each,
                                       "heist", settings)
            else:
                await self.bot.db.credit(guild.id, int(player["user_id"]), each, kind="heist")

        mentions = ", ".join(f"<@{p['user_id']}>" for p in players[:20])
        try:
            await message.reply(
                embed=util.base_embed(
                    settings, title="🧨 Heist succeeded",
                    description=bag.get("heist_success", players=len(players),
                                        amount=util.coins(each, settings))
                    + f"\n\n{mentions}",
                )
            )
        except discord.HTTPException:
            pass

    # ─── staff: trivia questions ──────────────────────────────────────────────
    trivia_group = app_commands.Group(
        name="trivia", description="Manage trivia questions", guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @trivia_group.command(name="add", description="Add a trivia question (staff)")
    @app_commands.describe(
        question="The question", answer="The correct answer",
        wrong1="A wrong answer", wrong2="A wrong answer", wrong3="A wrong answer",
    )
    async def trivia_add(self, interaction: discord.Interaction, question: str,
                         answer: str, wrong1: str, wrong2: str, wrong3: str) -> None:
        settings = await self.bot.db.get_guild(interaction.guild.id)
        if not util.is_staff(interaction.user, settings):
            return await interaction.response.send_message(
                embed=util.err_embed("Staff only."), ephemeral=True
            )
        answers = [answer.strip()[:90], wrong1.strip()[:90],
                   wrong2.strip()[:90], wrong3.strip()[:90]]
        if len({a.lower() for a in answers}) < 4:
            return await interaction.response.send_message(
                embed=util.err_embed("All four answers need to be different."), ephemeral=True
            )
        qid = await self.bot.db.add_question(
            interaction.guild.id, question.strip()[:300], answers, 0, "custom"
        )
        count = await self.bot.db.count_custom_questions(interaction.guild.id)
        await interaction.response.send_message(
            embed=util.ok_embed(
                settings,
                f"Added question `#{qid}`. This server now has **{count}** of its own, "
                "on top of the built-in bank.",
            ),
            ephemeral=True,
        )

    @trivia_group.command(name="remove", description="Remove one of your questions (staff)")
    @app_commands.describe(id="The question number")
    async def trivia_remove(self, interaction: discord.Interaction, id: int) -> None:
        settings = await self.bot.db.get_guild(interaction.guild.id)
        if not util.is_staff(interaction.user, settings):
            return await interaction.response.send_message(
                embed=util.err_embed("Staff only."), ephemeral=True
            )
        if await self.bot.db.delete_question(int(id), interaction.guild.id):
            await interaction.response.send_message(
                embed=util.ok_embed(settings, f"Removed question `#{id}`."), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=util.err_embed(
                    "No such question on this server. You can't remove built-in ones, "
                    "but they can be switched off wholesale in `/config games`."
                ),
                ephemeral=True,
            )

    @trivia_group.command(name="list", description="Your server's own questions (staff)")
    async def trivia_list(self, interaction: discord.Interaction) -> None:
        settings = await self.bot.db.get_guild(interaction.guild.id)
        if not util.is_staff(interaction.user, settings):
            return await interaction.response.send_message(
                embed=util.err_embed("Staff only."), ephemeral=True
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await self.bot.db.get_questions(interaction.guild.id, include_builtin=False)
        if not rows:
            return await interaction.followup.send(
                embed=util.base_embed(
                    settings,
                    description=f"No custom questions yet — the built-in bank of "
                                f"{len(BUILTIN_QUESTIONS)} is in use. Add your own with "
                                "`/trivia add`.",
                ),
                ephemeral=True,
            )
        lines = [f"`#{r['id']}` {util.truncate(r['question'], 80)}" for r in rows[:25]]
        await interaction.followup.send(
            embed=util.base_embed(
                settings, title=f"🧠 Custom questions ({len(rows)})",
                description="\n".join(lines),
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Games(bot))
