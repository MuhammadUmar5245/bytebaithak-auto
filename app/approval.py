"""Where previews, approvals and alerts go: Discord (default), Telegram, or nowhere (logs only).

Pick with NOTIFY=discord | telegram | none in .env. The rest of the app only calls the functions here.
"""
import logging

from . import db
from .config import S

log = logging.getLogger("notify")


def _backend():
    if S.notify == "discord":
        from . import discord_bot
        return discord_bot
    if S.notify == "telegram":
        from . import telegram_bot
        return telegram_bot
    return None


def start():
    b = _backend()
    if b and hasattr(b, "start"):
        b.start()


def send(text):
    b = _backend()
    if not b or not b.enabled():
        log.info("[notify off] %s", text)
        return None
    return b.send(text)


def preview(row, image_paths, caption, notes):
    b = _backend()
    if not b or not b.enabled():
        log.info("[notify off] preview %s (%d slides) waiting for approval", row["id"], len(image_paths))
        return None
    return b.preview(row, image_paths, caption, notes)


def poll():
    if S.notify == "telegram":
        from . import telegram_bot
        telegram_bot.poll()


def check():
    b = _backend()
    if not b:
        raise RuntimeError("NOTIFY=none, approvals only via command line")
    return f"{S.notify}: {b.check()}"


HELP = ("Commands: /status (aaj ki posts) · /pause (saari posting band) · /resume · "
        "/approve ID · /reject ID · /retry ID")


def handle(cmd):
    """Shared command logic for every chat backend. Returns the reply text."""
    from . import pipeline  # late import to avoid a cycle
    cmd = (cmd or "").strip()
    if cmd.startswith("ok:") or cmd.startswith("/approve"):
        return pipeline.set_status(_arg(cmd), "approved")
    if cmd.startswith("no:") or cmd.startswith("/reject"):
        return pipeline.set_status(_arg(cmd), "rejected")
    if cmd.startswith("/retry"):
        return pipeline.retry(_arg(cmd))
    if cmd in ("pause", "/pause"):
        db.kv_set("paused", "1")
        return "⏸ Posting paused. /resume se dobara shuru."
    if cmd == "/resume":
        db.kv_set("paused", "0")
        return "▶️ Posting resumed."
    if cmd == "/status":
        return pipeline.status_text()
    return HELP


def _arg(cmd):
    if ":" in cmd and not cmd.startswith("/"):
        return cmd.split(":", 1)[1]
    parts = cmd.split()
    return parts[1] if len(parts) > 1 else ""
