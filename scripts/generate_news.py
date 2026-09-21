import os
import json
import hashlib
import re
import html
import time
import random
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
import feedparser

try:
    import trafilatura  # পুরো আর্টিকেল পেজ থেকে লেখা বের করার জন্য
except ImportError:
    trafilatura = None

# =========================================================
# SETTINGS
# =========================================================

START_TIME = time.time()
RUN_BUDGET = int(os.getenv("RUN_BUDGET") or "800")        # পুরো রানের সর্বোচ্চ সেকেন্ড
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT") or "40")

NEWS_FILE = "news.json"
MAX_TOTAL = int(os.getenv("MAX_TOTAL") or "300")
MAX_BD_PER_RUN = int(os.getenv("MAX_BD_PER_RUN") or "10")
MAX_WORLD_PER_RUN = int(os.getenv("MAX_WORLD_PER_RUN") or "45")   # মধ্যপ্রাচ্য+ইউরোপ ফিড বাড়ায় সীমাও বাড়ানো হলো
PER_FEED_NEW_WORLD = 3    # প্রতিটি ফিড থেকে প্রতি রানে সর্বোচ্চ নতুন খবর (যাতে একটি ফিডেই সীমা শেষ না হয়)
PER_FEED_NEW_BD = 8
FEED_SCAN_DEPTH = 20      # প্রতিটি ফিডের প্রথম কয়টি এন্ট্রি দেখা হবে
FULL_TEXT_MIN = 800       # ফিডের লেখা এর চেয়ে ছোট হলে আর্টিকেল পেজ থেকে পুরো লেখা আনা হবে
FALLBACK_CHARS = int(os.getenv("FALLBACK_CHARS") or "250")   # AI না চললে বাংলা খবরের যতটুকু অংশ রাখা হবে

UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# =========================================================
# Groq (আগে OpenAI ছিল, এখন Groq দিয়ে রিরাইট হয়)
# =========================================================

GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODELS = [
    m.strip()
    for m in (os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b,openai/gpt-oss-20b,llama-3.3-70b-versatile,llama-3.1-8b-instant").split(",")
    if m.strip()
]
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
AI_DELAY = float(os.getenv("AI_DELAY") or "1")   # Groq এর রেট-লিমিট সাধারণত অনেক শিথিল
AI_DISABLED = not GROQ_KEY
ACTIVE_MODEL = ""
DEAD_MODELS = set()       # যে মডেল পাওয়া যায়নি / বন্ধ

if AI_DISABLED:
    print("Warning: GROQ_API_KEY missing, AI disabled")

# =========================================================
# RSS FEEDS — বাংলাদেশ (বাংলা, AI ছাড়াও fallback দিয়ে চলে)
# =========================================================
# প্রতিটি এন্ট্রি: {"url": ..., "country": "BD"}
# সব ফিড চালু আছে কিনা GitHub Actions লগে "entries: N" দেখে যাচাই করুন। 0 হলে ওই ফিড বদলান/বাদ দিন।

BD_FEEDS = [
    {"url": "https://www.prothomalo.com/feed", "country": "BD"},
]

# =========================================================
# RSS FEEDS — বিশ্ব (ইংরেজি, বাধ্যতামূলক AI রিরাইট প্রয়োজন)
# দক্ষিণ এশিয়া + USA + UK + Russia + সাধারণ আন্তর্জাতিক
# =========================================================

WORLD_FEEDS = [
    # ---------- বাংলাদেশ, ইংরেজি সোর্স (bdnews24 স্থায়ীভাবে বন্ধ হওয়ায় বিকল্প) ----------
    {"url": "https://en.ittefaq.com.bd/feed/", "country": "BD"},

    # ---------- ভারত (IN) ----------
    {"url": "https://www.thehindu.com/news/national/feeder/default.rss", "country": "IN"},
    {"url": "https://www.thehindu.com/news/international/feeder/default.rss", "country": "IN"},
    {"url": "https://indianexpress.com/section/india/feed/", "country": "IN"},
    {"url": "https://feeds.feedburner.com/ndtvnews-world-news", "country": "IN"},

    # ---------- পাকিস্তান (PK) ----------
    {"url": "https://www.dawn.com/feeds/home", "country": "PK"},
    {"url": "https://www.dawn.com/feeds/world", "country": "PK"},

    # ---------- শ্রীলঙ্কা (LK) ----------
    {"url": "https://www.dailymirror.lk/RSS_Feeds/breaking-news", "country": "LK"},

    # ---------- নেপাল (NP) ----------
    {"url": "https://kathmandupost.com/rss", "country": "NP"},

    # ---------- USA (US) ----------
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "country": "US"},
    {"url": "https://feeds.washingtonpost.com/rss/world", "country": "US"},
    {"url": "https://feeds.npr.org/1004/rss.xml", "country": "US"},

    # ---------- UK (UK) ----------
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml", "country": "UK"},
    {"url": "https://feeds.bbci.co.uk/news/world/asia/rss.xml", "country": "UK"},
    {"url": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "country": "UK"},
    {"url": "https://feeds.bbci.co.uk/news/world/africa/rss.xml", "country": "UK"},
    {"url": "https://feeds.bbci.co.uk/news/world/europe/rss.xml", "country": "UK"},
    {"url": "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml", "country": "UK"},
    {"url": "https://www.theguardian.com/world/rss", "country": "UK"},
    {"url": "https://feeds.skynews.com/feeds/rss/world.xml", "country": "UK"},

    # ---------- রাশিয়া (RU) ----------
    {"url": "https://tass.com/rss/v2.xml", "country": "RU"},
    {"url": "https://www.rt.com/rss/", "country": "RU"},

    # ---------- মধ্যপ্রাচ্য (ME) ----------
    {"url": "https://www.timesofisrael.com/feed/", "country": "ME"},
    {"url": "https://english.alarabiya.net/rss.xml", "country": "ME"},
    {"url": "https://www.middleeasteye.net/rss", "country": "ME"},

    # ---------- ইউরোপ (EU) ----------
    {"url": "https://www.euronews.com/rss?level=theme&name=news", "country": "EU"},
    {"url": "https://www.politico.eu/feed/", "country": "EU"},

    # ---------- সাধারণ আন্তর্জাতিক (INT) ----------
    {"url": "https://www.aljazeera.com/xml/rss/all.xml", "country": "INT"},
    {"url": "https://rss.dw.com/xml/rss-en-world", "country": "INT"},
    {"url": "https://www.france24.com/en/rss", "country": "INT"},
]

COUNTRY_NAMES = {
    "BD": "বাংলাদেশ", "IN": "ভারত", "PK": "পাকিস্তান", "LK": "শ্রীলঙ্কা",
    "NP": "নেপাল", "US": "যুক্তরাষ্ট্র", "UK": "যুক্তরাজ্য", "RU": "রাশিয়া",
    "ME": "মধ্যপ্রাচ্য", "EU": "ইউরোপ", "INT": "আন্তর্জাতিক",
}

ALLOWED_CATEGORIES = ["বাংলাদেশ", "বিশ্ব", "রাজনীতি", "অর্থনীতি", "প্রযুক্তি", "খেলা", "বিনোদন", "লাইফস্টাইল"]

LINK_RULES = [
    ("/sports", "খেলা"),
    ("/sport", "খেলা"),
    ("/entertainment", "বিনোদন"),
    ("/politics", "রাজনীতি"),
    ("/technology", "প্রযুক্তি"),
    ("/business", "অর্থনীতি"),
    ("/economy", "অর্থনীতি"),
    ("/lifestyle", "লাইফস্টাইল"),
    ("/international", "বিশ্ব"),
    ("/world", "বিশ্ব"),          # প্রথম আলোর বিশ্ব বিভাগ /world/ পথে থাকে
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

def over_budget():
    return time.time() - START_TIME > RUN_BUDGET

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

def norm_link(link):
    """?utm=... ইত্যাদি বাদ দিয়ে লিংক এক করা, যাতে একই খবর দুই ফিডে এলে ডুপ্লিকেট ধরা পড়ে"""
    return (link or "").split("?")[0].split("#")[0].rstrip("/")

def source_name(link):
    try:
        return urlparse(link).netloc.replace("www.", "")
    except Exception:
        return ""

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
        r = requests.get(url, timeout=25, headers=UA_HEADERS)
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

def paragraph_fallback(page):
    paras = re.findall(r"<p[^>]*>(.*?)</p>", page or "", flags=re.S | re.I)
    texts = [clean_text(p) for p in paras]
    texts = [t for t in texts if len(t) > 60]
    return "\n\n".join(texts)

def fetch_article_text(url):
    """আর্টিকেল পেজ থেকে পুরো লেখা আনে। ব্যর্থ হলে খালি স্ট্রিং"""
    try:
        r = requests.get(url, timeout=15, headers=UA_HEADERS)
        if r.status_code != 200:
            print(f"    page status {r.status_code}")
            return ""
        page = r.text
    except Exception as e:
        print(f"    page fetch error: {e}")
        return ""

    text = ""
    if trafilatura:
        try:
            text = trafilatura.extract(page, include_comments=False, include_tables=False) or ""
        except Exception:
            text = ""
    if len(text) < 300:
        text = paragraph_fallback(page)
    return text.strip()

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
    text = title  # শুধু শিরোনাম: পুরো লেখা ধরলে ভুল ক্যাটাগরি হতে পারে
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
# GROQ CALL
# =========================================================

def call_groq(prompt):
    """সফল হলে উত্তরের লেখা ফেরত দেয়, নাহলে None"""
    global AI_DISABLED, ACTIVE_MODEL
    if AI_DISABLED:
        return None
    if over_budget():
        AI_DISABLED = True
        print("    → সময়সীমা শেষ, এই রানে AI বন্ধ")
        return None

    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json",
    }

    models = [m for m in GROQ_MODELS if m not in DEAD_MODELS]
    if ACTIVE_MODEL in models:
        models.remove(ACTIVE_MODEL)
        models.insert(0, ACTIVE_MODEL)

    for model in models:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "তুমি একজন অভিজ্ঞ বাংলা সংবাদ সম্পাদক। শুধু বৈধ JSON আউটপুট দাও, অন্য কোনো টেক্সট নয়।"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 2000,
            "response_format": {"type": "json_object"},
        }
        for attempt in range(2):
            t0 = time.time()
            try:
                r = requests.post(GROQ_URL, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
            except Exception as e:
                print(f"    → Network/timeout ({model}): {e}")
                break  # পরের মডেল চেষ্টা
            secs = round(time.time() - t0, 1)

            if r.status_code == 200:
                ACTIVE_MODEL = model
                print(f"    → OK {model} {secs}s")
                try:
                    return r.json()["choices"][0]["message"]["content"]
                except Exception:
                    print("    → খালি বা অপঠনযোগ্য উত্তর")
                    return None

            print(f"    → Groq {r.status_code} ({model}, {secs}s): {r.text[:160]}")

            if r.status_code == 429:
                if attempt == 0:
                    time.sleep(15)   # রেট-লিমিট হলে অপেক্ষা
                    continue
                DEAD_MODELS.add(model)
                print(f"    → {model} রেট-লিমিটেড, পরের মডেলে যাচ্ছি")
                break
            if r.status_code in (401, 403):
                AI_DISABLED = True
                print("    → API key সমস্যা, AI বন্ধ")
                return None
            if r.status_code == 404:
                DEAD_MODELS.add(model)   # মডেলের নাম ভুল বা অ্যাক্সেস নেই
            break  # 400, 5xx: পরের মডেল চেষ্টা

    if all(m in DEAD_MODELS for m in GROQ_MODELS):
        AI_DISABLED = True
        print("    → সব মডেল ব্যর্থ, এই রানে AI বন্ধ")
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
        f"আসল খবর:\n{content[:5000]}"
    )

    for attempt in range(2):
        text = call_groq(prompt)
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

def collect(feed_list, limit, per_feed_new, is_english, default_cat, min_len, used_ids, used_links):
    items = []
    feeds = list(feed_list)
    random.shuffle(feeds)   # প্রতি রানে ক্রম বদলায়, তাই শেষের ফিড/দেশ কখনো বঞ্চিত হয় না

    for feed in feeds:
        feed_url = feed["url"]
        feed_country = feed.get("country", "")

        if len(items) >= limit:
            break
        if over_budget():
            print("  সময়সীমা শেষ, এই ধাপ এখানেই থামল")
            break
        if AI_DISABLED and is_english:
            print("  AI বন্ধ, ইংরেজি খবর রিরাইট সম্ভব নয়, এই ফিড এড়িয়ে যাওয়া হলো")
            continue   # পুরো ধাপ থামানো হয় না — শুধু AI ছাড়া চলবে না এমন ফিড এড়ানো হয়

        print(f"  Fetching [{feed_country}]: {feed_url}")
        entries = get_rss_items(feed_url)
        print(f"    entries: {len(entries)}")

        added = 0
        stats = {"dup": 0, "short": 0, "skip": 0, "rewrite_fail": 0}

        for entry in entries[:FEED_SCAN_DEPTH]:
            if len(items) >= limit or added >= per_feed_new:
                break
            if over_budget():
                break

            title = re.sub(r"\s+", " ", clean_text(entry.get("title", "")))
            link = entry.get("link", "")
            if not title or not link:
                stats["skip"] += 1
                continue
            if "/video/" in link or "/live/" in link or any(w in title for w in SKIP_TITLE_WORDS):
                stats["skip"] += 1
                continue

            nlink = norm_link(link)
            sid = make_id(title + "|" + link)
            if nlink in used_links or sid in used_ids:
                stats["dup"] += 1
                continue

            content = extract_full_content(entry)
            if len(content) < FULL_TEXT_MIN:
                page_text = fetch_article_text(link)
                if len(page_text) > len(content):
                    content = page_text[:6000]
            if len(content) < min_len:
                stats["short"] += 1
                continue

            print(f"    Rewriting: {title[:50]}...")
            used_ai = not AI_DISABLED
            result = rewrite_with_ai(title, content, is_english)
            if used_ai and not AI_DISABLED:
                time.sleep(AI_DELAY)

            if result:
                bn_title, bn_content, ai_cat = result
            elif not is_english:
                # AI না চললে বাংলা খবরের শুধু শিরোনাম ও ছোট অংশ রাখা হয়
                bn_title, bn_content, ai_cat = title, content[:FALLBACK_CHARS].strip() + "...", ""
            else:
                print("    → বাদ (বাংলা রিরাইট হয়নি)")
                stats["rewrite_fail"] += 1
                if AI_DISABLED:
                    break
                continue

            bn_title = re.sub(r"\s+", " ", bn_title).strip()

            if is_english:
                category = ai_cat or detect_category(bn_title, bn_content, default_cat)
            else:
                category = category_from_link(link) or ai_cat or detect_category(bn_title, bn_content, default_cat)

            if is_english:
                region = "world"
            else:
                region = "world" if category_from_link(link) == "বিশ্ব" else "bd"

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
                "source": source_name(link),
                "country": feed_country,
                "country_name": COUNTRY_NAMES.get(feed_country, ""),
                "region": region,
                "ai_rewritten": bool(result),
                "published_at": entry_time(entry),
            })

            used_ids.add(sid)
            used_links.add(nlink)
            added += 1

        print(f"    → added {added} | {stats}")
    return items

# =========================================================
# MAIN
# =========================================================

def main():
    print("=" * 50)
    print("DOP NEWS 24 — AI Bengali News Publisher (Groq)")
    print("=" * 50)
    print("trafilatura:", "yes" if trafilatura else "no (fallback parser)")

    data = load_news()
    all_old = data.get("articles", [])

    # শুধু ইংরেজি শিরোনামের পুরোনো খবর বাদ, বাকি সব থাকে
    old_articles = [a for a in all_old if is_bengali(a.get("title", ""))]
    print(f"Old articles kept: {len(old_articles)} / {len(all_old)}")

    used_ids = {a["source_id"] for a in old_articles if a.get("source_id")}
    used_links = {norm_link(a["link"]) for a in old_articles if a.get("link")}

    print("\n[1] Bangladesh News...")
    bd = collect(BD_FEEDS, MAX_BD_PER_RUN, PER_FEED_NEW_BD, False, "বাংলাদেশ", 60, used_ids, used_links)
    print(f"  Added Bangladesh: {len(bd)}")

    print("\n[2] World News (South Asia + USA + UK + Russia + International)...")
    world = collect(WORLD_FEEDS, MAX_WORLD_PER_RUN, PER_FEED_NEW_WORLD, True, "বিশ্ব", 40, used_ids, used_links)
    print(f"  Added World: {len(world)}")

    # কোন দেশ থেকে কতটা এলো — ডিবাগের জন্য
    by_country = {}
    for a in bd + world:
        c = a.get("country") or "?"
        by_country[c] = by_country.get(c, 0) + 1
    print("  Country breakdown:", by_country)

    combined = bd + world + old_articles
    combined.sort(key=lambda a: a.get("published_at", ""), reverse=True)
    data["articles"] = combined[:MAX_TOTAL]
    save_news(data)
    print(f"Done in {round(time.time() - START_TIME)}s.")

if __name__ == "__main__":
    main()
