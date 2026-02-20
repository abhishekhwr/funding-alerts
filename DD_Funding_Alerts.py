import feedparser
import requests
import time
import json
import os
import re
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
import threading
from difflib import SequenceMatcher
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TELEGRAM_TOKEN = "***REVOKED_TELEGRAM_TOKEN***"
CHAT_ID = "***REDACTED_CHAT_ID***"
SEEN_FILE = "/data/seen_entries.json"
SENT_TODAY_FILE = "/data/sent_today.json"
POLL_INTERVAL = 600
MAX_AUTO_ALERTS = 3
MAX_LATEST_ALERTS = 7

FEEDS = [
    "https://entrackr.com/feed/",
    "https://inc42.com/feed/",
    "https://yourstory.com/feed",
    "https://www.vccircle.com/feed",
    "https://economictimes.indiatimes.com/small-biz/startups/rss.cms",
    "https://www.livemint.com/rss/startup",
    "https://news.google.com/rss/search?q=india+startup+funding+raised&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+series+a+series+b+raised&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+startup+acquisition+merger&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+startup+debt+financing&hl=en-IN&gl=IN&ceid=IN:en",
]

KEYWORDS = [
    "funding", "raises", "raised", "series a", "series b", "series c",
    "seed round", "pre-seed", "investment", "crore", "million",
    "acquisition", "acquires", "acquired", "merger", "stake",
    "debt financing", "venture debt", "ncd", "debenture"
]

EXCLUDE_KEYWORDS = [
    "upsc", "exam", "syllabus", "ias", "government scheme",
    "budget allocation", "policy", "startup india fund",
    "fund of funds" 
]

# --- Utility ---

def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def save_json(path, data, limit=1000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data[-limit:], f)

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

def time_ago(entry):
    try:
        published = entry.get("published_parsed")
        if not published:
            return "Recently"
        pub_date = datetime(*published[:6], tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - pub_date
        if diff.seconds < 3600:
            return f"{diff.seconds // 60}m ago"
        elif diff.days == 0:
            return f"{diff.seconds // 3600}h ago"
        else:
            return f"{diff.days}d ago"
    except:
        return "Recently"

def is_recent(entry, days=14):
    try:
        published = entry.get("published_parsed")
        if not published:
            return True
        pub_date = datetime(*published[:6], tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return pub_date >= cutoff
    except:
        return True

def is_relevant(entry):
    text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
    if any(ex in text for ex in EXCLUDE_KEYWORDS):
        return False
    return any(kw in text for kw in KEYWORDS)

def is_duplicate(title, seen_titles, threshold=0.5):
    title_words = set(title.lower().split())
    for seen in seen_titles:
        seen_words = set(seen.lower().split())
        overlap = len(title_words & seen_words) / max(len(title_words), 1)
        if overlap > threshold:
            return True
        key_terms = set(w for w in title.split() if w[0].isupper() and len(w) > 3)
        seen_terms = set(w for w in seen.split() if w[0].isupper() and len(w) > 3)
        if len(key_terms & seen_terms) >= 2:
            return True
    return False

def fuzzy_match(query, text, threshold=0.6):
    query = query.lower()
    text = text.lower()
    if query in text:
        return True
    ratio = SequenceMatcher(None, query, text).ratio()
    return ratio >= threshold

# --- Entity Extraction ---

def extract_entities(title, summary):
    text = title + " " + clean_text(summary)

    # Amount
    amount = ""
    amount_match = re.search(
        r'(\$[\d,.]+\s*(?:million|billion|mn|bn|M|B)|\₹[\d,.]+\s*(?:crore|lakh|cr)?|[\d,.]+\s*(?:crore|lakh|million|billion|cr|mn|bn))',
        text, re.IGNORECASE
    )
    if amount_match:
        amount = amount_match.group(0).strip()

    # Round type
    round_type = ""
    round_match = re.search(
        r'(pre-seed|seed\s*round|series\s*[a-f][\+]?|bridge\s*round|venture\s*debt|debt\s*financing|ncd|debenture|ipo|pre-ipo|unicorn)',
        text, re.IGNORECASE
    )
    if round_match:
        round_type = round_match.group(0).strip().title()

    # Investors — handle both "led by X" and "X-led"
    investors = ""
    investor_match = re.search(
        r'(?:led by|backed by|from|investors include|participation from)\s+([A-Z][^,.]{3,60})',
        text
    )
    if investor_match:
        investors = investor_match.group(1).strip()
    else:
        # Handle "Blackstone-led" pattern
        led_match = re.search(r'([A-Z][a-zA-Z\s]+?)-led', text)
        if led_match:
            investors = led_match.group(1).strip()

    # Company name — expanded to catch more verb patterns
    company = ""
    company_match = re.search(
        r'^(?:Gen AI startup|Startup|Fintech|Edtech|SaaS)?\s*([A-Z][a-zA-Z0-9\s]{1,25}?)\s+(?:raises|raised|secures|secured|gets|closes|acquires|turns unicorn|bags)',
        title
    )
    if company_match:
        company = company_match.group(1).strip()

    return {
        "company": company,
        "amount": amount,
        "round": round_type,
        "investors": investors
    }
def categorize(title, summary):
    text = (title + " " + summary).lower()
    # Check roundup first before acquisition
    if any(w in text for w in ["this week", "weekly", "roundup", "wrap", "digest", "funding recap", "ecosystem"]):
        return "roundup"
    if any(w in text for w in ["acquires", "acquired", "acquisition", "merger", "stake purchase", "buys"]):
        return "acquisition"
    if any(w in text for w in ["debt", "ncd", "debenture", "venture debt", "credit facility", "term loan"]):
        return "debt"
    if any(w in text for w in ["new fund", "fund launch", "announces fund", "raises fund"]):
        return "new_fund"
    return "funding"

# --- Message Formatting ---

def format_message(entry, url):
    title = entry.title
    summary = entry.get("summary", "")
    source = get_source(url)
    age = time_ago(entry)
    entities = extract_entities(title, clean_text(summary))
    category = categorize(title, summary)

    if category == "acquisition":
        emoji = "🤝"
        label = "ACQUISITION ALERT"
    elif category == "debt":
        emoji = "💳"
        label = "DEBT FINANCING"
    elif category == "roundup":
        emoji = "📊"
        label = "FUNDING DIGEST"
    elif category == "new_fund":
        emoji = "🏦"
        label = "NEW FUND"
    else:
        emoji = "🚨"
        label = "FUNDING ALERT"

    lines = [f"{emoji} <b>{label}</b>\n"]

    if entities["company"]:
        lines.append(f"<b>Company:</b> {entities['company']}")
    if entities["round"]:
        lines.append(f"<b>Round:</b> {entities['round']}")
    if entities["amount"]:
        lines.append(f"<b>Amount:</b> {entities['amount']}")
    if entities["investors"]:
        lines.append(f"<b>Investors:</b> {entities['investors']}")

    lines.append(f"\n<i>{title}</i>")
    lines.append(f"\n📰 {source}  ·  🕐 {age}")

    return "\n".join(lines), url, entities.get("company", "")

def send_alert(entry, url):
    message, url, company = format_message(entry, url)

    keyboard = [[
        InlineKeyboardButton("📄 Read Article", url=url),
    ]]
    if company:
        keyboard.append([
            InlineKeyboardButton(f"🔍 Search {company}", callback_data=f"search:{company}"),
        ])
    keyboard.append([
        InlineKeyboardButton("❌ Not Relevant", callback_data="not_relevant")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "reply_markup": reply_markup.to_dict()
        }
    )

def send_text(text):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    )

# --- Feed Fetching ---

def fetch_and_alert(seen, sent_titles, max_alerts=MAX_AUTO_ALERTS):
    new_seen = []
    new_sent = []
    count = 0

    for feed_url in FEEDS:
        if count >= max_alerts:
            break
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                if count >= max_alerts:
                    break
                entry_id = entry.get("id") or entry.get("link")
                if entry_id not in seen:
                    if is_relevant(entry) and is_recent(entry) and not is_duplicate(entry.title, sent_titles + new_sent):
                        real_url = resolve_url(entry.link)
                        send_alert(entry, real_url)
                        new_sent.append(entry.title)
                        count += 1
                    new_seen.append(entry_id)
        except Exception as e:
            print(f"Feed error: {e}")

    return new_seen, new_sent

# --- Bot Commands ---

async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching latest funding news...")
    sent_today = load_json(SENT_TODAY_FILE)
    seen = load_json(SEEN_FILE)

    new_seen, new_sent = fetch_and_alert(seen, sent_today, max_alerts=MAX_LATEST_ALERTS)

    seen = list(set(seen + new_seen))
    sent_today = list(set(sent_today + new_sent))
    save_json(SEEN_FILE, seen)
    save_json(SENT_TODAY_FILE, sent_today)

    if not new_sent:
        await update.message.reply_text("No new funding activity found right now.")

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /search <company name>")
        return

    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Searching for <b>{query}</b>...", parse_mode="HTML")

    results = []
    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                text = entry.title + " " + entry.get("summary", "")
                if fuzzy_match(query, text) and is_relevant(entry):
                    days_old = 999
                    try:
                        pub = entry.get("published_parsed")
                        if pub:
                            pub_date = datetime(*pub[:6], tzinfo=timezone.utc)
                            days_old = (datetime.now(timezone.utc) - pub_date).days
                    except:
                        pass
                    results.append((days_old, entry))
        except:
            pass

    # Sort by recency
    results.sort(key=lambda x: x[0])

    if not results:
        await update.message.reply_text(f"No articles found for <b>{query}</b>.", parse_mode="HTML")
        return

    # Check if all results are older than 14 days
    recent = [r for r in results if r[0] <= 14]
    if not recent:
        oldest_days = results[0][0]
        await update.message.reply_text(
            f"No articles in the last 14 days. Showing results from up to {oldest_days} days ago."
        )

    sent = []
    for days_old, entry in results[:5]:
        if not is_duplicate(entry.title, sent):
            real_url = resolve_url(entry.link)
            send_alert(entry, real_url)
            sent.append(entry.title)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    seen = load_json(SEEN_FILE)
    sent_today = load_json(SENT_TODAY_FILE)
    await update.message.reply_text(
        f"✅ <b>Bot Status</b>\n\n"
        f"📊 Articles tracked: {len(seen)}\n"
        f"📬 Sent today: {len(sent_today)}\n"
        f"⏱ Check interval: every 10 minutes\n"
        f"📡 Feeds monitored: {len(FEEDS)}",
        parse_mode="HTML"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 <b>Available Commands</b>\n\n"
        "/latest — fetch up to 7 fresh funding alerts\n"
        "/search <i>name</i> — fuzzy search for a company\n"
        "/status — bot health and stats\n"
        "/help — this menu\n\n"
        "<i>Alerts are sent automatically every 10 minutes when new articles are detected.</i>",
        parse_mode="HTML"
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("search:"):
        company = query.data.split("search:")[1]
        context.args = company.split()
        await cmd_search(update, context)
    elif query.data == "not_relevant":
        await query.message.reply_text("Got it. This helps improve filtering over time.")

# --- Main ---

def polling_loop():
    seen = load_json(SEEN_FILE)
    sent_today = load_json(SENT_TODAY_FILE)
    last_reset = datetime.now().date()

    while True:
        try:
            # Reset sent_today at midnight
            today = datetime.now().date()
            if today != last_reset:
                sent_today = []
                save_json(SENT_TODAY_FILE, sent_today)
                last_reset = today

            new_seen, new_sent = fetch_and_alert(seen, sent_today)
            seen = list(set(seen + new_seen))
            sent_today = list(set(sent_today + new_sent))
            save_json(SEEN_FILE, seen)
            save_json(SENT_TODAY_FILE, sent_today)
        except Exception as e:
            print(f"Polling error: {e}")
        time.sleep(POLL_INTERVAL)

def main():
    print("Bot started...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("latest", cmd_latest))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_callback))

    t = threading.Thread(target=polling_loop, daemon=True)
    t.start()

    app.run_polling()

if __name__ == "__main__":
    main()
