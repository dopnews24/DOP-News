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

MAX_PER_CATEGORY = 6
MAX_TOTAL = 24

# ✅ বাংলা সাইটের RSS ফিড (সব কাজ করে)
FEEDS = {
    "বাংলাদেশ": "https://www.prothomalo.com/feed",
    "বিশ্ব": "https://www.prothomalo.com/world/feed",
    "খেলা": "https://www.prothomalo.com/sports/feed",
    "প্রযুক্তি": "https://www.prothomalo.com/technology/feed",
}


# =========================================================
# COMMON FUNCTIONS
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
    except Exception as e:
        print("news.json read error:", e)
        return {"updated_at": "", "articles": []}


def save_news(data):
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("news.json saved:", len(data["articles"]), "articles")


# =========================================================
# REMOVE NEWSPAPER REFERENCES
# =========================================================

SOURCE_WORDS = [
    "প্রথম আলো", "যুগান্তর", "কালের কণ্ঠ", "সমকাল", "ইত্তেফাক",
    "বাংলাদেশ প্রতিদিন", "বিডিনিউজ২৪", "বাংলা ট্রিবিউন", "ঢাকা পোস্ট",
    "bdnews24", "prothomalo", "banglatribune", "dhakapost",
    "bbc", "cnn", "reuters", "al jazeera", "the daily star"
]


def remove_source_from_title(title):
    title = clean_text(title)
    pattern = (
        r"\s*[-|–—]\s*"
        r"(?:" + "|".join(re.escape(x) for x in SOURCE_WORDS) + r")"
        r"(?:\.com|\.net|\.org)?\s*$"
    )
    title = re.sub(pattern, "", title, flags=re.I)
    return title.strip(" -–—|")


# =========================================================
# RSS
# =========================================================

def get_rss_items(url):
    print("\nReading RSS:", url)
    try:
        response = requests.get(
            url, timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36"
            }
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        print("RSS items:", len(feed.entries))
        return feed.entries
    except Exception as e:
        print("RSS ERROR:", e)
        return []


# =========================================================
# EXTRACT FULL CONTENT (content:encoded)
# =========================================================

def extract_full_content(entry):
    """
    RSS entry থেকে সবচেয়ে বিস্তারিত টেক্সট বের করে।
    content:encoded → content → summary → description
    """

    # 1. content:encoded (সবচেয়ে বিস্তারিত)
    if entry.get("content"):
        for c in entry.get("content", []):
            val = c.get("value", "")
            if val and len(val) > 200:
                return clean_text(val)

    # 2. content:encoded alternate
    content_encoded = entry.get("content_encoded")
    if content_encoded and len(content_encoded) > 200:
        return clean_text(content_encoded)

    # 3. summary
    summary = entry.get("summary") or ""
    if len(summary) > 200:
        return clean_text(summary)

    # 4. description
    desc = entry.get("description") or ""
    return clean_text(desc)


# =========================================================
# FIND IMAGE
# =========================================================

def extract_rss_image(entry):
    try:
        media = entry.get("media_content")
        if media:
            for item in media:
                url = item.get("url")
                if url:
                    return url
    except Exception:
        pass

    try:
        thumb = entry.get("media_thumbnail")
        if thumb:
            for item in thumb:
                url = item.get("url")
                if url:
                    return url
    except Exception:
        pass

    try:
        enclosures = entry.get("enclosures")
        if enclosures:
            for item in enclosures:
                url = item.get("href") or item.get("url")
                if url:
                    return url
    except Exception:
        pass

    # content:encoded থেকে img ট্যাগ
    try:
        if entry.get("content"):
            for c in entry.get("content", []):
                raw = c.get("value", "")
                match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw, flags=re.I)
                if match:
                    return match.group(1)
    except Exception:
        pass

    try:
        raw = entry.get("summary") or entry.get("description") or ""
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw, flags=re.I)
        if match:
            return match.group(1)
    except Exception:
        pass

    return ""


def extract_og_image(url):
    if not url:
        return ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, timeout=15, headers=headers, allow_redirects=True)
        if response.status_code != 200:
            return ""
        page = response.text
        match = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            page, flags=re.I)
        if match:
            return html.unescape(match.group(1))
        match = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            page, flags=re.I)
        if match:
            return html.unescape(match.group(1))
        match = re.search(
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            page, flags=re.I)
        if match:
            return html.unescape(match.group(1))
    except Exception as e:
        print("OG image error:", e)
    return ""


def make_unique_visual(category, title):
    seed = make_id(category + "|" + title)
    number = int(seed[:8], 16)

    palettes = [
        ("#991b1b", "#450a0a"), ("#1d4ed8", "#172554"),
        ("#047857", "#022c22"), ("#7c3aed", "#2e1065"),
        ("#c2410c", "#431407"), ("#0369a1", "#082f49")
    ]
    color1, color2 = palettes[number % len(palettes)]

    icons = {"বাংলাদেশ": "🇧🇩", "বিশ্ব": "🌍", "খেলা": "🏆", "প্রযুক্তি": "💻"}
    icon = icons.get(category, "📰")

    short = clean_text(title)
    if len(short) > 32:
        short = short[:32] + "…"

    safe_title = short.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675" viewBox="0 0 1200 675">
        <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="{color1}"/><stop offset="100%" stop-color="{color2}"/></linearGradient></defs>
        <rect width="1200" height="675" fill="url(#g)"/>
        <text x="600" y="280" text-anchor="middle" font-size="120">{icon}</text>
        <text x="600" y="400" text-anchor="middle" fill="white" font-size="40" font-family="Arial" font-weight="bold">{safe_title}</text>
        <text x="600" y="480" text-anchor="middle" fill="white" opacity=".8" font-size="26" font-family="Arial">DOP NEWS 24</text>
    </svg>"""

    return "data:image/svg+xml;charset=UTF-8," + requests.utils.quote(svg, safe="")


def get_best_image(entry, article_url, used_images, category, title):
    image = extract_rss_image(entry)
    if image and image not in used_images:
        return image

    if article_url:
        og = extract_og_image(article_url)
        if og and og not in used_images:
            return og

    return make_unique_visual(category, title)


# =========================================================
# MAIN
# =========================================================

def main():
    print("\n========================================\nDOP NEWS 24 PUBLISHER (FREE)\n========================================")

    data = load_news()
    old_articles = data.get("articles", [])

    existing_ids = {art.get("source_id") for art in old_articles if art.get("source_id")}
    used_images = {art.get("image") for art in old_articles if art.get("image")}

    new_articles = []

    for category, feed_url in FEEDS.items():
        print("\nCATEGORY:", category)
        entries = get_rss_items(feed_url)
        if not entries:
            continue

        count = 0
        for entry in entries:
            if count >= MAX_PER_CATEGORY:
                break

            raw_title = clean_text(entry.get("title", ""))
            full_content = extract_full_content(entry)
            article_url = entry.get("link", "")

            if not raw_title or not full_content:
                continue

            clean_title = remove_source_from_title(raw_title)
            source_id = make_id(clean_title + "|" + article_url)

            if source_id in existing_ids:
                print("Duplicate:", clean_title)
                continue

            print("Processing:", clean_title)

            # summary = প্রথম ২০০ অক্ষর, content = পুরোটা
            summary = full_content[:200].strip()
            if len(full_content) > 200:
                summary += "..."

            image = get_best_image(entry, article_url, used_images, category, clean_title)
            used_images.add(image)

            article = {
                "id": make_id(source_id + str(datetime.now())),
                "source_id": source_id,
                "category": category,
                "title": clean_title,
                "summary": summary,
                "content": full_content,
                "image": image,
                "published_at": datetime.now(timezone.utc).isoformat()
            }

            new_articles.append(article)
            existing_ids.add(source_id)
            count += 1

            if len(new_articles) >= MAX_TOTAL:
                break

        if len(new_articles) >= MAX_TOTAL:
            break

    print("\nNew articles:", len(new_articles))

    combined = (new_articles + old_articles)[:MAX_TOTAL]
    data["articles"] = combined
    save_news(data)

    print("\n========================================\nPUBLISH COMPLETE\n========================================")


if __name__ == "__main__":
    main()
