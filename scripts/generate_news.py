import os
import json
import hashlib
import re
import html
import time
from datetime import datetime, timezone

import requests
import feedparser

try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    HAS_OPENAI = True
except:
    HAS_OPENAI = False
    print("Warning: OpenAI not available, will use original text")

# =========================================================
# SETTINGS
# =========================================================

NEWS_FILE = "news.json"
MAX_PER_CATEGORY = 6
MAX_TOTAL = 50

# বাংলাদেশের সোর্স
BD_FEEDS = [
    "https://www.prothomalo.com/feed",
    "https://www.prothomalo.com/bangladesh/feed",
]

# বিশ্বের সোর্স (ইংরেজি)
WORLD_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://rss.cnn.com/rss/edition_world.rss",
    "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best",
    "https://feeds.feedburner.com/ndtvnews-world-news",
]

# ক্যাটাগরি ম্যাপিং
CATEGORY_KEYWORDS = {
    "রাজনীতি": ["politic", "election", "government", "minister", "parliament", "রাজনীতি", "নির্বাচন", "সরকার"],
    "অর্থনীতি": ["economy", "business", "market", "finance", "bank", "trade", "অর্থনীতি", "ব্যবসা", "বাজার"],
    "প্রযুক্তি": ["tech", "technology", "ai", "google", "apple", "microsoft", "প্রযুক্তি", "আইটি"],
    "খেলা": ["sport", "football", "cricket", "match", "player", "খেলা", "ক্রিকেট", "ফুটবল"],
    "বিনোদন": ["entertainment", "movie", "film", "celebrity", "actor", "বিনোদন", "সিনেমা", "তারকা"],
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

def get_rss_items(url):
    try:
        r = requests.get(url, timeout=25, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if r.status_code != 200:
            return []
        feed = feedparser.parse(r.content)
        return feed.entries
    except Exception as e:
        print(f"  RSS Error ({url}): {e}")
        return []

def extract_full_content(entry):
    if entry.get("content"):
        for c in entry.get("content", []):
            v = c.get("value", "")
            if v and len(v) > 150:
                return clean_text(v)
    ce = entry.get("content_encoded")
    if ce and len(ce) > 150:
        return clean_text(ce)
    summary = entry.get("summary") or ""
    if len(summary) > 100:
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
        except:
            pass
    try:
        enc = entry.get("enclosures")
        if enc:
            for item in enc:
                u = item.get("href") or item.get("url")
                if u:
                    return u
    except:
        pass
    for raw in [entry.get("summary", ""), entry.get("description", "")]:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw or "", flags=re.I)
        if m:
            return m.group(1)
    return ""

def detect_category(title, content, default="বিশ্ব"):
    text = (title + " " + content).lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text:
                return cat
    return default

# =========================================================
# AI REWRITE (বাংলায় রিরাইট)
# =========================================================

def rewrite_to_bengali(title, content):
    """OpenAI দিয়ে ইংরেজি নিউজ বাংলায় রিরাইট করে"""
    if not HAS_OPENAI or not os.getenv("OPENAI_API_KEY"):
        return title, content[:800]

    try:
        prompt = f"""তুমি একজন পেশাদার বাংলা সংবাদ সম্পাদক। নিচের ইংরেজি খবরটিকে সুন্দর, সাবলীল ও প্রাকৃতিক বাংলায় রিরাইট করো।

নিয়ম:
1. শুধু বাংলায় লেখো
2. টাইটেল আকর্ষণীয় ও সংক্ষিপ্ত রাখো (১০-১৮ শব্দ)
3. কনটেন্ট ১৫০-২৫০ শব্দের মধ্যে রাখো
4. নিউজের মূল তথ্য বাদ দিও না
5. কোনো ইংরেজি শব্দ রাখো না (প্রয়োজন হলে বাংলায় লেখো)

আসল টাইটেল: {title}

আসল খবর:
{content[:1200]}

আউটপুট ফরম্যাট (ঠিক এভাবে দাও):
TITLE: (এখানে বাংলা টাইটেল)
CONTENT: (এখানে বাংলা কনটেন্ট)"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "তুমি একজন অভিজ্ঞ বাংলা সংবাদ লেখক। শুধু বাংলায় লেখো।"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.6,
            max_tokens=800
        )

        result = response.choices[0].message.content.strip()

        # Parse
        new_title = title
        new_content = content[:800]

        if "TITLE:" in result and "CONTENT:" in result:
            parts = result.split("CONTENT:")
            title_part = parts[0].replace("TITLE:", "").strip()
            content_part = parts[1].strip()
            if title_part:
                new_title = title_part
            if content_part:
                new_content = content_part

        return new_title, new_content

    except Exception as e:
        print(f"  AI Rewrite Error: {e}")
        return title, content[:800]

# =========================================================
# MAIN
# =========================================================

def main():
    print("=" * 50)
    print("DOP NEWS 24 — AI Bengali News Publisher")
    print("=" * 50)

    data = load_news()
    old_articles = data.get("articles", [])

    used_source_ids = set()
    used_links = set()
    for a in old_articles:
        if a.get("source_id"):
            used_source_ids.add(a["source_id"])
        if a.get("link"):
            used_links.add(a["link"])

    new_articles = []

    # ---------- ১. বাংলাদেশের খবর ----------
    print("\n[1] Collecting Bangladesh News...")
    bd_count = 0
    for feed_url in BD_FEEDS:
        if bd_count >= MAX_PER_CATEGORY:
            break
        entries = get_rss_items(feed_url)
        for entry in entries:
            if bd_count >= MAX_PER_CATEGORY:
                break

            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "")
            content = extract_full_content(entry)

            if not title or not link or len(content) < 60:
                continue
            if link in used_links:
                continue

            sid = make_id(title + "|" + link)
            if sid in used_source_ids:
                continue

            summary = content[:200].strip() + ("..." if len(content) > 200 else "")
            image = extract_image(entry)

            new_articles.append({
                "id": make_id(sid + str(time.time())),
                "source_id": sid,
                "category": "বাংলাদেশ",
                "title": title,
                "summary": summary,
                "content": content,
                "image": image,
                "link": link,
                "published_at": datetime.now(timezone.utc).isoformat()
            })

            used_source_ids.add(sid)
            used_links.add(link)
            bd_count += 1

    print(f"  Added Bangladesh: {bd_count}")

    # ---------- ২. বিশ্বের খবর (AI রিরাইট) ----------
    print("\n[2] Collecting & Rewriting World News...")
    world_count = 0

    for feed_url in WORLD_FEEDS:
        if world_count >= 18:  # বিশ্বের জন্য বেশি জায়গা
            break

        print(f"  Fetching: {feed_url}")
        entries = get_rss_items(feed_url)

        for entry in entries[:8]:  # প্রতি ফিড থেকে কিছু
            if world_count >= 18:
                break

            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "")
            content = extract_full_content(entry)

            if not title or not link or len(content) < 80:
                continue
            if link in used_links:
                continue

            sid = make_id(title + "|" + link)
            if sid in used_source_ids:
                continue

            # AI দিয়ে বাংলায় রিরাইট
            print(f"    Rewriting: {title[:50]}...")
            bn_title, bn_content = rewrite_to_bengali(title, content)

            # ক্যাটাগরি ডিটেক্ট
            category = detect_category(bn_title, bn_content, default="বিশ্ব")

            summary = bn_content[:200].strip() + ("..." if len(bn_content) > 200 else "")
            image = extract_image(entry)

            new_articles.append({
                "id": make_id(sid + str(time.time())),
                "source_id": sid,
                "category": category,
                "title": bn_title,
                "summary": summary,
                "content": bn_content,
                "image": image,
                "link": link,
                "published_at": datetime.now(timezone.utc).isoformat()
            })

            used_source_ids.add(sid)
            used_links.add(link)
            world_count += 1
            time.sleep(1.2)  # Rate limit

    print(f"  Added World (AI rewritten): {world_count}")

    print("\nTotal new articles:", len(new_articles))

    # পুরোনো + নতুন মিলিয়ে
    combined = (new_articles + old_articles)[:MAX_TOTAL]
    data["articles"] = combined
    save_news(data)
    print("Done.")

if __name__ == "__main__":
    main()
