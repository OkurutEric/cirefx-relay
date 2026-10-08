# cirefx-relay

Copies new Discord posts (from the server owner, in selected channels) to Telegram
channels/topics. Runs on GitHub Actions every 5 minutes. Routes are in `relay.py`.

Secrets (Settings → Secrets and variables → Actions): `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_HQ_GROUP_ID`,
`TELEGRAM_MENTORSHIP_GROUP_ID`, `TELEGRAM_TOPICS` (JSON: group → topic name → thread id).

Run it now: Actions → relay → Run workflow.
