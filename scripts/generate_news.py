import os
import json
import hashlib
import re
import html
from datetime import datetime, timezone

import requests
import feedparser


# =========================================================
# SETTINGS
# =========================================================

NEWS_FILE = "news.json"
MAX_PER_FEED = 10
MAX_TOTAL = 30

# ✅ শুধু কাজ করে এমন RSS ফিড
FEEDS = [
    {"category": "বাংলাদেশ", "url": "https://www.prothomalo.com/feed"},
    {"category": "বিশ্ব",    "url": "https://www.prothomalo.com/feed"},
    {"category": "খেলা",     "url": "https://www.prothomalo.com/feed"},
    {"category": "প্রযুক্তি", "url": "https://www.prothomalo.com/feed"},
]


# =========================================================
# HELPERS
# =========================================================

def clean_text(text):
    if not text:
        return ""
    text = html.unescape(str(text))
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def make_id(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def load_news():
    if not os.path.exists(NEWS_FILE):
        return {"updated_at": "", "articles": []}
    try:
        with open(NEWS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"updated_at": "", "articles": []}
        if not isinstance(data.get("articles"), list):
            data["articles"] = []
        return data
    except Exception:
        return {"updated_at": "", "articles": []}


def save_news(data):
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("Saved:", len(data["articles"]), "articles")


# =========================================================
# RSS
# =========================================================

def get_rss_items(url):
    print("RSS:", url)
    try:
        r = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        })
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        print("  items:", len(feed.entries))
        return feed.entries
    except Exception as e:
        print("  ERROR:", e)
        return []


def extract_full_content(entry):
    if entry.get("content"):
        for c in entry.get("content", []):
            v = c.get("value", "")
            if v and len(v) > 200:
                return clean_text(v)

    ce = entry.get("content_encoded")
    if ce and len(ce) > 200:
        return clean_text(ce)

    summary = entry.get("summary") or ""
    if len(summary) > 200:
        return clean_text(summary)

    desc = entry.get("description") or ""
    return clean_text(desc)


def extract_image(entry):
    for key in ("media_content", "media_thumbnail"):
        try:
            arr = entry.get(key)
            if arr:
                for item in arr:
                    u = item.get("url")
                    if u:
                        return u
        except Exception:
            pass

    try:
        enc = entry.get("enclosures")
        if enc:
            for item in enc:
                u = item.get("href") or item.get("url")
                if u:
                    return u
    except Exception:
        pass

    for raw in [entry.get("summary", ""), entry.get("description", "")]:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw or "", flags=re.I)
        if m:
            return m.group(1)

    return ""


# =========================================================
# MAIN
# =========================================================

def main():
    print("=" * 40)
    print("DOP NEWS 24 — Free Publisher")
    print("=" * 40)

    data = load_news()
    old_articles = data.get("articles", [])
    seen_ids = {a.get("source_id") for a in old_articles if a.get("source_id")}

    # সব RSS থেকে সব entry সংগ্রহ
    all_entries = []
    for feed in FEEDS:
        entries = get_rss_items(feed["url"])
        for e in entries:
            all_entries.append((feed["category"], e))

    # ডুপ্লিকেট সরান (একই link একবারই)
    unique = []
    seen_links = set()
    for cat, e in all_entries:
        link = e.get("link", "")
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        unique.append((cat, e))

    print("Unique entries:", len(unique))

    new_articles = []
    for cat, entry in unique:
        if len(new_articles) >= MAX_TOTAL:
            break

        title = clean_text(entry.get("title", ""))
        content = extract_full_content(entry)
        link = entry.get("link", "")

        if not title or not content or len(content) < 100:
            continue

        sid = make_id(title + "|" + link)
        if sid in seen_ids:
            continue

        summary = content[:220].strip()
        if len(content) > 220:
            summary += "..."

        image = extract_image(entry)

        new_articles.append({
            "id": make_id(sid + str(datetime.now())),
            "source_id": sid,
            "category": cat,
            "title": title,
            "summary": summary,
            "content": content,
            "image": image,
            "published_at": datetime.now(timezone.utc).isoformat()
        })
        seen_ids.add(sid)

    print("New articles:", len(new_articles))

    # নতুন + পুরোনো
    combined = new_articles + old_articles
    # MAX_TOTAL এ সীমাবদ্ধ
    data["articles"] = combined[:MAX_TOTAL]
    save_news(data)
    print("Done.")


if __name__ == "__main__":
    main()
