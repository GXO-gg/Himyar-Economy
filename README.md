# Himyar Economy

A virtual currency for Discord, earned from activity and spent in a shop staff
control — plus games that are about competing and co-operating rather than
gambling.

Part of the [Himyar](https://himyar.org) bot suite. Built with `discord.py`.

---

## What it does

**Earning.** Coins trickle in from chatting and from being in voice, plus `/daily`
with a streak bonus and `/work` on a cooldown. Staff can grant coins directly.

**Holding.** A wallet and a bank. Only the wallet can be robbed, which makes
`/deposit` a real decision rather than a formality.

**Spending.** A shop of permanent roles, temporary roles that expire on their own,
and custom items staff fulfil by hand.

**Games.** Trivia, guess-the-number, head-to-head duels, and a co-op heist.

## No casino framing

There are deliberately no slots, no blackjack, no roulette and no betting against
the house. Members compete with each other or work together; nothing is dressed
up as a gambling machine. This is a design choice, not an oversight.

## Multi-server by design

No guild, channel, or role id is hardcoded. Every setting and every balance lives
in SQLite keyed by `guild_id`. **There is no dashboard** — all configuration
happens in Discord.

## Commands

### Members

| Command | What it does |
| --- | --- |
| `/balance` | Wallet, bank, rank, streak, lifetime earnings |
| `/daily` | Once a day, with a streak bonus |
| `/work` | A job on a cooldown |
| `/pay` | Send coins to someone |
| `/deposit` · `/withdraw` | Move coins in and out of the bank |
| `/rob` | Risky. Costs you a fine if you're caught |
| `/rich` | The richest members, paged |
| `/shop view` · `buy` · `inventory` | Spend coins |
| `/game trivia` | First correct answer wins |
| `/game guess` | Guess the number, winner takes the pot |
| `/game duel` | Challenge someone, both stake the same |
| `/game heist` | Everyone who joins gets paid — more people means more each |

### Staff

| Command | What it does |
| --- | --- |
| `/setup` | One command to configure the server |
| `/config view` | Everything at a glance |
| `/config earning` | Chat, voice, daily, work, and the daily earning cap |
| `/config transfers` | `/pay` on/off, minimum account age, daily limit, fee, bank |
| `/config robbery` | Success rate, share taken, fine, cooldowns, protection window |
| `/config games` | Rewards, costs, stakes, heist size and cooldown |
| `/config currency` | Name, emoji, starting balance, colour |
| `/config messages` | Rewrite any text the bot sends, in any language |
| `/shop add` · `edit` · `remove` | Manage the shop |
| `/shop pending` · `fulfil` | Custom purchases waiting on staff |
| `/eco give` · `take` · `reset` | Adjust balances |
| `/eco audit` | Every coin movement, filterable by member |
| `/trivia add` · `remove` · `list` | Your own questions on top of the built-in bank |

## How the money is kept honest

Coins are the whole point of this bot, so the arithmetic is tested rather than
assumed. What's verified:

- **Transfers balance exactly.** Across 3,000 amount-and-fee combinations,
  `debit == credit + tax` always holds, and rounding never favours the sender.
- **Concurrency can't duplicate coins.** 200 simultaneous transfers from a wallet
  holding enough for 100 produce exactly 100 successes, and the total in
  circulation is unchanged. 100 simultaneous debits of a 500-coin wallet take
  exactly 500.
- **Robbery odds match the setting.** 0.3527 measured against 0.3500 configured
  over 40,000 attempts. A victim's balance never goes negative and the total never
  grows.
- **A game can only pay out once.** 20 simultaneous attempts to close one game
  produce exactly one payout — so two people answering trivia in the same
  millisecond can't both be paid.
- **Shop stock is atomic.** 20 simultaneous buys of an item with 3 in stock
  succeed exactly 3 times.
- **An interrupted game refunds everyone.** Stakes are recorded in the database,
  so if the bot restarts mid-duel the coins come back on the next start rather
  than vanishing. Refunding twice pays nothing extra.

## Anti-abuse

- **Per-command cooldowns** on everything that pays, all configurable.
- **Daily earning cap** (default 2,000) across all sources combined.
- **Transfer guards** — minimum Discord account age (default 7 days) and a daily
  send limit (default 5,000), so alts can't be farmed and funnelled.
- **Audit log** — every grant, take, transfer, robbery, purchase and payout is
  recorded. It survives a balance reset, because that's exactly when you need it.

## Robbery defaults

Deliberately cautious, because this is where economy bots generate arguments:

| Setting | Default |
| --- | --- |
| Success chance | 35% |
| Most it can take | 20% of the victim's **wallet** |
| Fine if caught | 10% of the robber's wallet |
| Cooldown | 6 hours |
| Victim protected after being robbed | 24 hours |
| Victim must be carrying | 100+ |

Banked coins are never at risk. Every number is tunable with `/config robbery`,
and the command warns you if you set something aggressive.

## Requirements

- Python 3.10+
- Privileged intents: **Server Members** and **Message Content** (the latter only
  so guess-the-number can read plain numbers typed in chat).
- Bot permissions: Send Messages, Embed Links, Read Message History, View
  Channels, Add Reactions, and Manage Roles for shop role items.

## Running it

```bash
git clone https://github.com/GXO-gg/himyar-economy.git
cd himyar-economy
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then put your token in it
python bot.py
```

### As a service (Ubuntu / systemd)

```bash
sudo cp himyar-economy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now himyar-economy
journalctl -u himyar-economy -f
```

### Environment

| Variable | Required | Default |
| --- | --- | --- |
| `DISCORD_TOKEN` | yes | — |
| `HIMYAR_DB_PATH` | no | `data/economy.db` |
| `DEV_GUILD_ID` | no | — |
| `LOG_LEVEL` | no | `INFO` |

## Layout

```
bot.py                  entry point, trivia seeding, crash refunds on start
core/economy.py         the money rules — pure, testable, no Discord objects
core/db.py              SQLite schema and every query, keyed by guild_id
core/trivia_bank.py     26 built-in questions, football and general knowledge
core/strings.py         every user-facing string + per-guild override layer
core/views.py           trivia, duel, heist and paging components
core/util.py            durations, embeds, permission checks
cogs/wallet.py          earning, /balance /daily /work /pay /deposit /rob /rich
cogs/shop.py            the shop and temporary role expiry
cogs/games.py           trivia, guess, duels, heist, trivia question management
cogs/config.py          /setup, /config, /eco staff tools, audit log
```

## Backups

Everything is in one file — `data/economy.db`. This one holds every member's
balance, so it's the most worth backing up of the whole suite.

```bash
sqlite3 data/economy.db ".backup '/home/himyar/backups/economy-$(date +%F).db'"
```

---

MIT licensed. Built for the Himyar bot suite.
