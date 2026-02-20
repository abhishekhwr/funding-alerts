import feedparser
import requests
import time
import json
import os
import re
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TELEGRAM_TOKEN = "***REVOKED_TELEGRAM_TOKEN***"
CHAT_ID = "***REDACTED_CHAT_ID***"
SEEN_FILE = "/data/seen_entries.json"
POLL_INTERVAL = 600
MAX_ALERTS_PER_RUN = 15

FEEDS = [
    # Core India startup focused
    "https://entrackr.com/feed/",
    "https://inc42.com/feed/",
    "https://yourstory.com/feed",
    "https://www.vccircle.com/feed",
    "https://theken.co/feed",
    # Broader business with startup coverage
    "https://economictimes.indiatimes.com/small-biz/startups/rss.cms",
    "https://www.livemint.com/rss/startup",
    # Google News for catchall
    "https://news.google.com/rss/search?q=india+startup+funding+raised&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+series+a+series+b+raised&hl=en-IN&gl=IN&ceid=IN:en",
]

KEYWORDS = [
    "funding", "raises", "raised", "series a", "series b", "series c",
    "seed round", "pre-seed", "investment", "crore", "million", "venture"
]

# Exclude government/macro news but keep roundups
EXCLUDE_KEYWORDS = [
    "upsc", "exam", "syllabus", "ias",
    "fund of funds", "deep-tech fund", "government fund",
    "budget allocation", "policy", "government scheme",
    "startup india fund"  # government initiative, not individual funding
]

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return json.load(f)
    return []

def save_seen(seen):
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(seen[-1000:], f)

def clean_text(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&[a-z]+;', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def resolve_url(url):
    try:
        r = requests.head(url, allow_redirects=True, timeout=5)
        return r.url
    except:
        return url

def get_source(url):
    try:
        domain = urlparse(url).netloc.replace("www.", "")
        return domain.split(".")[0].capitalize()
    except:
        return "Source"

def is_relevant(entry):
    text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
    if any(ex in text for ex in EXCLUDE_KEYWORDS):
        return False
    return any(kw in text for kw in KEYWORDS)

def is_recent(entry):
    try:
        published = entry.get("published_parsed")
        if not published:
            return True
        pub_date = datetime(*published[:6], tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        return pub_date >= cutoff
    except:
        return True

def is_duplicate(title, seen_titles):
    title_words = set(title.lower().split())
    for seen in seen_titles:
        seen_words = set(seen.lower().split())
        overlap = len(title_words & seen_words) / max(len(title_words), 1)
        if overlap > 0.6:
            return True
    return False

def send_telegram(title, url, summary=""):
    source = get_source(url)
    clean_summary = clean_text(summary)[:200]
    message = (
        f"💰 <b>{title}</b>\n\n"
        f"{clean_summary}\n\n"
        f"🔗 <a href='{url}'>Read Full Article</a>  |  📰 {source}"
    )
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
    )

def fetch_and_alert(seen, max_alerts=MAX_ALERTS_PER_RUN):
    new_seen = []
    sent_titles = []
    alert_count = 0

    for feed_url in FEEDS:
        if alert_count >= max_alerts:
            break
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                if alert_count >= max_alerts:
                    break
                entry_id = entry.get("id") or entry.get("link")
                if entry_id not in seen:
                    if is_relevant(entry) and is_recent(entry) and not is_duplicate(entry.title, sent_titles):
                        real_url = resolve_url(entry.link)
                        send_telegram(entry.title, real_url, entry.get("summary", ""))
                        sent_titles.append(entry.title)
                        alert_count += 1
                    new_seen.append(entry_id)
        except Exception as e:
            print(f"Error fetching {feed_url}: {e}")

    return new_seen

# --- Interactive Bot Commands ---

async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send latest funding articles on demand"""
    await update.message.reply_text("Fetching latest funding news...")
    seen = load_seen()
    # Temporarily lower the cap for on-demand fetch
    new_entries = fetch_and_alert(seen, max_alerts=5)
    seen = list(set(seen + new_entries))
    save_seen(seen)

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for a specific company across feeds"""
    if not context.args:
        await update.message.reply_text("Usage: /search <company name>")
        return
    query = " ".join(context.args).lower()
    await update.message.reply_text(f"Searching for '{query}'...")

    results = []
    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                if query in entry.title.lower() or query in entry.get("summary", "").lower():
                    results.append(entry)
        except:
            pass

    if not results:
        await update.message.reply_text(f"No recent articles found for '{query}'.")
        return

    for entry in results[:5]:
        real_url = resolve_url(entry.link)
        send_telegram(entry.title, real_url, entry.get("summary", ""))

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    seen = load_seen()
    await update.message.reply_text(f"✅ Bot is running\n📊 Articles tracked: {len(seen)}\n⏱ Check interval: every 10 minutes")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Available commands:\n"
        "/latest — fetch latest funding news now\n"
        "/search <name> — search for a specific company\n"
        "/status — check bot health\n"
        "/help — show this menu"
    )

def main():
    print("Bot started...")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("latest", cmd_latest))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))

    # Background polling loop
    import threading
    def polling_loop():
        seen = load_seen()
        while True:
            try:
                new_entries = fetch_and_alert(seen)
                seen = list(set(seen + new_entries))
                save_seen(seen)
            except Exception as e:
                print(f"Polling error: {e}")
            time.sleep(POLL_INTERVAL)

    t = threading.Thread(target=polling_loop, daemon=True)
    t.start()

    app.run_polling()

if __name__ == "__main__":
    main()
