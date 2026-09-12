"""
Himyar Economy — every user-facing string.

Defaults are English; any server can rewrite any of them with
`/config messages set`, which is how a server runs the bot in Arabic or in a
client's own voice without touching code.
"""

from __future__ import annotations

DEFAULT_STRINGS: dict[str, str] = {
    "balance_title": "💰 {name}",
    "daily_claimed": "🗓️ You claimed {amount}! Streak: **{streak}** day(s).",
    "daily_wait": "🗓️ You've already claimed today. Come back {when}.",
    "work_done": "🛠️ {flavour} You earned {amount}.",
    "work_wait": "🛠️ You're worn out. Try again {when}.",
    "earn_capped": "You've hit today's earning cap, so this one didn't pay.",
    "pay_sent": "✅ Sent {amount} to {target}.",
    "pay_received": "💸 {sender} sent you {amount} on **{guild}**.",
    "deposit_done": "🏦 Deposited {amount}. Bank is safe from robbery.",
    "withdraw_done": "🏦 Withdrew {amount} to your wallet.",
    "rob_success": "🦝 {robber} robbed {amount} from {victim}!",
    "rob_failed": "🚨 {robber} got caught trying to rob {victim} and paid a {amount} fine.",
    "rob_wait": "🦝 Lie low for a while — try again {when}.",
    "rob_protected": "🛡️ {victim} was robbed recently and is protected for now.",
    "shop_title": "🛒 {guild} shop",
    "shop_empty": "The shop is empty. Staff can add items with `/shop add`.",
    "buy_done": "✅ You bought **{item}** for {amount}.",
    "buy_poor": "❌ You can't afford that. It costs {amount} and you have {balance}.",
    "buy_owned": "❌ You already have that role.",
    "buy_stock": "❌ That item is out of stock.",
    "rich_title": "🏆 Richest on {guild}",
    "trivia_question": "🧠 **Trivia** — first correct answer wins {amount}",
    "trivia_correct": "🧠 {user} got it right and won {amount}! The answer was **{answer}**.",
    "trivia_nobody": "🧠 Nobody got it. The answer was **{answer}**.",
    "guess_started": "🔢 Guess the number between **1** and **{max}** — type your guess here. Entry: {amount}",
    "guess_won": "🔢 {user} guessed **{number}** and took the pot of {amount}!",
    "guess_expired": "🔢 Nobody guessed **{number}** in time. The pot rolls away.",
    "duel_challenge": "⚔️ {challenger} challenges {opponent} for {amount}!",
    "duel_won": "⚔️ {winner} beat {loser} and took {amount}!",
    "duel_draw": "⚔️ A draw — {a} and {b} both keep their coins.",
    "duel_declined": "⚔️ {opponent} declined the duel.",
    "heist_started": "🧨 A heist is forming! **{needed}** people needed — everyone who joins gets paid. Closes {when}.",
    "heist_success": "🧨 The heist pulled it off — {players} members each got {amount}!",
    "heist_failed": "🧨 Not enough people showed up. The heist is off.",
    "games_disabled": "❌ Games are turned off on this server.",
    "staff_only": "❌ You need to be staff on this server to do that.",
    "not_enough": "❌ You don't have enough for that.",
}

PLACEHOLDERS: dict[str, str] = {
    "balance_title": "{name}", "daily_claimed": "{amount} {streak}",
    "daily_wait": "{when}", "work_done": "{amount} {flavour}", "work_wait": "{when}",
    "earn_capped": "—", "pay_sent": "{amount} {target}",
    "pay_received": "{sender} {amount} {guild}",
    "deposit_done": "{amount}", "withdraw_done": "{amount}",
    "rob_success": "{robber} {victim} {amount}", "rob_failed": "{robber} {victim} {amount}",
    "rob_wait": "{when}", "rob_protected": "{victim}",
    "shop_title": "{guild}", "shop_empty": "—", "buy_done": "{item} {amount}",
    "buy_poor": "{amount} {balance}", "buy_owned": "—", "buy_stock": "—",
    "rich_title": "{guild}",
    "trivia_question": "{amount}", "trivia_correct": "{user} {amount} {answer}",
    "trivia_nobody": "{answer}",
    "guess_started": "{max} {amount}", "guess_won": "{user} {number} {amount}",
    "guess_expired": "{number}",
    "duel_challenge": "{challenger} {opponent} {amount}",
    "duel_won": "{winner} {loser} {amount}", "duel_draw": "{a} {b}",
    "duel_declined": "{opponent}",
    "heist_started": "{needed} {when}", "heist_success": "{players} {amount}",
    "heist_failed": "—",
    "games_disabled": "—", "staff_only": "—", "not_enough": "—",
}

WORK_FLAVOUR = [
    "You ran the till at the corner shop.",
    "You fixed someone's wiring.",
    "You delivered a stack of parcels.",
    "You refereed a five-a-side match.",
    "You walked the neighbour's dog.",
    "You helped unload a delivery truck.",
    "You sold out of everything before noon.",
    "You covered a shift nobody else wanted.",
    "You tuned up a car engine.",
    "You tutored someone through their exams.",
]


class StringBag:
    """Per-guild strings: custom overrides on top of the English defaults."""

    def __init__(self, overrides: dict[str, str] | None = None):
        self.overrides = overrides or {}

    def raw(self, key: str) -> str:
        return self.overrides.get(key, DEFAULT_STRINGS.get(key, ""))

    def is_custom(self, key: str) -> bool:
        return key in self.overrides

    def get(self, key: str, **kwargs) -> str:
        """Format tolerantly — a staff typo in a custom string must never stop a
        payout that has already been debited."""
        template = self.raw(key)
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            out = template
            for name, value in kwargs.items():
                out = out.replace("{" + name + "}", str(value))
            return out
