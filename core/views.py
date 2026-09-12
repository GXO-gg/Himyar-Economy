"""
Himyar Economy — interactive views.

Game views are short-lived on purpose: every game also has a row in the database
with its stakes recorded, so if the bot restarts mid-game the stakes are refunded
rather than quietly kept.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import discord

log = logging.getLogger(__name__)


class TriviaView(discord.ui.View):
    """Four answer buttons. First correct answer ends the round."""

    def __init__(self, game_id: int, answers: list[str], on_answer: Callable,
                 timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.game_id = game_id
        self.on_answer = on_answer
        self.finished = False
        for index, answer in enumerate(answers[:4]):
            self.add_item(TriviaButton(index, answer))


class TriviaButton(discord.ui.Button):
    LABELS = ["🇦", "🇧", "🇨", "🇩"]

    def __init__(self, index: int, answer: str):
        super().__init__(
            label=(answer or f"Option {index + 1}")[:80],
            emoji=self.LABELS[index] if index < len(self.LABELS) else None,
            style=discord.ButtonStyle.primary,
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view: TriviaView = self.view
        if view.finished:
            return await interaction.response.send_message(
                "This round is already over.", ephemeral=True
            )
        await view.on_answer(interaction, view, self.index)


class DuelView(discord.ui.View):
    """Accept or decline a duel. Only the challenged member may press."""

    def __init__(self, game_id: int, opponent_id: int, on_accept: Callable,
                 on_decline: Callable, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.game_id = game_id
        self.opponent_id = opponent_id
        self.on_accept = on_accept
        self.on_decline = on_decline
        self.resolved = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message(
                "This duel isn't yours to answer.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Accept", emoji="⚔️", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            return await interaction.response.defer()
        self.resolved = True
        for child in self.children:
            child.disabled = True
        await self.on_accept(interaction, self)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            return await interaction.response.defer()
        self.resolved = True
        for child in self.children:
            child.disabled = True
        await self.on_decline(interaction, self)


class ChoiceView(discord.ui.View):
    """Rock / paper / scissors picker, sent privately to each duellist."""

    def __init__(self, game_id: int, user_id: int, on_choice: Callable,
                 timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.game_id = game_id
        self.user_id = user_id
        self.on_choice = on_choice
        for label, emoji in (("rock", "🪨"), ("paper", "📄"), ("scissors", "✂️")):
            self.add_item(ChoiceButton(label, emoji))


class ChoiceButton(discord.ui.Button):
    def __init__(self, choice: str, emoji: str):
        super().__init__(label=choice.capitalize(), emoji=emoji,
                         style=discord.ButtonStyle.secondary)
        self.choice = choice

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ChoiceView = self.view
        for child in view.children:
            child.disabled = True
        await view.on_choice(interaction, view, self.choice)


class HeistView(discord.ui.View):
    """Join button for the co-op heist."""

    def __init__(self, game_id: int, on_join: Callable, timeout: float = 90.0):
        super().__init__(timeout=timeout)
        self.game_id = game_id
        self.on_join = on_join
        self.closed = False

    @discord.ui.button(label="Join the heist", emoji="🧨", style=discord.ButtonStyle.danger)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.closed:
            return await interaction.response.send_message(
                "This heist has already gone ahead.", ephemeral=True
            )
        await self.on_join(interaction, self)


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ This confirmation isn't yours.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class PagerView(discord.ui.View):
    def __init__(self, render: Callable, page: int, pages: int, author_id: int,
                 timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.render = render
        self.page = page
        self.pages = pages
        self.author_id = author_id
        self._sync()

    def _sync(self) -> None:
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= self.pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Run the command yourself to page through it.", ephemeral=True
            )
            return False
        return True

    async def _show(self, interaction: discord.Interaction) -> None:
        self._sync()
        embed = await self.render(interaction.guild, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self._show(interaction)

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.pages - 1, self.page + 1)
        await self._show(interaction)
