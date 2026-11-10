# Telegram Giveaway Hoster Bot

Multi-host giveaway bot (aiogram 3 · SQLAlchemy 2 async · PostgreSQL · Redis · APScheduler · Pillow). Telegram-only, no web UI.

## Deploy on Railway
1. New project → **Deploy from repo** (or `railway up`).
2. Add the **PostgreSQL** and **Redis** plugins (they inject `DATABASE_URL`, `REDIS_URL`).
3. Set variables: `BOT_TOKEN`, `BOT_OWNER_ID`; optional `LOG_CHANNEL_ID`.
4. Deploy. The start command runs `alembic upgrade head && python bot.py`; migrations also run on boot.

## Bot setup
- Make the bot **admin** in every required channel (needed to read membership).
- Owner: `/panel`, `/addchannel`, `/delchannel`, `/listchannels`, `/channelsettings`, `/testchannels`, `/ban`, `/unban`, `/setlog`, `/broadcast`.
- Hidden: `/winner <giveaway_id> <user_id|@username>` (owner only; never registered, never in /help; silent for everyone else).

## Design notes
- Giveaway due-times live in PostgreSQL; the APScheduler job (persistent SQLAlchemy store) sweeps every 15 s, so restarts/redeploys lose nothing. A Redis lock + `SELECT … FOR UPDATE` make the draw atomic.
- Log events are written in the same transaction as the change and delivered to `LOG_CHANNEL_ID` with automatic retry.
- Join-request channels: a pending request is reported as `left` by Telegram, so it never counts as a subscription.

## Tests
`pip install -r requirements.txt && pytest`
