"""
Himyar Economy — the money rules.

Pure functions taking plain values, no Discord objects. This is where the
arithmetic that decides who has what lives, so it can be tested directly rather
than inferred from watching balances move in a live server.

Every function that moves coins returns exactly what to add and what to subtract,
so the caller can never accidentally create or destroy currency.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass
from typing import Optional

_system_random = random.SystemRandom()


# ─── cooldowns ────────────────────────────────────────────────────────────────
def cooldown_remaining(last_used: Optional[dt.datetime], cooldown_seconds: int,
                       now: Optional[dt.datetime] = None) -> int:
    """Seconds left before a command can be used again. 0 means ready."""
    if not cooldown_seconds or last_used is None:
        return 0
    now = now or dt.datetime.now(dt.timezone.utc)
    elapsed = (now - last_used).total_seconds()
    remaining = int(cooldown_seconds) - int(elapsed)
    return max(0, remaining)


# ─── daily claim and streaks ──────────────────────────────────────────────────
def streak_after_claim(last_claim: Optional[dt.date], today: dt.date,
                       current_streak: int) -> tuple[bool, int]:
    """Work out whether /daily is available and what the streak becomes.

    Returns (can_claim, new_streak). Claiming on consecutive days builds the
    streak; missing a day resets it to 1 rather than to 0, because the day they
    come back is itself day one again.
    """
    if last_claim is None:
        return True, 1
    if last_claim >= today:
        return False, int(current_streak)          # already claimed today
    if (today - last_claim).days == 1:
        return True, int(current_streak) + 1       # consecutive
    return True, 1                                 # streak broken


def daily_payout(base: int, streak: int, bonus_per_day: int, bonus_cap: int) -> int:
    """Base amount plus a streak bonus, capped so a long streak can't run away."""
    base = max(0, int(base))
    streak = max(1, int(streak))
    bonus = min(max(0, int(bonus_cap)), max(0, int(bonus_per_day)) * (streak - 1))
    return base + bonus


# ─── daily earning cap ────────────────────────────────────────────────────────
def apply_daily_cap(amount: int, earned_today: int, cap: int) -> int:
    """Trim an award so a member's daily total never exceeds the cap.

    A cap of 0 means unlimited.
    """
    amount = max(0, int(amount))
    cap = int(cap or 0)
    if cap <= 0:
        return amount
    remaining = cap - max(0, int(earned_today))
    if remaining <= 0:
        return 0
    return min(amount, remaining)


# ─── transfers ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TransferResult:
    ok: bool
    reason: str = ""
    debit: int = 0        # taken from the sender
    credit: int = 0       # given to the recipient
    tax: int = 0          # difference, destroyed


def plan_transfer(amount: int, sender_wallet: int, *, account_age_days: float,
                  min_account_age_days: int, sent_today: int, daily_limit: int,
                  tax_percent: float, same_person: bool = False,
                  recipient_is_bot: bool = False) -> TransferResult:
    """Validate and cost a /pay. Never returns a credit larger than the debit."""
    amount = int(amount)
    if amount <= 0:
        return TransferResult(False, "Amount must be at least 1.")
    if same_person:
        return TransferResult(False, "You can't pay yourself.")
    if recipient_is_bot:
        return TransferResult(False, "You can't pay a bot.")
    if amount > int(sender_wallet):
        return TransferResult(False, "You don't have that much in your wallet.")
    if min_account_age_days and account_age_days < float(min_account_age_days):
        return TransferResult(
            False,
            f"Your Discord account needs to be at least {int(min_account_age_days)} "
            "days old to send coins.",
        )
    limit = int(daily_limit or 0)
    if limit > 0 and int(sent_today) + amount > limit:
        left = max(0, limit - int(sent_today))
        return TransferResult(
            False, f"That would pass your daily transfer limit. You can send {left:,} more today."
        )

    tax = int(amount * max(0.0, float(tax_percent or 0)) / 100)
    tax = min(tax, amount)
    return TransferResult(True, "", debit=amount, credit=amount - tax, tax=tax)


# ─── robbery ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RobberyResult:
    ok: bool
    reason: str = ""
    success: bool = False
    taken: int = 0        # moved from victim to robber
    fine: int = 0         # taken from the robber on failure


def plan_robbery(*, robber_wallet: int, victim_wallet: int, success_percent: float,
                 max_percent: float, fine_percent: float, min_victim_wallet: int,
                 same_person: bool = False, victim_is_bot: bool = False,
                 rng: Optional[random.Random] = None) -> RobberyResult:
    """Resolve a robbery attempt.

    Only the victim's wallet is ever at risk — banked coins are untouchable, which
    is what makes /deposit a real decision rather than a formality.
    """
    if same_person:
        return RobberyResult(False, "You can't rob yourself.")
    if victim_is_bot:
        return RobberyResult(False, "You can't rob a bot.")

    victim_wallet = max(0, int(victim_wallet))
    robber_wallet = max(0, int(robber_wallet))
    floor = max(0, int(min_victim_wallet))
    if victim_wallet < floor:
        return RobberyResult(
            False, f"They're carrying too little to be worth robbing (under {floor:,})."
        )

    fine = int(robber_wallet * max(0.0, float(fine_percent or 0)) / 100)
    if robber_wallet <= 0:
        return RobberyResult(False, "You need coins of your own before you can risk a robbery.")

    chooser = rng or _system_random
    if chooser.uniform(0, 100) < max(0.0, float(success_percent)):
        taken = int(victim_wallet * max(0.0, float(max_percent)) / 100)
        taken = max(1, min(taken, victim_wallet))
        return RobberyResult(True, "", success=True, taken=taken)
    return RobberyResult(True, "", success=False, fine=min(fine, robber_wallet))


# ─── games ────────────────────────────────────────────────────────────────────
def heist_payout(base_per_player: int, players: int, bonus_percent_per_player: float,
                 max_multiplier: float = 3.0) -> int:
    """Per-player payout for a co-op heist, scaling with turnout.

    The point is to reward getting people into a channel together, so the payout
    per person goes UP with more players rather than splitting a fixed pot.
    """
    players = max(0, int(players))
    if players <= 0:
        return 0
    multiplier = 1.0 + (max(0.0, float(bonus_percent_per_player)) / 100) * (players - 1)
    multiplier = min(multiplier, max(1.0, float(max_multiplier)))
    return int(max(0, int(base_per_player)) * multiplier)


def guess_hint(guess: int, target: int) -> str:
    if guess == target:
        return "correct"
    return "lower" if guess > target else "higher"


def duel_winner(choice_a: str, choice_b: str) -> Optional[int]:
    """Rock-paper-scissors. Returns 0 for A, 1 for B, None for a draw."""
    beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    a, b = (choice_a or "").lower(), (choice_b or "").lower()
    if a not in beats or b not in beats:
        return None
    if a == b:
        return None
    return 0 if beats[a] == b else 1


def split_pot(pot: int, winners: int) -> tuple[int, int]:
    """Split a pot evenly. Returns (each, remainder) so the remainder can be
    handled explicitly rather than silently vanishing."""
    pot = max(0, int(pot))
    winners = max(0, int(winners))
    if winners <= 0:
        return 0, pot
    return pot // winners, pot % winners


def format_amount(amount: int, name: str = "Coins", emoji: str = "🪙") -> str:
    return f"{emoji} **{int(amount):,}** {name}"
