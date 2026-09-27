# DD Funding Alerts

A Telegram bot for following Indian startup funding news in a team group. It reads RSS feeds, checks whether stories are relevant, extracts deal details, and tries to send one alert per funding event across publishers.

The bot runs as a single Railway worker and saves its queue, history and settings on a persistent volume. It checks for news periodically; it is not a real-time or exhaustive funding database.

## Current defaults

| Setting | Default |
|---|---|
| Configured feed URLs | 22, including publisher feeds and Google News searches |
| Automatic scan interval | Target of 10 minutes between scan starts; long scans can delay the next start |
| Articles inspected | Up to the first 50 entries supplied by each feed |
| Automatic alerts | Up to 10 send attempts per cycle |
| `/latest` alerts | Up to 10 send attempts per request |
| Article eligibility window | 14 days |
| Archive size | Up to 5,000 articles |
| Operating timezone | India Standard Time, UTC+05:30 |

Reading more articles does not automatically send more alerts. Stories still need to pass the relevance, detail-quality and duplicate checks. The configured feed count is not the number of distinct publishers or proof that every feed is working; use `/feeds` to inspect actual results.

## How alerts work

1. **Read every configured feed.** The bot attempts all feeds before processing the alert queue. One failed source does not stop the others. Requests have connection and read timeouts and identify the application as FundingAlerts.
2. **Save articles and pending work.** Inspected articles with usable IDs or links enter the archive. New eligible candidates enter a saved queue, so reaching the send limit does not discard them.
3. **Check relevance.** Funding-related words shortlist candidates, then Claude checks the article's meaning. Words such as “defence,” “compliance” or “Summit Partners” do not automatically block a genuine deal. Unchecked candidates wait for another cycle instead of bypassing validation.
4. **Prepare the details.** Claude supplies the company, sector, investors and deal category. Separate rules read amounts, valuations, round names and deal status from the RSS title and opening summary. This helps distinguish money raised from company valuation, and “raising” from “raised.” Missing or unclear facts can remain unstated.
5. **Compare the funding event.** The bot checks article URLs and company/deal details against saved history. It uses bounded AI comparisons for ambiguous cases, such as rounded amounts or an omitted round name. Uncertain matches wait for another attempt. Similar headlines about different companies should not be merged merely because they share words.
6. **Send and remember.** Confirmed sends are recorded. Temporary processing or delivery failures remain eligible for retry, starting after 10 minutes and increasing to a maximum six-hour delay. Sources take turns during processing so a busy feed does not monopolise the queue.

The bot reads RSS titles and summaries; it does not fetch the full text of every article. Alerts can cover funding, venture debt, acquisitions and funding roundups. The formatter also supports new-fund announcements, subject to relevance checks.

An illustrative alert:

```text
🚨 FUNDING ALERT

Company: ExampleCo
Sector: Healthtech
Round: Pre-Series A
Status: Raised
Amount: ₹20 crore
Investors: Example Ventures

A short description from the article.

📰 Publisher · 🕐 30m ago

[Read Article] [Search Company] [Not Relevant]
```

Valuation appears separately when identified. Fields and buttons depend on the available article details.

## News coverage

| Source | Feed URLs | Coverage/access |
|---|---:|---|
| Entrackr | 3 | Main, snippets and exclusives RSS |
| Inc42 | 3 | Main, buzz and features RSS |
| YourStory | 1 | Direct RSS |
| Economic Times | 1 | Direct funding RSS |
| Mint | 1 | Direct companies RSS; broader than startup funding |
| IndianStartupNews | 1 | Direct funding RSS |
| Indian Startup Times | 1 | Direct investment RSS |
| Entrepreneur India | 1 | Direct funding-topic RSS |
| VCCircle | 1 | Targeted Google News discovery feed |
| Business Standard | 1 | Targeted Google News discovery feed |
| General Google News searches | 8 | Indian startup funding, rounds, acquisitions and debt |
| **Total** | **22** | **12 direct publisher feeds and 10 Google News feeds** |

The exact URLs and descriptive request headers are in `FEEDS`, `FEED_LABELS` and `FEED_HTTP_HEADERS` in [DD_Funding_Alerts.py](./DD_Funding_Alerts.py).

VCCircle and Business Standard use Google News alternatives because their direct endpoints failed validation. This does not restore direct access: coverage depends on Google's indexing and search results. Additional publishers can report the same event, so event matching remains part of delivery.

All 22 configured feeds returned readable, nonempty RSS in the local check on **27 September 2026 at 23:59 IST**. That is a dated observation, not a guarantee of future availability or identical results from Railway.

## Commands

Commands and feedback buttons are accepted only in the group configured by `CHAT_ID`. Private chats and other groups are ignored. This is a group restriction, not a separate administrator/user allowlist.

| Command | What it does |
|---|---|
| `/latest` | Scans feeds and processes queued articles, with up to 10 send attempts. Works even when automatic alerts are muted. |
| `/today` | Sends up to 10 eligible archived stories published today in IST, respecting shared delivery history. Does not fetch feeds. |
| `/search <name>` | Searches the saved archive and sends up to five matching verified alerts. Does not fetch feeds. |
| `/summary` | Generates a digest from eligible archived articles published in the past 14 days. |
| `/week` | Generates a digest from eligible archived articles published in the past seven days. |
| `/sector` | Shows the current sector filter and available sectors. |
| `/sector <name>` | Sets an exact, case-insensitive sector filter for automatic alerts and `/latest`, for example `/sector Healthtech`. |
| `/sector off` | Removes the sector filter. |
| `/mute` | Pauses automatic relevance processing and funding-alert delivery. Feed collection and health warnings continue. |
| `/unmute` | Resumes automatic processing and funding alerts. |
| `/status` or `/settings` | Shows configuration, archive counts, queue size and the last scan's results. |
| `/feeds` | Shows saved per-feed health, errors, last successful reads and feed links. Does not run a fresh scan. |
| `/feedback <message>` | Saves general feedback for review. |
| `/rejections` | Shows recent articles marked Not Relevant. |
| `/reset` | Shows what a reset would clear. |
| `/reset confirm` | Clears article data and processing history; see the reset note below. |
| `/help` | Lists the available commands. |

### Behaviour to know

- **Search is a deliberate replay.** Repeating `/search` can resend an archived story. Search deduplicates within that request and does not add its sends to the automatic delivery history.
- **Sector filtering is limited to automatic alerts and `/latest`.** Archive searches, `/today` and digests do not use it. Articles skipped for a sector mismatch are removed from the pending queue; changing the filter later does not automatically replay them.
- **Feedback is not model training.** The Not Relevant button saves negative examples whose titles can guide later relevance prompts. General `/feedback` messages are logged for review, not automatically applied as filtering rules.
- **Digests are partial views.** Archive commands check at most 15 eligible candidates per request. Repeated digest requests can check the same candidates, so `/summary` and `/week` are not comprehensive reports of every deal in the period.
- **Reset removes duplicate memory.** `/reset confirm` clears the archive, seen IDs, daily title list, pending queue, delivery/event history and feed-health history. It preserves mute/sector settings, general feedback and rejection examples. Older stories may be sent again after a reset. Normal code updates do not require one.

## Automatic feed-health notifications

Every scan checks whether each address responds successfully and contains readable RSS or Atom data. The bot sends a grouped warning to the configured Telegram group after **at least three consecutive failures spanning at least 20 minutes**.

The warning names the source, shows the failure reason and gives its last successful read. A continuing outage receives at most one reminder per affected feed every 24 hours. A previously reported source gets a recovery message when it becomes readable again. Notification history survives normal restarts; failed notification attempts wait at least ten minutes before retrying.

Valid RSS with zero entries counts as a successful read. A quiet search result should not be labelled a broken address. HTML pages, HTTP errors and unreadable feed responses are failures. An otherwise readable but stale feed is not automatically flagged as broken.

These checks and warnings continue during `/mute`. They detect feed failures without someone requesting `/status`, but require the bot itself to be running. They cannot predict publisher changes or report a complete bot/Railway outage; that needs an independent monitor.

### Reading `/status` and `/feeds`

- **Feeds read** counts successful feed reads in the last cycle, not distinct publishers.
- **Article entries scanned** includes entries already seen and reports of the same event from different sources; it is not a count of new deals.
- **Awaiting relevance check** means a queued story still needs a decision or is waiting for a retry.
- **Passed relevance; awaiting final checks/delivery** does not mean ready to send: extraction, sector and duplicate checks may remain.
- **Repeated events blocked** and **alerts sent** describe the last cycle, not lifetime totals.
- **Zero new articles or alerts** can be normal. Check feed health and the queue before concluding that no funding news exists.

`/feeds` provides each source's latest recorded result, last check, last successful read, entry count and newest dated article among inspected entries. An old waiting article or a growing queue deserves investigation; `/status` flags a queue of at least 500 articles or an oldest wait of at least 24 hours.

## Railway setup

The application is a long-running worker using Telegram polling, not a web server. It does not expose an HTTP healthcheck endpoint. Run **one instance** against its state files; the in-process lock does not coordinate multiple replicas.

Add the Telegram bot to the intended team group and allow it to receive commands and send messages. Use that group's ID for `CHAT_ID`; commands sent privately to the bot will be ignored.

1. Connect this GitHub repository to a Railway service.
2. Attach a persistent volume to that service with mount path **`/data`**. The code uses absolute paths under this directory. Railway volumes make files at the mount path persistent across deployments; see [Railway's volume documentation](https://docs.railway.com/volumes).
3. Add the required variables below in Railway's Variables tab. Keep credentials out of source files and commits.
4. Set or confirm the start command as `python DD_Funding_Alerts.py`. The repository's [Procfile](./Procfile) declares the same worker command; Railway also supports an explicit [start-command setting](https://docs.railway.com/deployments/start-command).
5. Deploy, inspect the logs, and run `/status` and `/feeds` in the configured group after a scan completes.

| Variable | Value |
|---|---|
| `TELEGRAM_TOKEN` | Token for the Telegram bot added to the team group |
| `CHAT_ID` | The team's Telegram group ID, usually a negative number |
| `ANTHROPIC_KEY` | Anthropic API key used for relevance, extraction, event comparisons and digests |

The application refuses to start if a required variable is missing. It uses the model identifier `claude-haiku-4-5-20251001` in `llm_call()`.

[requirements.txt](./requirements.txt) currently contains:

```text
feedparser
requests
python-telegram-bot
anthropic
```

The exact package versions and Python runtime are not yet pinned. There is no additional database service or scheduled Railway cron job required; the worker owns the polling loop.

### Updating through GitHub

Edit the existing files and commit to the branch connected to Railway. When installing a complete replacement Python file, replace its contents instead of appending another copy. With [GitHub autodeploy enabled](https://docs.railway.com/deployments/github-autodeploys), commits to the connected branch trigger deployments according to the service's configuration.

Keep the existing `/data` volume attached and verify the deployment before using commands to check behaviour. Do not clear the volume or run `/reset` as part of a normal update. A persistent volume does not replace a backup; configure and verify backups separately.

## Saved data

All seven state files are stored under `/data`:

| File | Purpose |
|---|---|
| `scan_state.json` | Pending articles, processing progress, delivery records, event matches, source rotation, last-cycle statistics and feed-health history |
| `article_archive.json` | Saved article titles, summaries, links, dates and associated delivery details; up to 5,000 entries |
| `seen_entries.json` | Recent processed article IDs; up to 1,000 entries, not an unlimited lifetime history |
| `sent_today.json` | Daily sent-title list used by commands and statistics; up to 1,000 titles |
| `bot_settings.json` | Mute state and sector filter |
| `feedback_log.json` | General feedback; up to 500 entries |
| `rejections.json` | Articles marked Not Relevant; up to 100 examples, with the latest 15 available to relevance prompts |

The pending queue survives restarts and articles disappearing from a live feed. It has no fixed count cap; articles expire beyond the 14-day age window. The scan state prunes completion and delivery records after about 30 days, while archived delivery details can remain until archive pruning. Event comparisons normally consider reports within a 14-day window.

`scan_state.json` is written to a temporary file and then replaced atomically. A damaged queue is not silently replaced with an empty one. Older archive/settings writers do not yet use that same protection. Automatic and manual delivery operations share a lock and reload state, but this is a single-process design.

## Processing limits and cost

Each queue cycle allows up to **15 relevance checks**, **10 AI event comparisons** and **30 queue-processing steps**. Extraction calls and retries are additional work; these numbers are not a total API-call or spending cap. Cached relevance decisions and event comparisons can reduce repeat work.

Costs depend on article volume, queue retries, manual commands and model usage. No fixed monthly cost is claimed. Use the Anthropic account's usage information and Railway billing to assess actual operating costs.

## Validation and remaining work

During the September 2026 updates, **192 offline regression tests passed**, covering group restrictions, funding facts, filters, cross-source duplicates, queues, retries, restarts and feed-health notifications. Telegram and AI were simulated in those tests. Separate public HTTP checks verified feed readability; neither test type establishes complete coverage or live model accuracy.

The regression suite currently lives in the development workspace, outside this repository. GitHub test automation and deployment gating are not configured. A syntax-only check can be run without starting the bot:

```bash
python -m py_compile DD_Funding_Alerts.py
```

Remaining work includes bringing the regression tests into the repository, adding GitHub checks, pinning tested dependencies/runtime, protecting all saved files against interrupted writes, and verifying backups. Once tests are in GitHub, Railway's [Wait for CI](https://docs.railway.com/deployments/github-autodeploys#wait-for-ci) can be configured separately. Digest-completeness and smaller formatting/statistics fixes remain deferred.

### Known limitations

- RSS feeds expose a limited window of articles. Stories absent from every configured feed cannot be discovered; reports previously marked seen are not automatically replayed after a filter update.
- Duplicate detection is conservative but imperfect. Unrecognised company aliases, multi-deal roundups, old reports and ambiguous facts can still cause repeats or delays. A crash after Telegram accepts a message but before local history is saved can also produce a repeat.
- AI decisions and source summaries can be wrong or incomplete. Follow the article link to check important deal details. Additional feeds do not guarantee more unique deals or faster reporting.
- The daily sent count can be inaccurate after a restart around midnight. The wider delivery history is separate from that counter.
- Some settings/digest replies still need more robust formatting. Changing a sector filter does not restore previously skipped stories.
- Feed monitoring checks availability and readability, not editorial completeness, and cannot report failure of the stopped worker itself.

Repository: [abhishekhwr/funding-alerts](https://github.com/abhishekhwr/funding-alerts)
