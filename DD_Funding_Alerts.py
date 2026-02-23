import feedparser
import requests
import time
import json
import os
import re
import sys
from urllib.parse import urlparse, quote_plus
from datetime import datetime, timezone, timedelta
import threading
from difflib import SequenceMatcher
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import anthropic

# --- Configuration ---

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")

SEEN_FILE = "/data/seen_entries.json"
SENT_TODAY_FILE = "/data/sent_today.json"
ARCHIVE_FILE = "/data/article_archive.json"
SETTINGS_FILE = "/data/bot_settings.json"
FEEDBACK_FILE = "/data/feedback_log.json"

POLL_INTERVAL = 600
MAX_AUTO_ALERTS = 3
MAX_LATEST_ALERTS = 7
IST = timezone(timedelta(hours=5, minutes=30))

# Thread lock for shared state
state_lock = threading.Lock()

# Singleton Anthropic client
llm_client = None

def get_llm_client():
    global llm_client
    if llm_client is None:
        llm_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    return llm_client

FEEDS = [
    "https://entrackr.com/feed/",
    "https://entrackr.com/snippets/feed/",
    "https://entrackr.com/exclusive/feed/",
    "https://inc42.com/feed/",
    "https://inc42.com/buzz/feed/",
    "https://inc42.com/features/feed/",
    "https://yourstory.com/feed",
    "https://www.vccircle.com/feed",
    "https://economictimes.indiatimes.com/small-biz/startups/rss.cms",
    "https://www.livemint.com/rss/startup",
    "https://www.business-standard.com/rss/startups-10304.rss",
    "https://news.google.com/rss/search?q=india+startup+funding+raised&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+series+a+series+b+raised&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+startup+seed+funding+2026&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+startup+pre-seed+raised&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+startup+acquisition+merger&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+startup+debt+financing&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+unicorn+funding+raised&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+startup+closes+round+2026&hl=en-IN&gl=IN&ceid=IN:en",
]

KEYWORDS = [
    "funding", "raises", "raised", "series a", "series b", "series c",
    "series d", "series e", "seed round", "pre-seed", "pre-series",
    "investment", "crore", "million", " mn", " cr ",
    "acquisition", "acquires", "acquired", "merger", "stake",
    "debt financing", "venture debt", "ncd", "debenture",
    "closes round", "funding round", "leads round",
]

EXCLUDE_KEYWORDS = [
    "upsc", "exam", "syllabus", "ias", "government scheme",
    "budget allocation", "policy", "startup india fund", "fund of funds",
    "order book", "capex", "design flaw", "cag report",
    "gig levy", "gig worker", "listed company", "ipo", "q3 results",
    "quarterly results", "net profit", "revenue growth", "spends over",
    "to spend", "to invest over", "by 2028", "by 2030",
    "climate finance", "global energy", "european", "french",
    # Stock/earnings language
    "zooms", "jumps", "surges", "net zooms", "profit rises",
    "profit falls", "shares rise", "shares fall", "stock price",
    "revenue jumps", "revenue surges", "revenue falls",
    "q1 results", "q2 results", "q4 results", "annual results",
    "earnings", "dividend", "buyback", "bonus issue",
    # Events/conferences
    "summit", "conference", "expo", "event", "seminar", "webinar",
    "conclave", "forum", "award", "awards ceremony",
    # Government/policy
    "cabinet approves", "govt allocates", "ministry", "parliament",
    "regulation", "compliance", "rbi circular", "sebi",
    # Infrastructure/non-startup
    "highway", "railway", "metro", "airport", "smart city",
    "power plant", "solar park", "wind farm",
    "defence", "military", "navy", "army",
    # Loss/negative business news
    "loss widens", "loss narrows", "shuts down", "layoffs", "lays off",
    "downsizes", "bankruptcy", "insolvency", "nclt",
]

DEFAULT_SETTINGS = {
    "muted": False,
    "sector_filter": None,  # None = all sectors
}

# --- Utility ---

def load_json(path):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading {path}: {e}")
    return []

def save_json(path, data, limit=1000):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data[-limit:] if isinstance(data, list) else data, f)
    except Exception as e:
        print(f"Error saving {path}: {e}")

def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                saved = json.load(f)
                settings = dict(DEFAULT_SETTINGS)
                settings.update(saved)
                return settings
    except Exception as e:
        print(f"Error loading settings: {e}")
    return dict(DEFAULT_SETTINGS)

def save_settings(settings):
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f)
    except Exception as e:
        print(f"Error saving settings: {e}")

def parse_published_string(pub_str):
    """Parse a published date string into a list [year, month, day, hour, min, sec] or None."""
    if not pub_str or pub_str == "None":
        return None
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y %H:%M:%S %z",
        "%B %d, %Y",
    ]:
        try:
            dt = datetime.strptime(pub_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_utc = dt.astimezone(timezone.utc)
            return [dt_utc.year, dt_utc.month, dt_utc.day, dt_utc.hour, dt_utc.minute, dt_utc.second]
        except ValueError:
            continue
    return None

def backfill_archive_dates():
    """One-time backfill: add published_parsed to old archive entries that only have published string."""
    archive = load_json(ARCHIVE_FILE)
    if not archive:
        return
    patched = 0
    for entry in archive:
        if entry.get("published_parsed") is None and entry.get("published"):
            parsed = parse_published_string(entry["published"])
            if parsed:
                entry["published_parsed"] = parsed
                patched += 1
    if patched > 0:
        save_json(ARCHIVE_FILE, archive, limit=5000)
        print(f"Backfilled published_parsed for {patched} archive entries.")

def get_article_date(article):
    """Get UTC datetime from an archive article dict, trying published_parsed then published string."""
    pp = article.get("published_parsed")
    if pp:
        try:
            return datetime(*pp[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    # Fallback: parse the published string
    pub_str = article.get("published", "")
    parsed = parse_published_string(pub_str)
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None

def clean_text(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&[a-z]+;', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def markdown_to_telegram_html(text):
    """Convert common Markdown formatting to Telegram-safe HTML."""
    # Remove markdown headers (## Header → bold)
    text = re.sub(r'^#{1,4}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    # Bold: **text** or __text__ → <b>text</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    # Italic: *text* or _text_ → <i>text</i>  (but not inside URLs or already-converted tags)
    text = re.sub(r'(?<![<\w])\*([^*\n]+?)\*(?![>\w])', r'<i>\1</i>', text)
    # Markdown horizontal rules → empty line
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    # Markdown links [text](url) → just text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # Clean up excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def clean_google_news_title(title):
    """Strip trailing '- Source Name' from Google News titles."""
    return re.sub(r'\s*[-–—]\s*[A-Z][A-Za-z0-9\s\.&,]+$', '', title).strip()

def clean_description(text, max_len=200):
    """Clean and truncate description for alert display."""
    text = clean_text(text)
    # Remove trailing URLs and social media handles
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'[-–—]\s*(instagram|twitter|facebook|linkedin)\.\S+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(' ', 1)[0] + "..."
    return text

def resolve_url(url):
    try:
        # Skip resolution for non-Google-News URLs (they already have direct links)
        if "news.google.com" not in url:
            return url
        r = requests.head(url, allow_redirects=True, timeout=3)
        return r.url
    except Exception:
        return url

def get_source(url):
    try:
        domain = urlparse(url).netloc.replace("www.", "")
        return domain.split(".")[0].capitalize()
    except Exception:
        return "Source"

def time_ago(entry):
    try:
        published = entry.get("published_parsed") if hasattr(entry, 'get') else None
        if not published:
            return "Recently"
        pub_date = datetime(*published[:6], tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - pub_date
        if diff.days == 0:
            if diff.seconds < 3600:
                return f"{diff.seconds // 60}m ago"
            else:
                return f"{diff.seconds // 3600}h ago"
        else:
            return f"{diff.days}d ago"
    except Exception:
        return "Recently"

def get_published_date(entry):
    """Extract published date as datetime, returns None if unparsable."""
    try:
        published = entry.get("published_parsed") if hasattr(entry, 'get') else None
        if published:
            return datetime(*published[:6], tzinfo=timezone.utc)
        # Try parsing from string
        pub_str = entry.get("published", "") if hasattr(entry, 'get') else ""
        if pub_str:
            for fmt in ["%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"]:
                try:
                    return datetime.strptime(pub_str, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
    except Exception:
        pass
    return None

def is_recent(entry, days=14):
    pub_date = get_published_date(entry)
    if not pub_date:
        return True  # Give benefit of doubt
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return pub_date >= cutoff

def is_relevant(entry):
    text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
    if any(ex in text for ex in EXCLUDE_KEYWORDS):
        return False
    return any(kw in text for kw in KEYWORDS)

def is_relevant_text(text):
    text_lower = text.lower()
    if any(ex in text_lower for ex in EXCLUDE_KEYWORDS):
        return False
    return any(kw in text_lower for kw in KEYWORDS)

def is_duplicate(title, seen_titles, threshold=0.5):
    if not title:
        return False
    title_words = set(title.lower().split())
    for seen in seen_titles:
        if not seen:
            continue
        seen_words = set(seen.lower().split())
        overlap = len(title_words & seen_words) / max(len(title_words), 1)
        if overlap > threshold:
            return True
        key_terms = set(w for w in title.split() if w and w[0].isupper() and len(w) > 3)
        seen_terms = set(w for w in seen.split() if w and w[0].isupper() and len(w) > 3)
        if len(key_terms & seen_terms) >= 2:
            return True
    return False

def fuzzy_match(query, text, threshold=0.6):
    query = query.lower()
    text = text.lower()
    if query in text:
        return True
    # Check individual words for better partial matching
    query_words = query.split()
    if all(w in text for w in query_words):
        return True
    ratio = SequenceMatcher(None, query, text).ratio()
    return ratio >= threshold

# --- LLM Helpers ---

def llm_call(prompt, max_tokens=300, retries=2):
    """Make an LLM call with retry logic for transient failures."""
    client = get_llm_client()
    for attempt in range(retries + 1):
        try:
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            return message.content[0].text.strip()
        except Exception as e:
            print(f"LLM call error (attempt {attempt + 1}/{retries + 1}): {e}")
            if attempt < retries:
                time.sleep(2)
    return None

# --- LLM: Relevance Validation ---

def llm_is_relevant(title, summary):
    """Second-pass LLM filter: reject false positives that keyword filter let through."""
    text = f"Title: {title}\nSummary: {clean_text(summary)[:300]}"

    answer = llm_call(f"""Is this article about a specific company or startup involved in one of these events?
- Raising funding (seed, Series A/B/C/D, etc.)
- Being acquired or merging with another company
- Taking on debt financing or venture debt
- A funding roundup/weekly digest covering multiple deals

It is NOT relevant if it's about: stock market moves, quarterly earnings, government policy, events/conferences, infrastructure projects, capex plans, general business updates, IPOs, or revenue/profit reports.

{text}

Answer ONLY "yes" or "no".""", max_tokens=10, retries=1)

    if answer is None:
        return True  # Default to relevant on failure
    return answer.lower().startswith("yes")

# --- LLM: Entity Extraction ---

def extract_entities_llm(title, summary):
    prompt = f"""Extract structured information from this startup/funding news article.

Title: {title}
Summary: {clean_text(summary)[:500]}

Return ONLY a JSON object with these fields:
- company: the startup or company name only (no descriptors like "startup", "fintech firm", "platform")
- sector: primary sector (Fintech, SaaS, Edtech, Healthtech, D2C, Logistics, AI, Agritech, CleanTech, EV, Gaming, DeepTech, SpaceTech, Media, HRTech, LegalTech, InsurTech, PropTech, FoodTech, Other)
- round: funding round (Pre-Seed, Seed, Series A, Series B, Series C, Series D+, Growth, Debt, Bridge, Acquisition, Undisclosed)
- amount: full amount with currency and unit (e.g. ₹4 Crore, $12 Million). Use original currency from article.
- investors: lead investor(s) only, comma separated. Max 3.
- deal_type: one of [funding, acquisition, debt, roundup, new_fund]
- confidence: how confident are you this is a real funding/deal event? one of [high, medium, low]

IMPORTANT distinctions:
- If this is a weekly roundup or digest covering multiple deals, set deal_type to "roundup"
- If this is about a NEW FUND being launched (not a startup raising), set deal_type to "new_fund"
- Government programs allocating money are NOT funding rounds — set confidence to "low"
- Events, conferences, summits are NOT deals — set confidence to "low"
- If you can't identify a specific company raising money, set confidence to "low"

If a field is not mentioned, return empty string. Return only valid JSON."""

    raw = llm_call(prompt, max_tokens=350, retries=2)
    default = {
        "company": "", "sector": "", "round": "",
        "amount": "", "investors": "", "deal_type": "funding",
        "confidence": "low"
    }
    if raw is None:
        return default
    try:
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)
        for field in ["company", "sector", "round", "amount", "investors", "deal_type", "confidence"]:
            if field not in result:
                result[field] = "" if field != "deal_type" else "funding"
        return result
    except Exception as e:
        print(f"LLM extraction parse error: {e}")
        return default

# --- Message Formatting ---

DEAL_TYPE_CONFIG = {
    "acquisition": ("🤝", "ACQUISITION ALERT"),
    "debt": ("💳", "DEBT FINANCING"),
    "roundup": ("📊", "FUNDING ROUNDUP"),
    "new_fund": ("🏦", "NEW FUND"),
    "funding": ("🚨", "FUNDING ALERT"),
}

def format_message(title, summary, url, published_parsed=None):
    """Format an alert message. Accepts raw data instead of feedparser entry objects."""
    # Clean Google News title suffix
    display_title = clean_google_news_title(title)
    source = get_source(url)

    # Time ago calculation from published_parsed
    age = "Recently"
    if published_parsed:
        try:
            pub_date = datetime(*published_parsed[:6], tzinfo=timezone.utc)
            diff = datetime.now(timezone.utc) - pub_date
            if diff.days == 0:
                if diff.seconds < 3600:
                    age = f"{diff.seconds // 60}m ago"
                else:
                    age = f"{diff.seconds // 3600}h ago"
            else:
                age = f"{diff.days}d ago"
        except Exception:
            pass

    entities = extract_entities_llm(display_title, summary)
    deal_type = entities.get("deal_type", "funding")
    emoji, label = DEAL_TYPE_CONFIG.get(deal_type, DEAL_TYPE_CONFIG["funding"])

    lines = [f"{emoji} <b>{label}</b>\n"]

    if entities.get("company"):
        lines.append(f"<b>Company:</b> {entities['company']}")
    if entities.get("sector"):
        lines.append(f"<b>Sector:</b> {entities['sector']}")
    if entities.get("round"):
        lines.append(f"<b>Round:</b> {entities['round']}")
    if entities.get("amount"):
        lines.append(f"<b>Amount:</b> {entities['amount']}")
    if entities.get("investors"):
        lines.append(f"<b>Investors:</b> {entities['investors']}")

    desc = clean_description(summary)
    if desc and desc != display_title:
        lines.append(f"\n<i>{desc}</i>")

    lines.append(f"\n📰 {source}  ·  🕐 {age}")

    return "\n".join(lines), url, entities.get("company", ""), entities

def has_minimum_fields(entities):
    """Check if extraction has enough data to be a meaningful alert."""
    has_company = bool(entities.get("company"))
    has_detail = bool(entities.get("amount") or entities.get("round") or entities.get("investors"))
    # Roundups don't need company+detail
    if entities.get("deal_type") == "roundup":
        return True
    return has_company and has_detail

def send_alert(title, summary, url, published_parsed=None):
    """Send a formatted alert to Telegram. Returns (success, company_name)."""
    message, url, company, entities = format_message(title, summary, url, published_parsed)

    # Minimum field threshold — skip low-quality alerts
    if not has_minimum_fields(entities):
        confidence = entities.get("confidence", "low")
        if confidence == "low":
            print(f"Skipped low-quality alert: {title[:80]}")
            return False, ""

    keyboard = [[InlineKeyboardButton("📄 Read Article", url=url)]]
    if company:
        search_url = f"https://www.google.com/search?q={quote_plus(company)}+funding+India"
        keyboard.append([
            InlineKeyboardButton(f"🔍 Search {company}", url=search_url),
        ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
                "reply_markup": reply_markup.to_dict()
            },
            timeout=10
        )
        if resp.status_code == 200:
            return True, company
        else:
            print(f"Telegram API error {resp.status_code}: {resp.text[:200]}")
            return False, ""
    except Exception as e:
        print(f"Send alert error: {e}")
        return False, ""

def send_text(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"Send text error: {e}")

# --- Feed Fetching ---

def fetch_and_alert(seen, sent_titles, max_alerts=MAX_AUTO_ALERTS, sector_filter=None):
    new_seen = []
    new_sent = []
    count = 0
    llm_checks = 0
    MAX_LLM_CHECKS_PER_CYCLE = 15  # Cap LLM calls to control latency

    archive = load_json(ARCHIVE_FILE)
    archive_ids = set(a.get("id") for a in archive)  # O(1) lookups
    seen_set = set(seen)  # O(1) lookups

    for feed_url in FEEDS:
        if count >= max_alerts:
            break
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:15]:  # Cap entries per feed to avoid slow feeds
                if count >= max_alerts:
                    break
                entry_id = entry.get("id") or entry.get("link")

                archive_entry = {
                    "id": entry_id,
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", ""),
                    "url": entry.get("link", ""),
                    "published": str(entry.get("published", "")),
                    "published_parsed": list(entry.published_parsed[:6]) if hasattr(entry, 'published_parsed') and entry.published_parsed else None,
                }
                if entry_id not in archive_ids:
                    archive.append(archive_entry)
                    archive_ids.add(entry_id)

                if entry_id not in seen_set:
                    new_seen.append(entry_id)

                    # Fast keyword filters first (no LLM cost)
                    if not is_relevant(entry):
                        continue
                    if not is_recent(entry):
                        continue
                    if is_duplicate(entry.get("title", ""), sent_titles + new_sent):
                        continue

                    # LLM second-pass relevance check (capped per cycle)
                    if llm_checks < MAX_LLM_CHECKS_PER_CYCLE:
                        llm_checks += 1
                        if not llm_is_relevant(entry.get("title", ""), entry.get("summary", "")):
                            print(f"LLM rejected: {entry.get('title', '')[:80]}")
                            continue

                    # Sector filter
                    if sector_filter:
                        entities = extract_entities_llm(entry.get("title", ""), entry.get("summary", ""))
                        if entities.get("sector", "").lower() != sector_filter.lower():
                            continue

                    real_url = resolve_url(entry.get("link", ""))
                    published_parsed = entry.published_parsed[:6] if hasattr(entry, 'published_parsed') and entry.published_parsed else None

                    success, _ = send_alert(
                        entry.get("title", ""),
                        entry.get("summary", ""),
                        real_url,
                        published_parsed
                    )
                    if success:
                        new_sent.append(entry.get("title", ""))
                        count += 1
                    # If send failed, don't add to sent — will retry next cycle
        except Exception as e:
            print(f"Feed error ({feed_url[:50]}): {e}")

    save_json(ARCHIVE_FILE, archive, limit=5000)
    return new_seen, new_sent

# --- Bot Commands ---

async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching latest funding news...")
    with state_lock:
        sent_today = load_json(SENT_TODAY_FILE)
        seen = load_json(SEEN_FILE)

    settings = load_settings()
    sector = settings.get("sector_filter")

    new_seen, new_sent = fetch_and_alert(seen, sent_today, max_alerts=MAX_LATEST_ALERTS, sector_filter=sector)

    with state_lock:
        seen = load_json(SEEN_FILE)
        sent_today = load_json(SENT_TODAY_FILE)
        seen = list(set(seen + new_seen))
        sent_today = list(set(sent_today + new_sent))
        save_json(SEEN_FILE, seen)
        save_json(SENT_TODAY_FILE, sent_today)

    if not new_sent:
        await update.message.reply_text("No new funding activity found right now. Try again later or use /summary for a digest.")

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /search <company name>\n\nExample: /search Razorpay")
        return

    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Searching for <b>{query}</b>...", parse_mode="HTML")

    archive = load_json(ARCHIVE_FILE)
    results = []

    for article in archive:
        text = article.get("title", "") + " " + article.get("summary", "")
        if fuzzy_match(query, text):
            results.append(article)

    if not results:
        await update.message.reply_text(f"No articles found for <b>{query}</b>. The archive currently has {len(archive)} articles.", parse_mode="HTML")
        return

    sent = []
    for article in results[:5]:
        if not is_duplicate(article.get("title", ""), sent):
            real_url = resolve_url(article.get("url", ""))
            pp = article.get("published_parsed")
            success, _ = send_alert(
                article.get("title", ""),
                article.get("summary", ""),
                real_url,
                tuple(pp) if pp else None
            )
            if success:
                sent.append(article.get("title", ""))

    if not sent:
        await update.message.reply_text(f"No relevant funding articles found for <b>{query}</b>.", parse_mode="HTML")

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Generating funding summary...")

    archive = load_json(ARCHIVE_FILE)
    candidates = [a for a in archive if is_relevant_text(a.get("title", "") + " " + a.get("summary", ""))][-20:]

    if not candidates:
        await update.message.reply_text("Not enough data yet. Check back after a few polling cycles.")
        return

    # Include summaries for richer context
    articles_text = "\n".join([
        f"- {a['title']}: {clean_text(a.get('summary', ''))[:150]}"
        for a in candidates
    ])

    prompt = f"""You are summarizing Indian startup funding news for a sales team focused on tech companies.

Here are the latest funding articles:
{articles_text}

Write a concise digest in this format:
1. 2-3 sentence overview of overall funding activity and market sentiment
2. Bullet list of the most notable deals (company, amount, sector, round) using • as bullet character
3. Any notable trends (hot sectors, large rounds, active investors)

IMPORTANT formatting rules:
- Use ONLY plain text. No markdown. No headers with #. No **bold** syntax.
- Use • for bullet points
- Use ₹ for Indian amounts
- Keep it under 250 words. Be direct, no fluff."""

    summary = llm_call(prompt, max_tokens=500, retries=2)
    if summary:
        summary = markdown_to_telegram_html(summary)
        await update.message.reply_text(f"📊 <b>Funding Digest</b>\n\n{summary}", parse_mode="HTML")
    else:
        await update.message.reply_text("Summary generation failed. Try again shortly.")

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Fetching today's funding activity...")

    archive = load_json(ARCHIVE_FILE)
    sent_today = load_json(SENT_TODAY_FILE)
    today_ist = datetime.now(IST).date()  # Use IST since target audience is India
    today_articles = []

    for a in archive:
        pub_date = get_article_date(a)
        if pub_date:
            # Convert to IST for date comparison
            pub_date_ist = pub_date.astimezone(IST).date()
            if pub_date_ist == today_ist:
                today_articles.append(a)

    relevant = [a for a in today_articles if is_relevant_text(a.get("title", "") + " " + a.get("summary", ""))]

    if not relevant:
        await update.message.reply_text("No funding activity detected today yet. Auto-alerts run every 10 minutes.")
        return

    # Skip articles already sent today
    sent = []
    skipped = 0
    for article in relevant[:10]:
        title = article.get("title", "")
        if is_duplicate(title, sent_today + sent):
            skipped += 1
            continue
        real_url = resolve_url(article.get("url", ""))
        pp = article.get("published_parsed")
        success, _ = send_alert(
            title,
            article.get("summary", ""),
            real_url,
            tuple(pp) if pp else None
        )
        if success:
            sent.append(title)
        if len(sent) >= 7:
            break

    if not sent:
        msg = "All of today's funding alerts have already been sent." if skipped > 0 else "No relevant funding deals found today."
        await update.message.reply_text(msg)

async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Generating this week's funding summary...")

    archive = load_json(ARCHIVE_FILE)
    cutoff = datetime.now(IST) - timedelta(days=7)
    week_articles = []

    for a in archive:
        pub_date = get_article_date(a)
        if pub_date:
            if pub_date.astimezone(IST) >= cutoff:
                week_articles.append(a)

    relevant = [a for a in week_articles if is_relevant_text(a.get("title", "") + " " + a.get("summary", ""))]

    if not relevant:
        await update.message.reply_text("No funding activity found this week yet.")
        return

    articles_text = "\n".join([
        f"- {a['title']}: {clean_text(a.get('summary', ''))[:150]}"
        for a in relevant[-25:]
    ])

    prompt = f"""Summarize this week's Indian startup funding activity for a sales team.

Articles from the past 7 days:
{articles_text}

Write a weekly digest:
1. Overall funding landscape this week (2-3 sentences)
2. Top deals of the week (company, amount, sector, round) — use • for bullet points
3. Most active sectors and investors
4. Key takeaway for the sales team

IMPORTANT formatting rules:
- Use ONLY plain text. No markdown. No headers with #. No **bold** syntax. No --- dividers.
- Use • for bullet points
- Use ₹ for Indian amounts
- Keep it under 300 words. Be specific with numbers."""

    summary = llm_call(prompt, max_tokens=600, retries=2)
    if summary:
        summary = markdown_to_telegram_html(summary)
        await update.message.reply_text(
            f"📅 <b>Weekly Funding Digest</b>\n"
            f"<i>{cutoff.strftime('%b %d')} — {datetime.now(IST).strftime('%b %d, %Y')}</i>\n\n"
            f"{summary}",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("Weekly summary generation failed. Try again.")

async def cmd_sector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        sectors = [
            "Fintech", "SaaS", "Edtech", "Healthtech", "D2C", "Logistics",
            "AI", "Agritech", "CleanTech", "EV", "Gaming", "DeepTech",
            "FoodTech", "HRTech", "PropTech", "InsurTech"
        ]
        await update.message.reply_text(
            "🏷 <b>Filter by Sector</b>\n\n"
            f"Available sectors:\n{', '.join(sectors)}\n\n"
            "Usage: /sector <name> — set sector filter\n"
            "/sector off — remove filter\n\n"
            f"<i>Current filter: {load_settings().get('sector_filter') or 'None (all sectors)'}</i>",
            parse_mode="HTML"
        )
        return

    sector = " ".join(context.args)
    settings = load_settings()

    if sector.lower() == "off":
        settings["sector_filter"] = None
        save_settings(settings)
        await update.message.reply_text("🏷 Sector filter removed. You'll receive alerts from all sectors.")
    else:
        settings["sector_filter"] = sector
        save_settings(settings)
        await update.message.reply_text(f"🏷 Sector filter set to <b>{sector}</b>. Only matching alerts will be shown.", parse_mode="HTML")

async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = load_settings()
    settings["muted"] = True
    save_settings(settings)
    await update.message.reply_text(
        "🔇 Auto-alerts <b>muted</b>. You can still use /latest, /search, /summary manually.\n"
        "Use /unmute to resume auto-alerts.",
        parse_mode="HTML"
    )

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = load_settings()
    settings["muted"] = False
    save_settings(settings)
    await update.message.reply_text("🔊 Auto-alerts <b>resumed</b>. You'll receive alerts every 10 minutes.", parse_mode="HTML")

async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "📝 <b>Send Feedback</b>\n\n"
            "Usage: /feedback <your message>\n\n"
            "Examples:\n"
            "• /feedback too many irrelevant alerts about government policy\n"
            "• /feedback missing alerts from TechCrunch India\n"
            "• /feedback the summary command is very useful\n\n"
            "<i>Feedback is logged for review and helps improve the bot.</i>",
            parse_mode="HTML"
        )
        return

    feedback_text = " ".join(context.args)
    feedback_log = load_json(FEEDBACK_FILE)
    feedback_log.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user": update.message.from_user.username or str(update.message.from_user.id),
        "feedback": feedback_text
    })
    save_json(FEEDBACK_FILE, feedback_log, limit=500)
    await update.message.reply_text("✅ Feedback logged. Thanks for helping improve the bot!")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset all data files to start fresh. Requires confirmation."""
    if not context.args or context.args[0].lower() != "confirm":
        archive = load_json(ARCHIVE_FILE)
        seen = load_json(SEEN_FILE)
        await update.message.reply_text(
            "⚠️ <b>Reset Bot Data</b>\n\n"
            f"This will clear:\n"
            f"• Archive: {len(archive)} articles\n"
            f"• Tracked entries: {len(seen)}\n"
            f"• Sent today log\n\n"
            f"Settings (mute, sector filter) will be preserved.\n\n"
            f"To confirm, run: /reset confirm",
            parse_mode="HTML"
        )
        return

    with state_lock:
        save_json(SEEN_FILE, [])
        save_json(SENT_TODAY_FILE, [])
        save_json(ARCHIVE_FILE, [], limit=5000)

    await update.message.reply_text(
        "✅ <b>Data reset complete.</b>\n\n"
        "• Archive cleared\n"
        "• Tracked entries cleared\n"
        "• Sent today cleared\n\n"
        "The bot will start building a fresh archive from the next polling cycle (within 10 minutes), "
        "or run /latest to populate immediately.",
        parse_mode="HTML"
    )

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = load_settings()
    seen = load_json(SEEN_FILE)
    archive = load_json(ARCHIVE_FILE)
    sent_today = load_json(SENT_TODAY_FILE)

    mute_status = "🔇 Muted" if settings.get("muted") else "🔊 Active"
    sector = settings.get("sector_filter") or "All sectors"

    await update.message.reply_text(
        f"⚙️ <b>Bot Settings</b>\n\n"
        f"<b>Auto-alerts:</b> {mute_status}\n"
        f"<b>Sector filter:</b> {sector}\n"
        f"<b>Poll interval:</b> Every 10 minutes\n"
        f"<b>Max auto-alerts/cycle:</b> {MAX_AUTO_ALERTS}\n"
        f"<b>Max on-demand alerts:</b> {MAX_LATEST_ALERTS}\n"
        f"<b>Feeds monitored:</b> {len(FEEDS)}\n\n"
        f"📊 <b>Stats</b>\n"
        f"Articles tracked: {len(seen)}\n"
        f"Archive size: {len(archive)}\n"
        f"Sent today: {len(sent_today)}",
        parse_mode="HTML"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick health check — kept for backward compatibility."""
    await cmd_settings(update, context)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 <b>Available Commands</b>\n\n"
        "<b>Alerts</b>\n"
        "/latest — fetch up to 7 fresh funding alerts\n"
        "/today — show today's funding activity\n"
        "/week — weekly funding digest with trends\n"
        "/search <i>name</i> — fuzzy search for a company\n"
        "/summary — AI-generated digest of recent activity\n\n"
        "<b>Filters</b>\n"
        "/sector <i>name</i> — filter alerts by sector\n"
        "/sector off — remove sector filter\n"
        "/mute — pause auto-alerts\n"
        "/unmute — resume auto-alerts\n\n"
        "<b>Info</b>\n"
        "/settings — current config and stats\n"
        "/feedback <i>message</i> — send feedback\n"
        "/reset — clear all data and start fresh\n"
        "/help — this menu\n\n"
        "<i>Auto-alerts run every 10 minutes when unmuted.</i>",
        parse_mode="HTML"
    )

# --- Main ---

def polling_loop():
    with state_lock:
        seen = load_json(SEEN_FILE)
        sent_today = load_json(SENT_TODAY_FILE)
    last_reset = datetime.now().date()

    while True:
        try:
            settings = load_settings()
            if settings.get("muted"):
                time.sleep(POLL_INTERVAL)
                continue

            today = datetime.now().date()
            if today != last_reset:
                with state_lock:
                    sent_today = []
                    save_json(SENT_TODAY_FILE, sent_today)
                last_reset = today

            sector_filter = settings.get("sector_filter")
            new_seen, new_sent = fetch_and_alert(seen, sent_today, sector_filter=sector_filter)

            with state_lock:
                seen = list(set(seen + new_seen))
                sent_today = list(set(sent_today + new_sent))
                save_json(SEEN_FILE, seen)
                save_json(SENT_TODAY_FILE, sent_today)
        except Exception as e:
            print(f"Polling error: {e}")
        time.sleep(POLL_INTERVAL)

def validate_env():
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID")
    if not ANTHROPIC_KEY:
        missing.append("ANTHROPIC_KEY")
    if missing:
        print(f"FATAL: Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

def main():
    validate_env()
    print("Bot starting...")

    # Backfill published_parsed for old archive entries
    backfill_archive_dates()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("latest", cmd_latest))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("sector", cmd_sector))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CommandHandler("feedback", cmd_feedback))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))

    t = threading.Thread(target=polling_loop, daemon=True)
    t.start()

    print(f"Bot running. Monitoring {len(FEEDS)} feeds every {POLL_INTERVAL}s.")
    app.run_polling()

if __name__ == "__main__":
    main()
