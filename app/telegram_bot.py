"""Telegram approval bot (alternative to Discord). Polling, so no public IP or webhook is needed.

Only messages from TELEGRAM_CHAT_ID are obeyed.
"""
import json
import logging

import requests

from . import db
from .config import S

log = logging.getLogger("telegram")


def enabled():
    return bool(S.tg_token and S.tg_chat)


def _api(method, files=None, **data):
    if not S.tg_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    r = requests.post(f"https://api.telegram.org/bot{S.tg_token}/{method}", data=data, files=files, timeout=60)
    out = r.json()
    if not out.get("ok"):
        raise RuntimeError(f"telegram {method}: {out.get('description')}")
    return out["result"]


def send(text, buttons=None):
    if not enabled():
        return None
    data = {"chat_id": S.tg_chat, "text": text[:4000], "disable_web_page_preview": "true"}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        return _api("sendMessage", **data)
    except (RuntimeError, requests.RequestException) as e:
        log.warning("telegram send failed: %s", e)


def preview(row, image_paths, caption, notes):
    if not enabled():
        return None
    files, media = {}, []
    try:
        for i, p in enumerate(list(image_paths)[:10]):
            files[f"f{i}"] = open(p, "rb")
            media.append({"type": "video" if str(p).endswith(".mp4") else "photo", "media": f"attach://f{i}"})
        _api("sendMediaGroup", files=files, chat_id=S.tg_chat, media=json.dumps(media))
    except (RuntimeError, requests.RequestException) as e:
        log.warning("album failed: %s", e)
    finally:
        for f in files.values():
            f.close()
    pid = row["id"]
    head = f"🗓 {row['date']} {row['slot']} · {row['pillar']}\n📌 {row['topic']}"
    if notes:
        head += "\n\n⚠️ Check karein:\n" + "\n".join(f"• {n}" for n in notes[:8])
    return send(f"{head}\n\n— Caption —\n{caption}",
                buttons=[[{"text": "✅ Approve", "callback_data": f"ok:{pid}"},
                          {"text": "❌ Reject", "callback_data": f"no:{pid}"}],
                         [{"text": "⏸ Pause all posting", "callback_data": "pause"}]])


def poll():
    """Handle new button taps and commands. Called every few seconds by the scheduler."""
    if not enabled():
        return
    from . import approval
    offset = int(db.kv_get("tg_offset", "0"))
    try:
        updates = _api("getUpdates", offset=offset, timeout=0,
                       allowed_updates=json.dumps(["message", "callback_query"]))
    except (RuntimeError, requests.RequestException) as e:
        log.debug("poll failed: %s", e)
        return
    for u in updates:
        db.kv_set("tg_offset", u["update_id"] + 1)
        if "callback_query" in u:
            cq = u["callback_query"]
            if str(cq["message"]["chat"]["id"]) != str(S.tg_chat):
                continue
            reply = approval.handle(cq.get("data", ""))
            try:
                _api("answerCallbackQuery", callback_query_id=cq["id"], text=reply[:190])
            except (RuntimeError, requests.RequestException):
                pass
            send(reply)
        elif "message" in u:
            m = u["message"]
            if str(m["chat"]["id"]) == str(S.tg_chat):
                send(approval.handle(m.get("text", "")))


def check():
    if not enabled():
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    if not send("✅ Test message from ByteBaithak bot"):
        raise RuntimeError("could not send, check token and chat id")
    return "test message sent"


def chat_ids():
    """For setup: list chats that have messaged the bot."""
    res = _api("getUpdates", timeout=0)
    return sorted({(u.get("message") or {}).get("chat", {}).get("id") for u in res} - {None})
