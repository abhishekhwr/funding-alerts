import feedparser
import requests
import time
import json
import os
import re
from urllib.parse import urlparse

TELEGRAM_TOKEN = "***REVOKED_TELEGRAM_TOKEN***"
CHAT_ID = "***REDACTED_CHAT_ID***"
SEEN_FILE = "seen_entries.json"
POLL_INTERVAL = 600
MAX_ALERTS_PER_RUN = 10  # cap per cycle

FEEDS = [
    "https://entrackr.com/feed/",
    "https://inc42.com/feed/",
    "https://news.google.com/rss/search?q=india+startup+funding&hl=en-IN&gl=IN&ceid=IN:en",
]

KEYWORDS = [
    "funding", "raises", "raised", "series a", "series b", "seed round",
    "investment", "crore", "million"
]

# Words that make an article irrelevant
EXCLUDE_KEYWORDS = [
    "upsc", "exam", "syllabus", "ias", "government scheme", "policy"
]

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return json.load(f)
    return []

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen[-500:], f)

def clean_text(text):
    text = re.sub(r'<[^>]+>', '', text)       # remove HTML tags
    text = re.sub(r'&nbsp;', ' ', text)        # fix &nbsp;
    text = re.sub(r'&[a-z]+;', '', text)       # remove other HTML entities
    text = re.sub(r'\s+', ' ', text).strip()   # clean whitespace
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

def is_duplicate(title, seen_titles):
    title_words = set(title.lower().split())
    for seen in seen_titles:
        seen_words = set(seen.lower().split())
        overlap = len(title_words & seen_words) / max(len(title_words), 1)
        if overlap > 0.6:  # 60% word overlap = duplicate
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

def check_feeds(seen):
    new_seen = []
    sent_titles = []
    alert_count = 0

    for feed_url in FEEDS:
        if alert_count >= MAX_ALERTS_PER_RUN:
            break
        feed = feedparser.parse(feed_url)
        for entry in feed.entries:
            if alert_count >= MAX_ALERTS_PER_RUN:
                break
            entry_id = entry.get("id") or entry.get("link")
            if entry_id not in seen:
                if is_relevant(entry) and not is_duplicate(entry.title, sent_titles):
                    real_url = resolve_url(entry.link)
                    summary = entry.get("summary", "")
                    send_telegram(entry.title, real_url, summary)
                    sent_titles.append(entry.title)
                    alert_count += 1
                new_seen.append(entry_id)
    return new_seen

def main():
    seen = load_seen()
    print("Monitoring started...")
    while True:
        new_entries = check_feeds(seen)
        seen = list(set(seen + new_entries))
        save_seen(seen)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
