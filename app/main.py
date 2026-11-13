"""Entry point.  python -m app.main <command>

run                 scheduler (what docker compose starts)
preview             make one test post now, nothing is published
generate [--date]   make the posts in calendar.csv for a date
publish-due         publish approved posts whose time has come
status [--date]     list posts and their state
approve|reject|retry ID
pause | resume
check               test AI, Discord/Telegram, R2 and Instagram settings
discord-setup       log the Discord bot in and print its invite link
telegram-chat-id    show the chat id after you message your Telegram bot
report              send the weekly report now
"""
import argparse
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import S, today

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout),
                              logging.FileHandler(S.data_dir / "bot.log", encoding="utf-8")])
log = logging.getLogger("main")


def run():
    from apscheduler.schedulers.blocking import BlockingScheduler
    from . import approval, instagram, pipeline

    h, m = map(int, S.generate_at.split(":"))
    sch = BlockingScheduler(timezone=S.tz, job_defaults={"coalesce": True, "max_instances": 1,
                                                          "misfire_grace_time": 3600})
    sch.add_job(pipeline.generate_for, "cron", hour=h, minute=m, id="generate")
    sch.add_job(pipeline.publish_due, "interval", minutes=1, id="publish")
    if S.notify == "telegram":
        sch.add_job(approval.poll, "interval", seconds=10, id="telegram")
    sch.add_job(pipeline.collect_insights, "interval", minutes=60, id="insights")
    sch.add_job(pipeline.resend_previews, "interval", minutes=5, id="resend")
    sch.add_job(pipeline.weekly_report, "cron", day_of_week="sun", hour=10, minute=0, id="weekly")
    if not S.dry_run:
        sch.add_job(instagram.refresh_token, "cron", day_of_week="mon", hour=5, minute=0, id="token")

    # catch-up: PC was off at GENERATE_AT
    now = datetime.now(ZoneInfo(S.tz))
    if (now.hour, now.minute) >= (h, m):
        sch.add_job(pipeline.generate_for, id="catchup", next_run_time=now)
    mode = "DRY RUN" if S.dry_run else "LIVE"
    approval.start()
    try:
        from . import dashboard
        dashboard.start()
    except OSError as e:  # port busy: the bot keeps running without the dashboard
        log.warning("dashboard could not start: %s", e)
    log.info("ByteBaithak bot started (%s, approval=%s, ai=%s→%s, notify=%s)", mode, S.approval_mode,
             S.llm_provider, S.llm_fallback, S.notify)
    approval.send(f"🤖 ByteBaithak bot chal para · {mode} · approval={S.approval_mode}\n/status likh kar aaj ki posts dekhein")
    sch.start()


def check():
    from . import approval, instagram, llm, storage
    ok = True

    def row(name, fn):
        nonlocal ok
        try:
            print(f"  OK   {name}: {fn()}")
        except Exception as e:
            ok = False
            print(f"  FAIL {name}: {e}")

    print(f"Settings: handle={S.handle} ai={S.llm_provider}->{S.llm_fallback} dry_run={S.dry_run} approval={S.approval_mode}")
    row("AI", lambda: llm.ask_json("Reply with JSON.", 'Return {"pong": true}'))
    if S.vision_check:
        row("AI can see images", lambda: llm.can_see_images())
    row("Approvals chat", approval.check)
    row("Cloudflare R2", storage.check)
    row("Instagram", instagram.me)
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(prog="app.main")
    ap.add_argument("cmd")
    ap.add_argument("id", nargs="?")
    ap.add_argument("--date")
    ap.add_argument("--topic", default="IaaS vs PaaS vs SaaS")
    ap.add_argument("--pillar", default="cloud")
    a = ap.parse_args(argv)

    from . import approval, db, pipeline
    if a.cmd == "run":
        return run()
    if a.cmd == "preview":
        r = pipeline.produce({"date": str(today()), "slot": "00:00", "pillar": a.pillar,
                              "format": "carousel", "topic": a.topic}, preview=True)
        print(r)
        return 0 if r["status"] != "failed_check" else 1
    if a.cmd == "generate":
        for r in pipeline.generate_for(a.date):
            print(r)
        return 0
    if a.cmd == "publish-due":
        print(pipeline.publish_due())
        return 0
    if a.cmd == "status":
        print(pipeline.status_text(a.date))
        return 0
    if a.cmd in ("approve", "reject"):
        print(pipeline.set_status(a.id, "rejected" if a.cmd == "reject" else "approved"))
        return 0
    if a.cmd == "retry":
        print(pipeline.retry(a.id, background=False))
        return 0
    if a.cmd in ("pause", "resume"):
        db.kv_set("paused", "1" if a.cmd == "pause" else "0")
        print(f"posting {a.cmd}d")
        return 0
    if a.cmd == "check":
        return 0 if check() else 1
    if a.cmd == "discord-setup":
        from . import discord_bot
        info = discord_bot.setup_info()
        print(f"Bot: {info['bot']}")
        print(f"Servers: {info['servers'] or 'none yet'}")
        print(f"Approvals channel: {info['channel'] or 'will be created after you invite the bot'}")
        print("\nInvite link (browser mein kholein, apna server chunein, Authorize):\n" + info["invite"])
        return 0
    if a.cmd == "telegram-chat-id":
        from . import telegram_bot
        print("Chats that messaged the bot:", telegram_bot.chat_ids())
        return 0
    if a.cmd == "report":
        pipeline.weekly_report()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)
