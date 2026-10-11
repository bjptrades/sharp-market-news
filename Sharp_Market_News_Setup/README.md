# Sharp Market News — Discord news bot

Posts selected stock-market headlines from free RSS feeds in `#market-news` using GitHub Actions and a Discord webhook. **Not a live market newswire.** No paid data plan required; feeds may be delayed or unavailable.

## What it covers

- MarketWatch Top Stories (broad market news)
- Yahoo Finance headlines for SPY (stocks and broad market)
- Federal Reserve monetary-policy press releases (official source)
- Google News RSS search for US market topics (fallback/aggregation; not a direct publisher)

All sources are third-party services outside our control and some may block GitHub or stop offering RSS without notice. The bot reports source errors in Actions logs. **Do not use these alerts for split-second trade execution.** News publishers own their headlines and content; the bot posts only headline, URL, attribution, and published timestamp.

## Setup

1. In Discord, open `#market-news` → Edit Channel → Integrations → Webhooks → New Webhook. Name it **Sharp Market News** and copy its URL. Never share it publicly.
2. Create a **private** GitHub repository, e.g. `sharp-market-news`.
3. Upload `news_bot.py`, `requirements.txt`, `README.md` to the repository root. Create `.github/workflows/market-news.yml` at that exact path (GitHub's Upload files flow may otherwise put everything into an extra enclosing folder).
4. GitHub → **Settings → Secrets and variables → Actions → Secrets** → New repository secret: `DISCORD_WEBHOOK_URL` = your Discord webhook URL.
5. Recommended: create a Discord self-assignable role `📰 News Alerts`. Copy its numeric role ID via Discord Developer Mode. In GitHub → **Actions → Variables**, set `DISCORD_ROLE_ID` = role ID; `PING_MODE` = `important` (recommended), `all` (will ping for every posted article), or `none`.
6. Open GitHub **Actions → Sharp Market News → Run workflow**, set mode `test`. Confirm Discord receives a test message, with **no** role ping.
7. Run `preview`: check **Check feeds and send relevant news** logs. This mode sends nothing to Discord and never changes duplicate history.
8. Run `live`: the **first** live run seeds the last few hours of headlines without sending any, to prevent old stories flooding your channel. The next run posts newly discovered recent stories.
9. Scheduled scans run **every 15 minutes on weekdays, 8 AM–7:59 PM Eastern** (may be delayed or occasionally skipped by GitHub). Change the workflow schedule as needed. On a newly created private repo, scheduled GitHub Actions uses your account's included minutes and may incur charges if you exceed your plan allowance. Check usage under GitHub billing.
10. If Koya or another news bot is already posting into `#market-news`, disable its overlapping RSS notifications to avoid duplicate feeds.

## Important behavior

- At most **3** newly discovered stories per scan. If more come through, remaining recent stories stay queued until later scans.
- Stories older than **3 hours** are never treated as new news. Published time is taken from the RSS feed and may be inaccurate or absent.
- Duplicates are tracked using GitHub Actions cache. Caches can be evicted, after which the bot silently seeds its database again; alerts could be missed but should not flood.
- `PING_MODE=important` uses a simple headline keyword filter and is **not an evidence-based impact classification**. `all` can generate excessive notifications; prefer `important` or `none`.
- Discord roles must be mentionable or the webhook must have suitable permissions. Members can opt out of push notifications.
- The first `live` run only verifies retrieval and initializes the cache, while `test` verifies Discord connectivity.
- If all feeds fail, the GitHub job fails with logs rather than inventing headlines. If one fails, other feeds can still work.

## Local testing (optional)

`python -m pip install -r requirements.txt`
`python -m unittest discover -s tests -v`

## Reminder

Price-action analysis, TJR trading concepts, and trade decisions belong in separate educational channels. A headline is **context**, not a buying or selling signal.
