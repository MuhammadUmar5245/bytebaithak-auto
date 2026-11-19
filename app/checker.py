"""Three layers of checks before anything can be published.

1. rules()   - deterministic: lengths, counts, links, banned words, duplicates
2. facts()   - a separate AI call reviews every claim (against the source text for news)
3. visual()  - an AI that can see images looks at the rendered slides
Errors block the post; warnings only force human approval.
"""
import difflib
import json
import re

from .config import NEWS_PILLARS
from .llm import LLMError, ask_json, can_see_images

HYPE = ("insane", "game-changer", "game changer", "mind-blowing", "mind blowing", "revolutionary",
        "100% guaranteed", "secret trick", "nobody tells you")


def _words(s):
    return len(re.findall(r"\S+", str(s or "")))


def rules(post, recent_topics=()):
    err, warn = [], []
    slides = post.get("slides") or []
    if not 3 <= len(slides) <= 8:
        err.append(f"needs 3-8 content slides, has {len(slides)}")
    cover = post.get("cover") or {}
    if not cover.get("title"):
        err.append("cover title missing")
    elif _words(cover["title"].replace("*", "")) > 8:
        warn.append("cover title longer than 8 words")
    for i, s in enumerate(slides, 2):
        if not s.get("title"):
            err.append(f"slide {i}: title missing")
        if _words(s.get("title")) > 10:
            warn.append(f"slide {i}: title over 10 words")
        if _words(s.get("body")) > 45:
            err.append(f"slide {i}: body over 45 words, split it")
        if len(s.get("bullets") or []) > 5:
            err.append(f"slide {i}: more than 5 bullets")
        code = s.get("code") or ""
        if code:
            lines = code.splitlines()
            if len(lines) > 7:
                err.append(f"slide {i}: code over 7 lines")
            if any(len(l) > 38 for l in lines):
                err.append(f"slide {i}: a code line is over 38 characters")
        if s.get("type") == "compare":
            cols = s.get("columns") or []
            if not 2 <= len(cols) <= 3:
                err.append(f"slide {i}: compare needs 2-3 columns")
    if not post.get("alt_text"):
        warn.append("alt text missing")
    _common(post, recent_topics, err, warn, min_caption_words=40)
    return err, warn


def reel_rules(post, recent_topics=()):
    err, warn = [], []
    hook = post.get("hook") or {}
    if not hook.get("title"):
        err.append("hook title missing")
    elif _words(hook["title"].replace("*", "")) > 8:
        warn.append("hook title longer than 8 words")
    if not hook.get("say"):
        err.append("hook narration (say) missing")
    scenes = post.get("scenes") or []
    if not 3 <= len(scenes) <= 6:
        err.append(f"needs 3-6 scenes, has {len(scenes)}")
    for i, s in enumerate(scenes, 2):
        if not s.get("title"):
            err.append(f"scene {i}: title missing")
        if not s.get("say"):
            err.append(f"scene {i}: narration (say) missing")
        if _words(s.get("say")) > 40:
            err.append(f"scene {i}: narration over 40 words, shorten it")
        if _words(s.get("text")) > 22:
            err.append(f"scene {i}: on-screen text over 22 words")
        lines = (s.get("code") or "").splitlines()
        if len(lines) > 5:
            err.append(f"scene {i}: code over 5 lines")
        # 38 still fits the reel's code box (the template shrinks the font to ~80%); real overflow is
        # caught by the render step anyway. kubectl/docker commands need the room.
        if any(len(l) > 38 for l in lines):
            err.append(f"scene {i}: a code line is over 38 characters; break it with a trailing backslash")
    spoken = [hook.get("say")] + [s.get("say") for s in scenes] + [(post.get("outro") or {}).get("say")]
    total = sum(_words(x) for x in spoken)
    if not 45 <= total <= 140:
        err.append(f"narration is {total} words, needs 45-140 (about 20-55 seconds)")
    if any(re.search(r"https?://|#\w|\*\*", str(x or "")) for x in spoken):
        err.append("narration contains a link, hashtag or markdown; it is read aloud")
    _common(post, recent_topics, err, warn, min_caption_words=30)
    return err, warn


def _common(post, recent_topics, err, warn, min_caption_words):
    """Checks shared by carousels and reels: caption, hashtags, hype, sources, repeats."""
    cap = post.get("caption") or ""
    if _words(cap) < min_caption_words:
        err.append(f"caption too short (under {min_caption_words} words)")
    if len(cap) > 2000:
        err.append("caption too long for Instagram")
    tags = post.get("hashtags") or []
    if not 3 <= len(tags) <= 8:
        err.append(f"needs 3-8 hashtags, has {len(tags)}")
    if any(not re.fullmatch(r"#[a-z0-9_]{2,40}", t) for t in tags):
        err.append("a hashtag has invalid characters")
    text = json.dumps(post, ensure_ascii=False).lower()
    for h in HYPE:
        if h in text:
            err.append(f"hype word not allowed: '{h}'")
    srcs = post.get("sources") or []
    if any(not str(u).startswith("https://") for u in srcs):
        err.append("every source must be an https link")
    if post.get("pillar") in NEWS_PILLARS and not srcs:
        err.append("news post without a source link")
    topic = (post.get("topic") or "").lower()
    for t in recent_topics:
        if t and difflib.SequenceMatcher(None, topic, t.lower()).ratio() > 0.86:
            warn.append(f"very similar to an earlier post: '{t}'")
            break


SUITABILITY_SYSTEM = """You decide if a news story fits an educational Instagram account for students, junior
developers and IT/network engineers in Pakistan and India who want cloud, DevOps, AI or security jobs.
The account is calm, practical and non-political. Judge only from the story text given.

SUITABLE when both are true:
- it is about software, systems or tools this audience uses or will use at work (operating systems, cloud,
  DevOps tools, developer packages, browsers, phones, networks, AI tools) or it is a large, confirmed incident;
- the source states concrete facts, and a viewer can learn a clear lesson or action from it (for security:
  patch or update, check a setting, rotate leaked keys, spot a phishing sign, use MFA).

NOT SUITABLE when any is true:
- the main point is blaming a country, government or army, or it is about a political or military conflict
  (including India-Pakistan); or it is hacktivist claims that are not confirmed;
- it is built on rumours, anonymous unverified claims, or the facts are still unclear;
- it centres on naming, exposing or accusing a private person;
- it would point viewers to leaked personal data, or is mostly about how to carry out an attack;
- it is a narrow vendor advisory (e.g. industrial control systems) that almost none of this audience touches;
- it is gossip, stock prices, lawsuits or business deals with nothing to learn;
- it is a sponsored post or product advertisement.

Return JSON: {"suitable": true or false, "reason": "max 15 words"}"""


def news_suitable(story, pillar):
    """(ok, reason) for an AUTO_NEWS story. If the check itself fails, the story is allowed: the fact check
    and the owner's approval still come after it."""
    user = ["TASK: NEWS_SUITABILITY", f"Pillar: {pillar}", f"Source: {story.get('source')}",
            f"Title: {story.get('title')}", f"Text: {(story.get('text') or '')[:4000]}"]
    try:
        r = ask_json(SUITABILITY_SYSTEM, "\n".join(user))
    except LLMError as e:
        return True, f"suitability check could not run: {e}"
    return r.get("suitable") is True, str(r.get("reason") or "")[:200]


FACT_SYSTEM = """You are a strict technical editor and fact checker for an educational Instagram account.
Check every factual claim, command and example in the post. For NEWS, a claim is only correct if the source
text supports it. For evergreen topics, use well-established knowledge; flag anything outdated, wrong,
misleading or unsafe (for example a destructive command without a warning). Do not flag style.
Mark each issue's severity:
  "major" = factually wrong, a command or code that would not run as written (wrong function or method
            name, wrong flag, missing import, syntax error) or is unsafe, or anything not supported by the
            news source text.
  "minor" = correct but could be clearer, simplified, or a nice-to-have note.
Return JSON: {"verdict": "pass" or "fail", "issues": [{"where": "slide N or caption", "severity": "major" or "minor", "problem": "...", "fix": "..."}]}"""


def facts(post, source_text=None):
    user = ["TASK: FACT_CHECK", "Post JSON:", json.dumps(post, ensure_ascii=False)]
    if source_text:
        user += ["", "Source text (the only allowed facts for news):", source_text[:7000]]
    try:
        r = ask_json(FACT_SYSTEM, "\n".join(user))
    except LLMError as e:
        return {"verdict": "error", "issues": [{"where": "-", "problem": f"fact check could not run: {e}"}]}
    issues = [i for i in (r.get("issues") or []) if isinstance(i, dict)]
    # Only major issues block a post. Minor ones go to the owner as notes in the approval preview.
    # An issue without a severity counts as major, to stay on the safe side.
    r["issues"] = [i for i in issues if str(i.get("severity", "major")).lower() != "minor"]
    r["minor"] = [i for i in issues if str(i.get("severity", "major")).lower() == "minor"]
    r["verdict"] = "fail" if r["issues"] or (r.get("verdict") != "pass" and not r["minor"]) else "pass"
    return r


VISION_SYSTEM = """You review finished Instagram carousel slides or reel frames before publishing. Look at each image.
Report only real problems: text cut off or touching an edge, overlapping elements, spelling mistakes,
unreadable contrast, wrong slide order, a slide that looks empty or broken.
Return JSON: {"verdict": "pass" or "fail", "issues": [{"slide": N, "problem": "..."}]}"""


def visual(image_paths):
    if not can_see_images():
        return {"verdict": "skipped", "issues": [], "note": "no image-capable AI configured"}
    try:
        r = ask_json(VISION_SYSTEM, "TASK: VISION_CHECK\nSlides are attached in order, slide 1 first.",
                     images=[str(p) for p in image_paths[:10]])
    except LLMError as e:
        return {"verdict": "error", "issues": [{"slide": 0, "problem": f"vision check could not run: {e}"}]}
    r.setdefault("issues", [])
    r["verdict"] = "fail" if r.get("verdict") != "pass" or r["issues"] else "pass"
    return r


def issue_lines(fact_report):
    return [f"{i.get('where', '')}: {i.get('problem', '')} -> {i.get('fix', '')}".strip()
            for i in fact_report.get("issues", [])]
