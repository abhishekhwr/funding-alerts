# DD Funding Alerts Bot

A Telegram bot that monitors Indian startup funding news in real time, extracts structured deal information using an LLM, and delivers formatted alerts to a team group chat.

Built for a sales team at Datadog India to stay on top of funding activity and identify new prospects the moment they raise capital.

---

## What It Does

The bot continuously polls a curated set of Indian startup news RSS feeds every 10 minutes. When it finds a new funding article, it uses Claude Haiku to extract structured information — company name, sector, round, amount, and investors — and sends a clean, formatted alert to a Telegram group. The team can also search the archive, pull the latest news on demand, or get a one-paragraph digest instead of scrolling through individual alerts.

---

## Features

### Automatic Alerts
- Polls 10 RSS feeds every 10 minutes
- Filters for relevant funding keywords and excludes noise (UPSC, policy articles, etc.)
- Deduplicates articles across sessions so the same story doesn't appear twice
- Resets the daily sent list at midnight to allow re-alerting of ongoing stories the next day

### LLM-Powered Extraction
- Every alert runs through Claude Haiku (`claude-haiku-4-5-20251001`) to extract:
  - Company name (clean, no descriptors)
  - Sector (Fintech, SaaS, Edtech, etc.)
  - Round (Seed, Series A, Debt, etc.)
  - Amount (with correct currency and unit)
  - Lead investors
- Categorises deals automatically: Funding Alert, Debt Financing, Acquisition Alert, New Fund, or Funding Digest

### Article Archive
- Every article seen by the bot is stored locally in a persistent JSON archive (up to 5,000 entries)
- Archive grows continuously, independent of what's currently in live RSS feeds
- Powers the search command — articles don't disappear after 24 hours

### Bot Commands
| Command | Description |
|---|---|
| `/latest` | Fetches up to 7 fresh funding alerts on demand |
| `/search <name>` | Fuzzy searches the full archive for a company |
| `/summary` | Generates a 200-word LLM digest of recent funding activity |
| `/status` | Shows articles tracked, archive size, alerts sent today, feeds monitored |
| `/help` | Lists all commands |

### Alert Format
```
🚨 FUNDING ALERT

Company: Portkey
Sector: AI
Round: Series A
Amount: $15 Million
Investors: Elevation Capital

Portkey Bags $15 Mn To Help Enterprises Manage AI Spending

📰 Inc42  ·  🕐 55m ago

[📄 Read Article]  [🔍 Search Portkey]
```

---

## Architecture

```
RSS Feeds (10 sources)
       ↓
  feedparser
       ↓
  Relevance Filter → Keyword match + exclude list
       ↓
  Deduplication → Word overlap + proper noun matching
       ↓
  Archive → /data/article_archive.json (5,000 article cap)
       ↓
  Claude Haiku → Structured entity extraction (JSON)
       ↓
  Telegram Bot API → Group chat alert with inline buttons
```

Two threads run concurrently:
- **Polling loop** — runs `fetch_and_alert()` every 10 minutes in a background daemon thread
- **Bot polling** — `app.run_polling()` listens for user commands in the main thread

Persistent state is stored in three JSON files on a Railway volume:
- `seen_entries.json` — all article IDs ever seen (prevents re-alerting)
- `sent_today.json` — titles sent today (resets at midnight)
- `article_archive.json` — full article store for search and summary

---

## Tech Stack

| Component | Choice |
|---|---|
| Language | Python 3 |
| News ingestion | `feedparser` |
| Bot framework | `python-telegram-bot` v20+ |
| LLM | Anthropic Claude Haiku via `anthropic` SDK |
| Hosting | Railway (always-on worker) |
| Persistent storage | Railway volume mounted at `/data` |

---

## RSS Feed Sources

- Entrackr
- Inc42
- YourStory
- VCCircle
- Economic Times Startups
- LiveMint Startup
- Google News (4 targeted queries: funding, Series A/B, acquisitions, debt)

---

## Environment Variables

Set these in Railway before deploying:

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from @BotFather |
| `CHAT_ID` | Telegram group chat ID (negative number for groups) |
| `ANTHROPIC_KEY` | Anthropic API key |

---

## Deployment

The bot runs as a Railway worker process (not a web server).

**Procfile:**
```
worker: python DD_Funding_Alerts.py
```

**requirements.txt:**
```
feedparser
requests
python-telegram-bot
anthropic
```

Railway auto-deploys on every commit to the connected GitHub repo. The `/data` volume persists across deploys.

---

## Iteration History

The bot went through several significant iterations before reaching its current state.

### v0.1 — Basic RSS poller
Initial version polled RSS feeds and sent raw article titles to a personal Telegram chat. No filtering, no formatting, no deduplication. Proof of concept only.

### v0.2 — Keyword filtering + deduplication
Added `KEYWORDS` and `EXCLUDE_KEYWORDS` lists to filter noise. Implemented word-overlap and proper noun deduplication to prevent the same story from appearing multiple times across feeds. Added `seen_entries.json` for cross-session memory.

### v0.3 — Regex-based entity extraction
Attempted to extract company names, amounts, and rounds using regex patterns. Results were inconsistent — `₹4 Cr` was not being parsed as `₹4 Crore`, investor names were missed entirely, and sector classification didn't exist.

### v0.4 — LLM extraction via Claude Haiku
Replaced regex with a structured LLM prompt. Claude Haiku extracts all six fields (company, sector, round, amount, investors, deal_type) and returns clean JSON. Cost negligible at current volume (<$1/month). Accuracy significantly improved — amount parsing, sector tagging, and investor extraction all work reliably.

### v0.5 — Group chat + security
Moved from personal chat to a Telegram group. Migrated all credentials (bot token, chat ID, Anthropic key) from hardcoded values to Railway environment variables. Added a temporary `/getchatid` command to retrieve the group's negative chat ID, then removed it after use.

### v0.6 — Interactive buttons
Added inline keyboard buttons to each alert: **Read Article** (direct URL) and **Search Company** (Google search link). Initial implementation used Telegram callbacks which silently failed in group chats. Replaced with direct URL buttons — simpler, more reliable, no callback handler needed.

### v0.7 — Article archive + improved search
`/search` originally queried live RSS feeds, which only hold the last 10–20 articles. Companies that raised earlier in the week returned no results. Added a persistent archive that stores every article the bot sees, independent of what's currently in feeds. Search now queries the full archive — results improve the longer the bot runs.

### v0.8 — Summary command
Added `/summary` which pulls the last 20 relevant articles from the archive, sends them to Claude Haiku, and returns a 200-word digest covering deal highlights and sector trends. Useful for team members who want a quick brief rather than scrolling through individual alerts.

---

## Known Limitations

- **Archive cold start** — On first deploy, the archive is empty. Search won't return results until the bot completes at least one poll cycle (~10 minutes).
- **RSS window** — Feeds only expose recent articles. If a company raised before the bot was deployed and the article has since dropped off all feeds, it won't appear in the archive.
- **Fuzzy search sensitivity** — The 0.6 similarity threshold can occasionally return loosely related results for short or common company names.
- **Summary quality** — The digest is only as good as what's in the archive. In the first few hours after deploy, it may have limited data to work with.

---

## Potential Future Improvements

- **Sector filter** — `/search fintech` or `/latest saas` to filter alerts by sector
- **Funding threshold filter** — Only alert for deals above a certain size (e.g. >$5M)
- **Weekly automated digest** — Schedule a Sunday morning summary without requiring a manual `/summary` call
- **CRM integration** — Push alerts directly to Salesforce as new leads when a company in a target sector raises
- **Web dashboard** — Simple read-only view of the archive with filters, instead of relying entirely on Telegram commands
- **Relevance feedback** — Restore the "Not Relevant" button with actual logging, and use it to fine-tune the keyword filter over time

---

## Repository

[github.com/abhishekhwr/funding-alerts](https://github.com/abhishekhwr/funding-alerts)
