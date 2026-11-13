"""Finds today's top AI story from public RSS feeds and fetches its text."""
import difflib
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import feedparser
import requests

log = logging.getLogger("research")
UA = {"User-Agent": "ByteBaithakBot/1.0 (+https://www.instagram.com/byte_baithak/)"}

# Primary sources first: company and research blogs carry more weight than aggregators.
FEEDS = [
    ("OpenAI", "https://openai.com/news/rss.xml", 3),
    ("Google AI", "https://blog.google/technology/ai/rss/", 3),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml", 3),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml", 2),
    ("Microsoft AI", "https://blogs.microsoft.com/ai/feed/", 2),
    ("NVIDIA", "https://blogs.nvidia.com/feed/", 2),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", 1),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 1),
    ("Hacker News AI", "https://hnrss.org/frontpage?q=AI+OR+LLM+OR+GPT+OR+Claude+OR+Gemini", 1),
]
KEYWORDS = ("model", "launch", "release", "open-source", "open source", "agent", "gpt", "gemini",
            "claude", "llama", "benchmark", "research", "chip", "api")


def recent_items(hours=36):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = []
    for name, url, weight in FEEDS:
        try:
            feed = feedparser.parse(requests.get(url, headers=UA, timeout=20).content)
        except requests.RequestException as e:
            log.warning("feed %s failed: %s", name, e)
            continue
        for e in feed.entries[:30]:
            t = e.get("published_parsed") or e.get("updated_parsed")
            when = datetime(*t[:6], tzinfo=timezone.utc) if t else None
            if when and when < cutoff:
                continue
            items.append({"source": name, "weight": weight, "title": html.unescape(e.get("title", "")).strip(),
                          "link": e.get("link", ""), "summary": _strip(e.get("summary", ""))[:500],
                          "when": when})
    return items


def pick_top(items, avoid_titles=()):
    """Score = source weight + how many other feeds cover the same story + keyword hits."""
    best, best_score = None, -1
    for it in items:
        if not it["link"].startswith("https://") or not it["title"]:
            continue
        if any(_similar(it["title"], a) > 0.6 for a in avoid_titles):
            continue
        echo = sum(1 for o in items if o is not it and o["source"] != it["source"]
                   and _similar(o["title"], it["title"]) > 0.45)
        kw = sum(1 for k in KEYWORDS if k in it["title"].lower())
        score = it["weight"] * 2 + echo * 3 + kw
        if score > best_score:
            best, best_score = it, score
    return best


def fetch_text(url, limit=8000):
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    return _strip(r.text)[:limit]


def top_story(avoid_titles=()):
    items = recent_items()
    log.info("research: %d fresh items", len(items))
    story = pick_top(items, avoid_titles)
    if not story:
        return None
    try:
        story["text"] = fetch_text(story["link"])
    except requests.RequestException as e:
        log.warning("article fetch failed (%s), using feed summary", e)
        story["text"] = story["summary"]
    if len(story["text"]) < 300:
        return None  # not enough facts to write a safe news post
    return story


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header", "noscript", "svg"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header", "noscript", "svg") and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip and d.strip():
            self.out.append(d.strip())


def _strip(s):
    p = _Text()
    try:
        p.feed(s or "")
    except Exception:  # broken HTML: fall back to a crude strip
        return re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", " ".join(p.out)).strip()


def _similar(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    s = top_story()
    print(s and {k: s[k] for k in ("source", "title", "link")})
