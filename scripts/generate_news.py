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
MAX_PER_CATEGORY = 8
MAX_TOTAL = 60

# ✅ প্রতিটি ক্যাটাগরির জন্য আলাদা RSS (যা কাজ করে)
FEEDS = {
    "বাংলাদেশ": [
        "https://www.prothomalo.com/feed",
    ],
    "বিশ্ব": [
        "https://www.prothomalo.com/world/feed",
        "https://www.prothomalo.com/feed",
    ],
    "রাজনীতি": [
        "https://www.prothomalo.com/politics/feed",
        "https://www.prothomalo.com/feed",
    ],
    "অর্থনীতি": [
        "https://www.prothomalo.com/business/feed",
        "https://www.prothomalo.com/feed",
    ],
    "খেলা": [
        "https://www.prothomalo.com/sports/feed",
        "https://www.prothomalo.com/feed",
    ],
    "প্রযুক্তি": [
        "https://www.prothomalo.com/technology/feed",
        "https://www.prothomalo.com/feed",
    ],
    "বিনোদন": [
        "https://www.prothomalo.com/entertainment/feed",
        "https://www.prothomalo.com/feed",
    ],
    "লাইফস্টাইল": [
        "https://www.prothomalo.com/lifestyle/feed",
        "https://www.prothomalo.com/feed",
    ],
}


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
    try:
        r = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        })
        if r.status_code != 200:
            return []
        feed = feedparser.parse(r.content)
        return feed.entries
    except Exception:
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

    return clean_text(entry.get("description") or "")


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
    print("DOP NEWS 24 — Multi-Category Publisher")
    print("=" * 40)

    data = load_news()
    old_articles = data.get("articles", [])

    # সব ব্যবহৃত source_id এবং link সংগ্রহ
    used_source_ids = set()
    used_links = set()
    for a in old_articles:
        if a.get("source_id"):
            used_source_ids.add(a["source_id"])
        if a.get("link"):
            used_links.add(a["link"])

    new_articles = []

    for category, feed_urls in FEEDS.items():
        print("\nCATEGORY:", category)
        cat_count = 0

        for feed_url in feed_urls:
            if cat_count >= MAX_PER_CATEGORY:
                break

            entries = get_rss_items(feed_url)
            for entry in entries:
                if cat_count >= MAX_PER_CATEGORY:
                    break

                title = clean_text(entry.get("title", ""))
                link = entry.get("link", "")
                content = extract_full_content(entry)

                if not title or not link or not content or len(content) < 80:
                    continue

                # ডুপ্লিকেট চেক
                if link in used_links:
                    continue

                sid = make_id(title + "|" + link)
                if sid in used_source_ids:
                    continue

                summary = content[:220].strip()
                if len(content) > 220:
                    summary += "..."

                image = extract_image(entry)

                new_articles.append({
                    "id": make_id(sid + str(datetime.now())),
                    "source_id": sid,
                    "category": category,
                    "title": title,
                    "summary": summary,
                    "content": content,
                    "image": image,
                    "link": link,
                    "published_at": datetime.now(timezone.utc).isoformat()
                })

                used_source_ids.add(sid)
                used_links.add(link)
                cat_count += 1

                if len(new_articles) >= MAX_TOTAL:
                    break

            if len(new_articles) >= MAX_TOTAL:
                break

        print("  Added:", cat_count)

    print("\nNew articles:", len(new_articles))

    # নতুন + পুরোনো, MAX_TOTAL এ সীমাবদ্ধ
    combined = (new_articles + old_articles)[:MAX_TOTAL]
    data["articles"] = combined
    save_news(data)
    print("Done.")


if __name__ == "__main__":
    main()
