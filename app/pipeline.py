"""The daily flow: calendar row -> write -> check -> render -> check -> approve -> publish -> learn."""
import csv
import json
import logging
import threading
from datetime import datetime, timedelta

from . import approval, checker, db, render_reel, research, writer
from .config import NEWS_PILLARS, S, SENSITIVE_PILLARS, now as tz_now, today
from .render_carousel import render

log = logging.getLogger("pipeline")


def post_id(d, slot):
    return f"{d}_{slot.replace(':', '')}"


def calendar_rows(d):
    path = S.data_dir / "calendar.csv"
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("date") == str(d)]


# ---------------------------------------------------------------- produce
def produce(row, preview=False):
    pid = ("preview-" + tz_now().strftime("%H%M%S")) if preview else post_id(row["date"], row["slot"])
    pillar, fmt, topic = row["pillar"], row.get("format", "carousel"), row["topic"]
    base = dict(date=row["date"], slot=row["slot"], pillar=pillar, format=fmt, topic=topic)
    out = S.output_dir / row["date"] / pid
    if not preview:
        db.upsert(pid, status="drafting", **base)

    is_reel = fmt == "reel"
    if is_reel and topic.strip().upper().startswith("FACE REEL"):
        msg = f"🎥 {row['date']} {row['slot']} wali Reel aap ko khud record karni hai (chehra chahiye): {topic}"
        if not preview:
            db.upsert(pid, status="manual", error="face reel: record it yourself")
            approval.send(msg)
        return {"id": pid, "status": "manual"}
    write = writer.write_reel if is_reel else writer.write_carousel
    check_rules = checker.reel_rules if is_reel else checker.rules
    render_fn = render_reel.render_frames if is_reel else render

    notes, report = [], {"checks": {}}
    try:
        news = None
        if topic == "AUTO_NEWS":
            kind = pillar if pillar in NEWS_PILLARS else "ai_news"
            recent = [p["topic"] for p in db.query("pillar=?", (kind,))][-40:]
            news = research.top_story(avoid_titles=recent, kind=kind,
                                      accept=lambda s: checker.news_suitable(s, kind))
            if not news:
                return _fail(pid, base, f"no fresh, suitable {kind} story with enough source text", preview)
            topic = news["title"]
            base["topic"] = topic
            report["news"] = {"source": news["source"], "link": news["link"], "skipped": news["rejected"]}
            notes += [f"news chhodi · {r}"[:300] for r in news["rejected"][:3]]

        recent_topics = [] if preview else db.recent_topics(exclude_id=pid)
        # "SCRIPT:name" = the owner's own words from data/scripts/name.json; the AI never rewrites them
        script = topic.strip().upper().startswith("SCRIPT:")
        if script:
            post = writer.load_script(topic.split(":", 1)[1].strip(), pillar, is_reel)
            topic = base["topic"] = post["topic"]
            if not preview:
                db.upsert(pid, topic=topic)
        else:
            post = write(topic, pillar, news=news, avoid=recent_topics)

        # text checks, with one automatic rewrite (never for the owner's own scripts)
        err, warn = check_rules(post, recent_topics)
        facts = checker.facts(post, news["text"] if news else None)
        if (err or facts["verdict"] == "fail") and not script:
            report["checks"]["first_draft"] = {"rules": err, "facts": facts}
            post = writer.revise(post, err + checker.issue_lines(facts), news)
            err, warn = check_rules(post, recent_topics)
            facts = checker.facts(post, news["text"] if news else None)
        report["checks"]["rules"] = {"errors": err, "warnings": warn}
        report["checks"]["facts"] = facts
        if err:
            return _fail(pid, base, "rules: " + "; ".join(err), preview, out, post, report)
        if facts["verdict"] == "fail":
            if not script:
                return _fail(pid, base, "facts: " + "; ".join(checker.issue_lines(facts)), preview, out, post, report)
            # owner's script: show the problems in the preview, the owner decides
            notes += [f"fact check · {l}"[:300] for l in checker.issue_lines(facts)[:4]]
        if facts["verdict"] == "error":
            notes.append("AI fact check chal nahi saka, khud parh lein")
        notes += warn
        notes += [f"fact tip · {i.get('where', '')}: {i.get('problem', '')}"[:300] for i in facts.get("minor", [])[:4]]

        # render, with one automatic shortening pass if text still overflows
        paths, layout = render_fn(post, out)
        if layout["overflow"] and not script:
            post = writer.revise(post, [f"{o}. Shorten that slide's text." for o in layout["overflow"]], news)
            paths, layout = render_fn(post, out)
        report["checks"]["layout"] = layout
        if layout["overflow"]:
            return _fail(pid, base, "layout: " + "; ".join(layout["overflow"]), preview, out, post, report)
        if layout["dense_slides"]:
            notes.append(f"slides {layout['dense_slides']} mein text ghana hai")

        vis = checker.visual(paths) if S.vision_check else {"verdict": "skipped", "issues": []}
        report["checks"]["visual"] = vis
        if vis["verdict"] in ("fail", "error"):
            notes += [f"slide {i.get('slide')}: {i.get('problem')}" for i in vis["issues"]]

        if is_reel:  # voice + video only after the text is final
            report["video"] = render_reel.make_video(post, paths, out)
            if report["video"]["seconds"] > 60:
                notes.append(f"reel {report['video']['seconds']}s lambi hai")

        caption = writer.full_caption(post)
        _save(out, post, report, caption)
        need = _needs_approval(pillar, notes)
        status = "pending_approval" if need else "approved"
        if preview:
            return {"id": pid, "status": status, "dir": str(out), "notes": notes}
        db.upsert(pid, status=status, content=post, report=dict(report, notes=notes),
                  media_dir=str(out), topic=topic, error=None)
        if need:
            _send_preview(db.get(pid))
        else:
            approval.send(f"✅ Auto-approved: {row['date']} {row['slot']} · {topic}")
        return {"id": pid, "status": status, "dir": str(out), "notes": notes}
    except Exception as e:  # never crash the scheduler; record and alert
        log.exception("produce failed")
        return _fail(pid, base, f"error: {e}", preview, out, None, report)


def _send_preview(p):
    """Send the approval preview; remember success so a missed one is re-sent later."""
    from pathlib import Path
    media = _media(p)
    caption = (Path(p["media_dir"]) / "caption.txt").read_text(encoding="utf-8")
    notes = (p.get("report") or {}).get("notes", [])
    if approval.preview(p, [str(s) for s in media], caption, notes):
        db.kv_set(f"previewed:{p['id']}", "1")
        return True
    return False


def _media(p):
    """What the approval preview shows: the video for a reel, the slides for a carousel."""
    from pathlib import Path
    d = Path(p["media_dir"])
    if p.get("format") == "reel" and (d / "reel.mp4").exists():
        return [d / "reel.mp4"]
    return sorted(d.glob("slide-*.jpg"))


def resend_previews():
    """Posts waiting for approval whose preview never arrived (chat app was down) get it again."""
    if S.notify == "none":
        return 0
    sent = 0
    for p in db.query("status='pending_approval' AND date >= ?", (str(today()),)):
        if db.kv_get(f"previewed:{p['id']}") != "1" and p.get("media_dir"):
            sent += bool(_send_preview(p))
    return sent


def _needs_approval(pillar, notes):
    if S.approval_mode == "all":
        return True
    if S.approval_mode == "news_only":
        return pillar in SENSITIVE_PILLARS or bool(notes)
    return bool(notes)


def _save(out, post, report, caption):
    out.mkdir(parents=True, exist_ok=True)
    (out / "post.json").write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "caption.txt").write_text(caption, encoding="utf-8")


def _fail(pid, base, reason, preview, out=None, post=None, report=None):
    log.warning("%s failed: %s", pid, reason)
    if out and post:
        _save(out, post, report or {}, writer.full_caption(post))
    if not preview:
        db.upsert(pid, status="failed_check", error=reason[:1000], content=post, report=report, **base)
        approval.send(f"🛑 Post ruk gayi: {base['date']} {base['slot']} · {base['topic']}\nWajah: {reason[:600]}")
    return {"id": pid, "status": "failed_check", "reason": reason}


_busy = set()          # post ids being generated right now (scheduler or /retry thread)
_busy_lock = threading.Lock()


def _produce_once(row):
    pid = post_id(row["date"], row["slot"])
    with _busy_lock:
        if pid in _busy:
            return {"id": pid, "status": "busy"}
        _busy.add(pid)
    try:
        return produce(row)
    finally:
        with _busy_lock:
            _busy.discard(pid)


def generate_for(d=None):
    d = str(d or today())
    done = []
    for row in calendar_rows(d):
        existing = db.get(post_id(row["date"], row["slot"]))
        # "drafting" here means an earlier run crashed half-way (container restart), so try again
        if existing and existing["status"] not in ("failed_check", "drafting"):
            continue
        done.append(_produce_once(row))
    if not done:
        log.info("nothing to generate for %s", d)
    return done


# ---------------------------------------------------------------- publish
def publish_due(now=None):
    if db.paused():
        return []
    now = now or tz_now()
    results = []
    for p in db.query("status='approved'"):
        when = datetime.strptime(f"{p['date']} {p['slot']}", "%Y-%m-%d %H:%M")
        if when > now:
            continue
        # late_ok = owner approved / retried after the slot had passed, so publish now instead of skipping
        if now - when > timedelta(hours=S.catchup_hours) and db.kv_get(f"late_ok:{p['id']}") != "1":
            db.upsert(p["id"], status="skipped", error="slot missed (PC off?)")
            approval.send(f"⏭ Skipped (waqt nikal gaya): {p['date']} {p['slot']} · {p['topic']}")
            continue
        results.append(publish_one(p))
    return results


def publish_one(p):
    from pathlib import Path
    from . import instagram, storage
    d = Path(p["media_dir"])
    slides = sorted(d.glob("slide-*.jpg"))
    caption = (d / "caption.txt").read_text(encoding="utf-8")
    reel = p.get("format") == "reel"
    what = "reel" if reel else f"{len(slides)} slides"
    if S.dry_run:
        db.upsert(p["id"], status="dry_run", error=None)
        approval.send(f"🧪 DRY RUN (post nahi hui): {p['date']} {p['slot']} · {p['topic']} · {what}")
        return {"id": p["id"], "status": "dry_run"}
    try:
        used, total = instagram.quota()
        if used >= total - 1:
            raise RuntimeError(f"Instagram daily limit reached ({used}/{total})")
        if reel:
            video_url, cover_url = storage.upload([d / "reel.mp4", slides[0]], f"{p['date']}/{p['id']}")
            media_id, link = instagram.publish_reel(video_url, caption, cover_url)
        else:
            urls = storage.upload(slides, f"{p['date']}/{p['id']}")
            alt = (p.get("content") or {}).get("alt_text")
            media_id, link = instagram.publish_carousel(urls, caption, alt)
        db.upsert(p["id"], status="published", ig_media_id=media_id, permalink=link, error=None)
        approval.send(f"🚀 Published: {p['topic']}\n{link}")
        return {"id": p["id"], "status": "published", "link": link}
    except Exception as e:
        log.exception("publish failed")
        db.upsert(p["id"], status="publish_failed", error=str(e)[:1000])
        approval.send(f"⚠️ Publish fail: {p['topic']}\n{str(e)[:500]}\nDobara try: /retry {p['id']}")
        return {"id": p["id"], "status": "publish_failed", "error": str(e)}


def _slot_passed(p):
    return datetime.strptime(f"{p['date']} {p['slot']}", "%Y-%m-%d %H:%M") <= tz_now()


def set_status(pid, status):
    p = db.get(pid)
    if not p:
        return f"Post '{pid}' nahi mili"
    if status == "approved" and p["status"] not in ("pending_approval", "publish_failed", "rejected", "approved"):
        return f"'{pid}' abhi '{p['status']}' hai, approve nahi ho sakti"
    if status == "rejected" and p["status"] in ("published", "drafting"):
        return f"'{pid}' abhi '{p['status']}' hai, reject nahi ho sakti"
    db.upsert(pid, status=status)
    icon = {"approved": "✅", "rejected": "❌"}.get(status, "•")
    msg = f"{icon} {p['date']} {p['slot']} · {p['topic']} → {status}"
    if status == "approved" and _slot_passed(p):
        db.kv_set(f"late_ok:{pid}", "1")
        msg += "\n(waqt guzar chuka hai, agle minute post ho jayegi)"
    return msg


def retry(pid, background=True):
    """failed_check / drafting -> write the post again. publish_failed / skipped -> publish again now."""
    p = db.get(pid)
    if not p:
        return f"Post '{pid}' nahi mili"
    if p["status"] in ("publish_failed", "skipped"):
        db.upsert(pid, status="approved", error=None)
        db.kv_set(f"late_ok:{pid}", "1")
        return f"🔁 {p['date']} {p['slot']} · {p['topic']} → agle minute dobara publish hogi"
    if p["status"] in ("failed_check", "drafting"):
        row = next((r for r in calendar_rows(p["date"]) if r.get("slot") == p["slot"]), None)
        if not row:
            return f"'{pid}' calendar.csv mein nahi mili, dobara nahi ban sakti"
        if not background:
            return str(_produce_once(row))
        threading.Thread(target=_produce_once, args=(row,), name=f"retry-{pid}", daemon=True).start()
        return f"🔁 {p['date']} {p['slot']} · {p['topic']} dobara ban rahi hai, preview thori der mein aayega"
    return f"'{pid}' abhi '{p['status']}' hai, retry ki zaroorat nahi"


def status_text(d=None):
    d = str(d or today())
    rows = db.query("date=?", (d,))
    if not rows:
        return f"{d}: koi post nahi"
    paused = " (⏸ PAUSED)" if db.paused() else ""
    lines = [f"{d}{paused}"] + [f"{r['slot']} · {r['status']} · {r['topic']}" + (f"\n   ↳ {r['error'][:120]}" if r.get('error') else "")
                                for r in rows]
    return "\n".join(lines)


# ---------------------------------------------------------------- learn
def collect_insights():
    from . import instagram
    now = tz_now()
    for p in db.query("status='published' AND ig_media_id IS NOT NULL"):
        published = datetime.fromisoformat(p["updated_at"])
        have = {m["hours"] for m in db.metrics_since(30) if m["post_id"] == p["id"]}
        for hours in (24, 72):
            if hours not in have and now - published >= timedelta(hours=hours):
                try:
                    db.save_metrics(p["id"], hours, instagram.insights(p["ig_media_id"]))
                except Exception as e:
                    log.warning("insights %s failed: %s", p["id"], e)


REVIEW_SYSTEM = f"""You are the content strategist for an educational Instagram account. {writer.ABOUT}
Pillars: ai_news, cyber_news, super_ai, ml, devops, cloud, code. Formats: carousels and 25-45 second Reels
(on-screen text, code and an AI voiceover, no face).

You get the account's post results (72 hours after posting), the topics already posted, and the topics
already planned. Judge success mainly by saves + shares per 1,000 reach, then reach. Only state patterns the
numbers actually show; with few posts, say the evidence is weak. Do not invent numbers.
Posting times are fixed before 17:00 Pakistan time (the owner's PC may be off later), so never suggest
evening or night posting; experiments must change content, hooks, format or length instead.
Missing topics: what this audience needs on the way to a cloud, DevOps or AI job (hands-on skills,
interview prep, certifications like CCNA, AWS and CKA, portfolio projects, career moves) that is neither
posted nor planned. Each topic must fit one carousel or one 30-45 second Reel.

Return ONLY JSON:
{{"patterns": ["max 5 findings, each naming the numbers behind it"],
  "experiments": [{{"change": "what to try", "keep": "what stays the same", "metric": "which number decides",
                    "posts": "how many posts to judge it"}}],
  "ideas": [{{"pillar": "...", "format": "reel or carousel", "topic": "new topic in the angle of the winners"}}],
  "missing_topics": [{{"pillar": "...", "topic": "...", "why": "max 15 words"}}]}}
Give 5 experiments, 10 ideas and 10 missing topics. With no results, return empty patterns, experiments
and ideas, and still give the missing topics."""


def _all_calendar_rows():
    with open(S.data_dir / "calendar.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _scored(rows):
    for m in rows:
        d = m["data"]
        reach = max(d.get("reach") or d.get("views") or 1, 1)
        m["score"] = round(1000 * ((d.get("saved") or 0) + (d.get("shares") or 0)) / reach, 1)
        content = (db.get(m["post_id"]) or {}).get("content") or {}
        m["format"] = content.get("format") or "carousel"
        m["hook"] = ((content.get("hook") or content.get("cover") or {}).get("title") or "").replace("**", "")
    return sorted(rows, key=lambda m: m["score"], reverse=True)


def _chunks(lines, size=1900):
    """Discord takes 2,000 characters per message, so send long text in pieces."""
    out, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = ""
        cur = (cur + "\n" + line) if cur else line[:size]
    return out + [cur] if cur else out


def weekly_report():
    """Sunday: top/weak posts, what the numbers show, 5 experiments, new ideas and missing topics.
    Sent to the approvals chat and saved as output/reports/weekly-<date>.md."""
    week = _scored([m for m in db.metrics_since(8) if m["hours"] == 72])
    month = _scored([m for m in db.metrics_since(30) if m["hours"] == 72])
    fmt = lambda m: f"{m['score']:>5} · {m['format']} · {m['pillar']} · {m['topic']}"
    text = ["📊 **Weekly report** (score = saves+shares per 1,000 reach)"]
    if week:
        text += ["**Top:**", *map(fmt, week[:3])]
        if week[3:]:  # with few posts, don't list the same post as both top and weak
            text += ["**Kamzor:**", *map(fmt, week[3:][-3:])]
    else:
        text.append("Is hafte ka 72-ghante wala data abhi nahi hai.")

    posted = [p["topic"] for p in db.query("status IN ('published','dry_run','approved','pending_approval')")]
    planned = [r["topic"] for r in _all_calendar_rows() if r.get("date", "") >= str(today())]
    results = [{"topic": m["topic"], "format": m["format"], "pillar": m["pillar"], "hook": m["hook"],
                "score": m["score"], **{k: m["data"].get(k) for k in ("reach", "views", "saved", "shares",
                                                                         "likes", "comments")}} for m in month]
    try:
        from .llm import ask_json
        r = ask_json(REVIEW_SYSTEM, "\n".join([
            "TASK: WEEKLY_REVIEW",
            "Results of the last 30 days: " + (json.dumps(results, ensure_ascii=False) if results else "none yet"),
            "Already posted: " + "; ".join(posted[-80:]),
            "Already planned: " + "; ".join(planned[:60])]))
        if r.get("patterns"):
            text += ["", "**Numbers kya keh rahe hain:**", *[f"• {x}" for x in r["patterns"][:5]]]
            if results:  # the writer reads these as hints for every new post (writer.learned)
                db.kv_set("learnings", json.dumps([str(x) for x in r["patterns"][:5]], ensure_ascii=False))
        if r.get("experiments"):
            text += ["", "**Agle 2 hafte ke experiments:**"]
            text += [f"{i}. {e.get('change')} (same: {e.get('keep')}; dekhna: {e.get('metric')}; {e.get('posts')})"
                     for i, e in enumerate(r["experiments"][:5], 1) if isinstance(e, dict)]
        if r.get("ideas"):
            text += ["", "**Winners jaise naye ideas:**"]
            text += [f"• {i.get('pillar')} · {i.get('format')}: {i.get('topic')}"
                     for i in r["ideas"][:10] if isinstance(i, dict)]
        if r.get("missing_topics"):
            text += ["", "**Jo audience ko chahiye, abhi cover nahi (missing topics):**"]
            text += [f"• {i.get('pillar')}: {i.get('topic')} — {i.get('why')}"
                     for i in r["missing_topics"][:10] if isinstance(i, dict)]
        text += ["", "Pasand aaye to topic `data/calendar.csv` mein daal dein."]
    except Exception as e:
        log.warning("weekly review failed: %s", e)
        text.append(f"(AI review nahi chal saka: {str(e)[:200]})")

    folder = S.output_dir / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"weekly-{today()}.md").write_text("\n".join(text), encoding="utf-8")
    for part in _chunks(text):
        approval.send(part)
    return text
