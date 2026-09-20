import os
import json
import hashlib
import re
import html
import time
from datetime import datetime, timezone

import requests
import feedparser

# =========================================================
# SETTINGS
# =========================================================

NEWS_FILE = "news.json"
MAX_TOTAL = 80
MAX_BD_PER_RUN = 4        # ফ্রি সীমার জন্য কম রাখা হয়েছে, বাড়াতে পারেন
MAX_WORLD_PER_RUN = 8
FALLBACK_CHARS = 500      # AI না চললে বাংলা খবরের যতটুকু অংশ রাখা হবে

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODELS = [
    m.strip()
    for m in (os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite,gemini-3-flash-preview,gemini-2.5-flash").split(",")
    if m.strip()
]
AI_DELAY = float(os.getenv("AI_DELAY") or "7")   # দুই রিকোয়েস্টের মাঝে সেকেন্ড
AI_DISABLED = not GEMINI_KEY
ACTIVE_MODEL = ""

if AI_DISABLED:
    print("Warning: GEMINI_API_KEY missing, AI disabled")

BD_FEEDS = [
    "https://www.prothomalo.com/feed",
]

WORLD_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://feeds.feedburner.com/ndtvnews-world-news",
]

ALLOWED_CATEGORIES = ["বাংলাদেশ", "বিশ্ব", "রাজনীতি", "অর্থনীতি", "প্রযুক্তি", "খেলা", "বিনোদন", "লাইফস্টাইল"]

LINK_RULES = [
    ("/sports", "খেলা"),
    ("/entertainment", "বিনোদন"),
    ("/politics", "রাজনীতি"),
    ("/technology", "প্রযুক্তি"),
    ("/business", "অর্থনীতি"),
    ("/economy", "অর্থনীতি"),
    ("/lifestyle", "লাইফস্টাইল"),
    ("/international", "বিশ্ব"),
]

CATEGORY_KEYWORDS = {
    "রাজনীতি": ["politics", "election", "parliament", "minister", "রাজনীতি", "নির্বাচন", "সংসদ", "মন্ত্রী"],
    "অর্থনীতি": ["economy", "business", "market", "finance", "bank", "trade", "অর্থনীতি", "ব্যবসা", "বাজার", "ব্যাংক"],
    "প্রযুক্তি": ["technology", "tech", "AI", "google", "apple", "microsoft", "প্রযুক্তি", "কৃত্রিম বুদ্ধিমত্তা"],
    "খেলা": ["sport", "football", "cricket", "match", "খেলা", "ক্রিকেট", "ফুটবল", "ম্যাচ"],
    "বিনোদন": ["entertainment", "movie", "film", "celebrity", "বিনোদন", "সিনেমা", "তারকা"],
}

SKIP_TITLE_WORDS = ["সারা দিনের খবর"]

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

def is_bengali(text, min_ratio=0.6):
    letters = re.findall(r"[A-Za-z\u0980-\u09FF]", text or "")
    if not letters:
        return False
    bn = sum(1 for ch in letters if "\u0980" <= ch <= "\u09FF")
    return bn / len(letters) >= min_ratio

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
            print(f"  RSS status {r.status_code}: {url}")
            return []
        return feedparser.parse(r.content).entries
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

def entry_time(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        try:
            return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat()

def category_from_link(link):
    l = (link or "").lower()
    for key, cat in LINK_RULES:
        if key in l:
            return cat
    return ""

def keyword_hit(text, kw):
    if re.search(r"[A-Za-z]", kw):
        return re.search(r"\b" + re.escape(kw) + r"\b", text, flags=re.I) is not None
    return kw in text

def detect_category(title, content, default):
    text = title + " " + content
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if keyword_hit(text, kw):
                return cat
    return default

def parse_json_text(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()
    return json.loads(text)

# =========================================================
# GEMINI (ফ্রি AI)
# =========================================================

def call_gemini(prompt):
    """সফল হলে উত্তরের লেখা ফেরত দেয়, নাহলে None"""
    global AI_DISABLED, ACTIVE_MODEL
    if AI_DISABLED:
        return None

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
        },
    }
    headers = {"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"}
    models = [ACTIVE_MODEL] if ACTIVE_MODEL else GEMINI_MODELS

    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(2):
            try:
                r = requests.post(url, headers=headers, json=body, timeout=90)
            except Exception as e:
                print("    → Network error:", e)
                time.sleep(3)
                continue

            if r.status_code == 200:
                ACTIVE_MODEL = model
                try:
                    return r.json()["candidates"][0]["content"]["parts"][0]["text"]
                except Exception:
                    print("    → খালি বা ব্লক করা উত্তর")
                    return None

            print(f"    → Gemini {r.status_code} ({model}): {r.text[:160]}")

            if r.status_code == 429:
                if attempt == 0:
                    time.sleep(35)   # প্রতি মিনিটের সীমা হলে অপেক্ষা
                    continue
                AI_DISABLED = True
                print("    → সীমা শেষ, এই রানে AI বন্ধ")
                return None
            if r.status_code in (401, 403):
                AI_DISABLED = True
                print("    → key সমস্যা, AI বন্ধ")
                return None
            if r.status_code in (400, 404):
                break  # পরের মডেল চেষ্টা
            time.sleep(4)
    return None

def rewrite_with_ai(title, content, is_english):
    """সফল হলে (বাংলা শিরোনাম, বাংলা লেখা, ক্যাটাগরি) ফেরত দেয়, ব্যর্থ হলে None"""
    if AI_DISABLED:
        return None

    src = "ইংরেজি" if is_english else "বাংলা"
    prompt = (
        f"তুমি একজন অভিজ্ঞ বাংলা সংবাদ সম্পাদক। নিচের {src} খবরটি নিজের ভাষায় বাংলায় নতুন করে লেখো।\n\n"
        "নিয়ম:\n"
        "- শুধু বাংলায় লেখো। বিদেশি নাম ও সংস্থার নাম বাংলা অক্ষরে লেখো (যেমন: ট্রাম্প, বিবিসি)\n"
        "- শুধু নিচের তথ্য ব্যবহার করো। নিজে থেকে কোনো তথ্য, সংখ্যা বা উদ্ধৃতি যোগ করবে না\n"
        "- মূল লেখার বাক্য হুবহু কপি করবে না, নিজের ভাষায় লিখবে\n"
        "- শিরোনাম সর্বোচ্চ ১৬ শব্দ\n"
        "- মূল লেখা বড় হলে গুরুত্বপূর্ণ সব তথ্যসহ ২০০-৩৫০ শব্দে অনুচ্ছেদ ভাগ করে লেখো\n"
        "- মূল লেখা ছোট হলে যতটুকু তথ্য আছে ততটুকুই ২-৪ বাক্যে লেখো, বাড়িয়ে লিখবে না\n"
        "- category এই তালিকা থেকে ঠিক একটি: " + ", ".join(ALLOWED_CATEGORIES) + "\n\n"
        "শুধু JSON দাও, এই ফরম্যাটে:\n"
        '{"title": "...", "content": "...", "category": "..."}\n\n'
        f"আসল শিরোনাম: {title}\n\n"
        f"আসল খবর:\n{content[:3500]}"
    )

    for attempt in range(2):
        text = call_gemini(prompt)
        if text is None:
            if AI_DISABLED:
                return None
            continue
        try:
            data = parse_json_text(text)
            new_title = clean_text(data.get("title", ""))
            new_content = clean_text(data.get("content", ""))
            cat = (data.get("category") or "").strip()
            if is_bengali(new_title) and is_bengali(new_content) and len(new_content) > 40:
                return new_title, new_content, (cat if cat in ALLOWED_CATEGORIES else "")
            print("    → বাংলা যাচাই ব্যর্থ, আবার চেষ্টা")
        except Exception as e:
            print("    → JSON পড়া যায়নি:", e)
    return None

# =========================================================
# COLLECT
# =========================================================

def collect(feed_urls, limit, per_feed, is_english, default_cat, min_len, used_ids, used_links):
    items = []
    for feed_url in feed_urls:
        if len(items) >= limit:
            break
        print(f"  Fetching: {feed_url}")
        entries = get_rss_items(feed_url)

        for entry in entries[:per_feed]:
            if len(items) >= limit:
                break

            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "")
            content = extract_full_content(entry)

            if not title or not link or len(content) < min_len:
                continue
            if "/video/" in link or any(w in title for w in SKIP_TITLE_WORDS):
                continue

            sid = make_id(title + "|" + link)
            if link in used_links or sid in used_ids:
                continue

            print(f"    Rewriting: {title[:50]}...")
            used_ai = not AI_DISABLED
            result = rewrite_with_ai(title, content, is_english)
            if used_ai:
                time.sleep(AI_DELAY)

            if result:
                bn_title, bn_content, ai_cat = result
            elif not is_english:
                # AI না চললে বাংলা খবরের শুধু শিরোনাম ও ছোট অংশ রাখা হয়
                bn_title, bn_content, ai_cat = title, content[:FALLBACK_CHARS].strip() + "...", ""
            else:
                print("    → বাদ (বাংলা রিরাইট হয়নি)")
                continue

            if is_english:
                category = ai_cat or detect_category(bn_title, bn_content, default_cat)
            else:
                category = category_from_link(link) or ai_cat or detect_category(bn_title, bn_content, default_cat)

            summary = bn_content[:200].strip() + ("..." if len(bn_content) > 200 else "")

            items.append({
                "id": make_id(sid + str(time.time())),
                "source_id": sid,
                "category": category,
                "cat_ok": True,
                "title": bn_title,
                "summary": summary,
                "content": bn_content,
                "image": extract_image(entry),
                "link": link,
                "published_at": entry_time(entry),
            })

            used_ids.add(sid)
            used_links.add(link)
    return items

# =========================================================
# MAIN
# =========================================================

def main():
    print("=" * 50)
    print("DOP NEWS 24 — AI Bengali News Publisher (Gemini)")
    print("=" * 50)

    data = load_news()
    all_old = data.get("articles", [])

    # শুধু ইংরেজি শিরোনামের পুরোনো খবর বাদ, বাকি সব থাকে
    old_articles = [a for a in all_old if is_bengali(a.get("title", ""))]
    print(f"Old articles kept: {len(old_articles)} / {len(all_old)}")

    used_ids = {a["source_id"] for a in all_old if a.get("source_id")}
    used_links = {a["link"] for a in all_old if a.get("link")}

    print("\n[1] Bangladesh News...")
    bd = collect(BD_FEEDS, MAX_BD_PER_RUN, 20, False, "বাংলাদেশ", 60, used_ids, used_links)
    print(f"  Added Bangladesh: {len(bd)}")

    print("\n[2] World News...")
    world = collect(WORLD_FEEDS, MAX_WORLD_PER_RUN, 10, True, "বিশ্ব", 80, used_ids, used_links)
    print(f"  Added World: {len(world)}")

    combined = bd + world + old_articles
    combined.sort(key=lambda a: a.get("published_at", ""), reverse=True)
    data["articles"] = combined[:MAX_TOTAL]
    save_news(data)
    print("Done.")

if __name__ == "__main__":
    main()
