"""Turns a topic (or a researched news item) into a carousel post as JSON."""
import json
import re

from . import db
from .config import NEWS_PILLARS, PILLARS, S
from .llm import ask_json

CYBER_RULES = """Cyber news rules: explain what happened, who or what is affected, and what defenders should
do now (update to a fixed version, change a setting, rotate leaked keys, spot the phishing sign, turn on MFA).
Defensive only: never explain how to carry out the attack, no exploit code or payloads, no links to leaked
data. Name an attacker group or country only if the source states it, and say who says so ("according to ...").
Calm tone, no fear-mongering. Use "code" only for safe checks or fixes (e.g. checking a version)."""


def news_block(news, pillar, kind):
    label = PILLARS.get(pillar, ("NEWS", ""))[0]
    lines = ["", f"This is a {label} {kind}. Source article (use only these facts):",
             f"Title: {news['title']}", f"Link: {news['link']}", f"Text: {news['text'][:6000]}"]
    return lines + (["", CYBER_RULES] if pillar == "cyber_news" else [])


def learned():
    """What the weekly report found works on this account (saved by pipeline.weekly_report)."""
    try:
        found = json.loads(db.kv_get("learnings") or "[]")
    except ValueError:
        return []
    if not found:
        return []
    return ["", "What has worked on this account so far (from real numbers; use it as a hint, not a rule, "
                "and never let it bend the facts):", *[f"- {x}" for x in found[:5]]]

SCHEMA = """{
  "topic": "the topic, short",
  "pillar": "one of: ai_news, cyber_news, super_ai, ml, devops, cloud, code",
  "hook_options": ["5 different cover hooks you considered, max 8 words each"],
  "cover": {"kicker": "2-3 words, e.g. DEVOPS IN 60s", "title": "max 7 words, wrap the key word in **double stars**", "subtitle": "max 14 words"},
  "slides": [
    {"type": "point", "title": "max 8 words", "body": "max 38 words", "bullets": ["optional, max 4 items, max 8 words each"], "code": "optional, max 6 lines, max 34 characters per line"},
    {"type": "compare", "title": "max 8 words", "columns": [{"name": "max 2 words", "lines": ["max 3 items, max 6 words each"]}]}
  ],
  "cta": {"title": "max 6 words", "subtitle": "max 12 words"},
  "caption": "90-170 words, short lines, plain language, ends with one question to the reader",
  "hashtags": ["3 to 6 hashtags, lowercase, no spaces"],
  "alt_text": "max 40 words describing the carousel for blind users",
  "sources": ["https links you used; REQUIRED for ai_news and cyber_news"],
  "claims_to_verify": ["every factual claim in the post, one short sentence each"]
}"""

ABOUT = f"""ByteBaithak ({S.handle}) is an Instagram account that teaches AI and computer
science to students, junior developers and IT/network engineers moving into cloud and DevOps.
The host is a senior network engineer and Cisco Networking Academy instructor in Pakistan.
Audience: students, fresh graduates, junior developers and IT/network/helpdesk staff in Pakistan and India
who want a cloud, DevOps or AI job. Their real problems: no experience for a first job, interview fear,
not knowing what to learn first, English as a second language, slow laptops, no money for paid courses
or cloud bills. Use simple English, and connect each topic to their job or interview when it fits.

Voice: a calm, friendly instructor. Clear, specific, practical. No hype words (insane, game-changer,
mind-blowing, revolutionary, 100%). Never invent numbers, dates, quotes, benchmarks or company plans.
If you are not sure a fact is true, leave it out. For news, use ONLY the facts in the source text given.
For Super AI topics, present the main expert views fairly and never state contested predictions as facts."""

HOOKS = """Hook: first write 5 different hooks in "hook_options" (types: a common mistake, an interview
question, before vs after, a true surprising fact, "you are doing X wrong"), then use the strongest one.
A good hook is max 8 words, specific (names the tool, command or job), makes the viewer curious,
is understood in under 2 seconds by a non-native English speaker, and is not clickbait or hype.

Make it worth saving and sharing: give the viewer something to keep (a checklist, a cheat sheet, the exact
command, an interview answer) so they would send it to a friend or a study group. Before its closing
question, the caption may ask readers to share it with someone who is learning the topic."""

SYSTEM = f"""You write Instagram carousels. {ABOUT}

Structure: 4 to 7 content slides between the cover and the CTA. One idea per slide. Use a "compare"
slide when comparing 2-3 things (each column max 3 short lines). Use "code" only for real, correct commands.
The cover title is the hook.
{HOOKS}
Write in {S.language}.

Return ONLY a JSON object with exactly this shape:
{SCHEMA}"""


REEL_SCHEMA = """{
  "topic": "the topic, short",
  "pillar": "one of: ai_news, cyber_news, super_ai, ml, devops, cloud, code",
  "hook_options": ["5 different hooks you considered, max 8 words each"],
  "hook": {"kicker": "2-3 words, e.g. DEVOPS IN 30s", "title": "max 7 words, wrap the key word in **double stars**", "say": "spoken opening line, max 20 words, makes the viewer stay"},
  "scenes": [
    {"title": "max 6 words", "text": "on-screen text, max 10 words", "code": "optional, max 4 lines, max 34 characters per line; break a long command over lines with a trailing backslash", "say": "what the narrator says during this scene, 8 to 20 words (about 4-6 seconds)"}
  ],
  "outro": {"title": "max 6 words", "say": "spoken closing line, max 16 words, invite viewers to follow and save"},
  "caption": "60-150 words, short lines, plain language, ends with one question to the reader",
  "hashtags": ["3 to 6 hashtags, lowercase, no spaces"],
  "sources": ["https links you used; REQUIRED for ai_news and cyber_news"],
  "claims_to_verify": ["every factual claim in the reel, one short sentence each"]
}"""

REEL_SYSTEM = f"""You write short vertical video scripts (Instagram Reels, 25-45 seconds). {ABOUT}

Structure: a hook scene, then 5 to 8 short scenes, then an outro. A new scene every 4-6 seconds keeps
viewers watching, so give each scene one small step of the idea (a definition, an example, the command,
what it prints, a common mistake, an interview tip) instead of a few long scenes. The narration ("say")
is read aloud by a text-to-speech voice, so write it for the ear: short sentences, everyday words,
no markdown, no emojis, no URLs, no hashtags, and spell out symbols (say "dash p", not "-p").
Do not say the account handle; the outro screen shows it. All "say" lines together: 70 to 110 words.
The on-screen "text" supports the narration and is shorter than it. Use "code" only for real, correct commands.
The hook title is on screen in the first second and the hook "say" line is the first thing heard.
{HOOKS}
Write in {S.language}.

Return ONLY a JSON object with exactly this shape:
{REEL_SCHEMA}"""


COURSE = re.compile(r"^\s*(.+?)\s+Course\s+L(\d+)\s*:\s*(.+)$", re.I)
SERIES = re.compile(r"^\s*(.+?)\s+(\d+)\s*/\s*(\d+)\s*:\s*(.+)$")


def course_hint(topic):
    """Topics like "DevOps Course L3: Linux permissions" are lessons of a step-by-step course;
    "Kubernetes 3/30: What is a Pod?" is day 3 of a 30-day reel series."""
    s = SERIES.match(topic or "")
    if s:
        name, n, total, lesson = s.group(1), int(s.group(2)), int(s.group(3)), s.group(4)
        nxt = ("Say that this was the last day of the series and invite viewers to follow for what comes next."
               if n >= total else f"In the outro, tell viewers to follow so they do not miss day {n + 1}.")
        return ["", f"This is day {n} of a {total}-day {name} series on this account, for beginners.",
                f"Today's topic: {lesson}. Teach only this one idea, with one concrete example or command.",
                "Many viewers will see only this video, so it must make sense on its own.",
                f'Use the kicker "{name.upper()} {n}/{total}". Start the caption with "{name} {n}/{total}". {nxt}']
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
    user = ["TASK: WRITE_REEL", f"Pillar: {pillar} ({label})", f"Topic: {topic}", *course_hint(topic), *learned()]
    if news:
        user += news_block(news, pillar, "reel")
    if avoid:
        user += ["", "Recently posted topics (do not repeat their angle): " + "; ".join(avoid[:25])]
    return _normalise_reel(ask_json(REEL_SYSTEM, "\n".join(user)), topic, pillar, news)


def _normalise_reel(p, topic, pillar, news):
    p = dict(p or {})
    p.pop("hook_options", None)  # the model's drafts; dropped so a rejected option can't trip the checks
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
    user = ["TASK: WRITE_CAROUSEL", f"Pillar: {pillar} ({label})", f"Topic: {topic}", *course_hint(topic),
            *learned()]
    if news:
        user += news_block(news, pillar, "post")
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
    if post.get("pillar") == "cyber_news":
        user += ["", CYBER_RULES]
    if reel:
        return _normalise_reel(ask_json(REEL_SYSTEM, "\n".join(user)), post.get("topic"), post.get("pillar"), news)
    return _normalise(ask_json(SYSTEM, "\n".join(user)), post.get("topic"), post.get("pillar"), news)


def _normalise(p, topic, pillar, news):
    p = dict(p or {})
    p.pop("hook_options", None)  # the model's drafts; dropped so a rejected option can't trip the checks
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
    if p.get("pillar") in NEWS_PILLARS and p.get("sources"):
        parts.append("Source: " + p["sources"][0])
    parts.append(f"Follow {S.handle} for one byte of AI + CS every day.")
    parts.append(" ".join(p.get("hashtags", [])))
    return "\n\n".join(x for x in parts if x)
