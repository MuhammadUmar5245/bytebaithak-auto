"""Offline tests for the Discord bot (no network, no token):  python -m tests.test_discord"""
import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ["DISCORD_BOT_TOKEN"] = "test-token"
os.environ.pop("DISCORD_OWNER_ID", None)

import discord  # noqa: E402

from app import db, discord_bot  # noqa: E402
from app.config import S  # noqa: E402


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kw):
        self.sent.append(dict(kw, content=content))
        return SimpleNamespace(id=len(self.sent))


def test_preview_payload():
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i in range(7):
            p = Path(d) / f"slide-{i + 1:02d}.jpg"
            p.write_bytes(b"\xff\xd8" + b"0" * 2_000_000)  # ~2 MB each -> needs two upload batches
            paths.append(p)
        bot = SimpleNamespace(channel=FakeChannel())
        row = {"id": "2026-11-11_1300", "date": "2026-11-11", "slot": "13:00", "pillar": "cloud",
               "topic": "IaaS vs PaaS vs SaaS"}
        asyncio.run(discord_bot._preview(bot, row, paths, "caption text", ["slide 3: text dense"]))
        sent = bot.channel.sent
        albums = [m for m in sent if m.get("files")]
        assert sum(len(m["files"]) for m in albums) == 7 and len(albums) == 2, [len(m.get("files") or []) for m in sent]
        assert albums[0]["content"].startswith("🗓") and albums[1]["content"] is None
        names = [f.filename for m in albums for f in m["files"]]
        assert names == [f"slide-{i:02d}.jpg" for i in range(1, 8)], names
        last = sent[-1]
        ids = [c.custom_id for c in last["view"].children]
        assert ids == ["ok:2026-11-11_1300", "no:2026-11-11_1300", "pause"], ids
        assert last["embed"].title == "IaaS vs PaaS vs SaaS" and "text dense" in last["embed"].fields[0].value
    print("preview payload: ok")


def test_slash_commands_register():
    async def go():
        bot = discord_bot.Bot()
        await bot.setup_hook()
        return sorted(c.name for c in bot.tree.get_commands())
    names = asyncio.run(go())
    assert names == ["approve", "pause", "reject", "resume", "retry", "status"], names
    print("slash commands: ok")


class FakeResponse:
    def __init__(self):
        self.calls = []

    async def send_message(self, content, ephemeral=False):
        self.calls.append(("send", content, ephemeral))

    async def edit_message(self, content=None, view="unset"):
        self.calls.append(("edit", content, view))


def fake_interaction(user_id, owner_id, custom_id):
    return SimpleNamespace(type=discord.InteractionType.component, data={"custom_id": custom_id},
                           user=SimpleNamespace(id=user_id), guild=SimpleNamespace(owner_id=owner_id),
                           response=FakeResponse())


def test_buttons_owner_only():
    pid = "test-discord-post"
    db.upsert(pid, status="pending_approval", date="2026-11-11", slot="13:00", pillar="cloud", topic="Test")
    try:
        bot = discord_bot.Bot()
        stranger = fake_interaction(2, 1, f"ok:{pid}")
        asyncio.run(bot.on_interaction(stranger))
        assert stranger.response.calls[0][0] == "send" and stranger.response.calls[0][2] is True
        assert db.get(pid)["status"] == "pending_approval"

        owner = fake_interaction(1, 1, f"ok:{pid}")
        asyncio.run(bot.on_interaction(owner))
        kind, text, view = owner.response.calls[0]
        assert kind == "edit" and "approved" in text and view is None, owner.response.calls
        assert db.get(pid)["status"] == "approved"
    finally:
        db._conn.execute("DELETE FROM posts WHERE id=?", (pid,))
        db._conn.commit()
    print("buttons owner-only: ok")


def test_notify_selection():
    assert S.notify == "discord"
    assert "permissions=117776" in discord_bot.invite_url(123) and "client_id=123" in discord_bot.invite_url(123)
    print("notify selection + invite link: ok")


if __name__ == "__main__":
    test_preview_payload()
    test_slash_commands_register()
    test_buttons_owner_only()
    test_notify_selection()
    print("ALL DISCORD TESTS PASSED")
