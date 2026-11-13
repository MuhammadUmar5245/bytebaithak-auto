"""Turns a topic (or a researched news item) into a carousel post as JSON."""
import json
import re

from .config import PILLARS, S
from .llm import ask_json

SCHEMA = """{
  "topic": "the topic, short",
  "pillar": "one of: ai_news, super_ai, ml, devops, cloud, code",
  "cover": {"kicker": "2-3 words, e.g. DEVOPS IN 60s", "title": "max 7 words, wrap the key word in **double stars**", "subtitle": "max 14 words"},
  "slides": [
    {"type": "point", "title": "max 8 words", "body": "max 38 words", "bullets": ["optional, max 4 items, max 8 words each"], "code": "optional, max 6 lines, max 34 characters per line"},
    {"type": "compare", "title": "max 8 words", "columns": [{"name": "max 2 words", "lines": ["max 3 items, max 6 words each"]}]}
  ],
  "cta": {"title": "max 6 words", "subtitle": "max 12 words"},
  "caption": "90-170 words, short lines, plain language, ends with one question to the reader",
  "hashtags": ["3 to 6 hashtags, lowercase, no spaces"],
  "alt_text": "max 40 words describing the carousel for blind users",
  "sources": ["https links you used; REQUIRED for ai_news"],
  "claims_to_verify": ["every factual claim in the post, one short sentence each"]
}"""

ABOUT = f"""ByteBaithak ({S.handle}) is an Instagram account that teaches AI and computer
science to students, junior developers and IT/network engineers moving into cloud and DevOps.
The host is a senior network engineer and Cisco Networking Academy instructor.

Voice: a calm, friendly instructor. Clear, specific, practical. No hype words (insane, game-changer,
mind-blowing, revolutionary, 100%). Never invent numbers, dates, quotes, benchmarks or company plans.
If you are not sure a fact is true, leave it out. For news, use ONLY the facts in the source text given.
For Super AI topics, present the main expert views fairly and never state contested predictions as facts."""

SYSTEM = f"""You write Instagram carousels. {ABOUT}

Structure: 4 to 7 content slides between the cover and the CTA. One idea per slide. Use a "compare"
slide when comparing 2-3 things (each column max 3 short lines). Use "code" only for real, correct commands.
Write in {S.language}.

Return ONLY a JSON object with exactly this shape:
{SCHEMA}"""


REEL_SCHEMA = """{
  "topic": "the topic, short",
  "pillar": "one of: ai_news, super_ai, ml, devops, cloud, code",
  "hook": {"kicker": "2-3 words, e.g. DEVOPS IN 30s", "title": "max 7 words, wrap the key word in **double stars**", "say": "spoken opening line, max 20 words, makes the viewer stay"},
  "scenes": [
    {"title": "max 6 words", "text": "on-screen text, max 16 words", "code": "optional, max 4 lines, max 30 characters per line", "say": "what the narrator says during this scene, max 32 words"}
  ],
  "outro": {"title": "max 6 words", "say": "spoken closing line, max 16 words, invite viewers to follow and save"},
  "caption": "60-150 words, short lines, plain language, ends with one question to the reader",
  "hashtags": ["3 to 6 hashtags, lowercase, no spaces"],
  "sources": ["https links you used; REQUIRED for ai_news"],
  "claims_to_verify": ["every factual claim in the reel, one short sentence each"]
}"""

REEL_SYSTEM = f"""You write short vertical video scripts (Instagram Reels, 25-45 seconds). {ABOUT}

Structure: a hook scene, then 3 to 5 scenes with one idea each, then an outro. The narration ("say")
is read aloud by a text-to-speech voice, so write it for the ear: short sentences, everyday words,
no markdown, no emojis, no URLs, no hashtags, and spell out symbols (say "dash p", not "-p").
Do not say the account handle; the outro screen shows it. All "say" lines together: 70 to 110 words.
The on-screen "text" supports the narration and is shorter than it. Use "code" only for real, correct commands.
Write in {S.language}.

Return ONLY a JSON object with exactly this shape:
{REEL_SCHEMA}"""


COURSE = re.compile(r"^\s*(.+?)\s+Course\s+L(\d+)\s*:\s*(.+)$", re.I)


def course_hint(topic):
    """Topics like "DevOps Course L3: Linux permissions" are lessons of a step-by-step course."""
    m = COURSE.match(topic or "")
    if not m:
        return []
    name, n, lesson = m.group(1), int(m.group(2)), m.group(3)
    return ["", f"This is lesson {n} of the step-by-step {name} course on this account, for beginners.",
            f"Lesson topic: {lesson}. Teach only this lesson; assume the earlier lessons were seen.",
            f'Use the kicker "{name.upper()} COURSE · L{n}". In the caption, name the lesson number and end by '
            "telling readers to follow so they do not miss the next lesson."]


def write_reel(topic, pillar, news=None, avoid=None):
    label = PILLARS.get(pillar, ("TECH", ""))[0]
    user = ["TASK: WRITE_REEL", f"Pillar: {pillar} ({label})", f"Topic: {topic}", *course_hint(topic)]
    if news:
        user += ["", "This is an AI NEWS reel. Source article (use only these facts):",
                 f"Title: {news['title']}", f"Link: {news['link']}", f"Text: {news['text'][:6000]}"]
    if avoid:
        user += ["", "Recently posted topics (do not repeat their angle): " + "; ".join(avoid[:25])]
    return _normalise_reel(ask_json(REEL_SYSTEM, "\n".join(user)), topic, pillar, news)


def _normalise_reel(p, topic, pillar, news):
    p = dict(p or {})
    p["format"] = "reel"
    p.setdefault("topic", topic)
    p["pillar"] = pillar or p.get("pillar")
    p["hook"] = dict(p.get("hook") or {})
    p["outro"] = dict(p.get("outro") or {})
    p["outro"].setdefault("title", "Follow for more")
    p["outro"].setdefault("say", "Follow for one byte of tech every day, and save this for later.")
    p["scenes"] = [dict(s) for s in (p.get("scenes") or []) if isinstance(s, dict)]
    for s in p["scenes"]:
        s["code"] = (s.get("code") or "").rstrip()
    p["hashtags"] = [("#" + h.lstrip("#")).lower().replace(" ", "") for h in p.get("hashtags", []) if h]
    p.setdefault("sources", [])
    if news and news["link"] not in p["sources"]:
        p["sources"].insert(0, news["link"])
    p.setdefault("claims_to_verify", [])
    return p


def load_script(name, pillar, reel):
    """The owner's own post, written by hand in data/scripts/<name>.json (same shape as the AI's JSON)."""
    path = S.data_dir / "scripts" / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"script not found: data/scripts/{name}.json")
    p = json.loads(path.read_text(encoding="utf-8"))
    topic = p.get("topic") or name
    return _normalise_reel(p, topic, pillar, None) if reel else _normalise(p, topic, pillar, None)


def write_carousel(topic, pillar, news=None, avoid=None):
    label = PILLARS.get(pillar, ("TECH", ""))[0]
    user = ["TASK: WRITE_CAROUSEL", f"Pillar: {pillar} ({label})", f"Topic: {topic}", *course_hint(topic)]
    if news:
        user += ["", "This is an AI NEWS post. Source article (use only these facts):",
                 f"Title: {news['title']}", f"Link: {news['link']}", f"Text: {news['text'][:6000]}"]
    if avoid:
        user += ["", "Recently posted topics (do not repeat their angle): " + "; ".join(avoid[:25])]
    return _normalise(ask_json(SYSTEM, "\n".join(user)), topic, pillar, news)


def revise(post, issues, news=None):
    reel = post.get("format") == "reel"
    user = ["TASK: REVISE_REEL" if reel else "TASK: REVISE_CAROUSEL",
            "Fix every issue below. Keep everything else. Return the full JSON again in the same shape.",
            "Issues:", *[f"- {i}" for i in issues], "", "Current post JSON:",
            json.dumps(post, ensure_ascii=False)]
    if news:
        user += ["", "Source text (only these facts are allowed):", news["text"][:6000]]
    if reel:
        return _normalise_reel(ask_json(REEL_SYSTEM, "\n".join(user)), post.get("topic"), post.get("pillar"), news)
    return _normalise(ask_json(SYSTEM, "\n".join(user)), post.get("topic"), post.get("pillar"), news)


def _normalise(p, topic, pillar, news):
    p = dict(p or {})
    p.setdefault("topic", topic)
    p["pillar"] = pillar or p.get("pillar")
    p.setdefault("cover", {})
    p.setdefault("slides", [])
    p.setdefault("cta", {"title": "Save this for later", "subtitle": f"Follow {S.handle} for daily AI + CS"})
    p["hashtags"] = [("#" + h.lstrip("#")).lower().replace(" ", "") for h in p.get("hashtags", []) if h]
    p.setdefault("sources", [])
    if news and news["link"] not in p["sources"]:
        p["sources"].insert(0, news["link"])
    p.setdefault("claims_to_verify", [])
    for s in p["slides"]:
        s.setdefault("type", "point")
        if s["type"] == "point":
            s.setdefault("bullets", [])
            s["code"] = (s.get("code") or "").rstrip()
    return p


def full_caption(p):
    parts = [p.get("caption", "").strip()]
    if p.get("pillar") == "ai_news" and p.get("sources"):
        parts.append("Source: " + p["sources"][0])
    parts.append(f"Follow {S.handle} for one byte of AI + CS every day.")
    parts.append(" ".join(p.get("hashtags", [])))
    return "\n\n".join(x for x in parts if x)
