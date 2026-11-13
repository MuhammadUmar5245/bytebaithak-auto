"""Discord approval bot: slide previews with Approve / Reject buttons, slash commands and alerts.

The discord.py client runs on its own thread; the rest of the app calls send() / preview().
Only the server owner (or DISCORD_OWNER_ID) can press the buttons or use the commands.
Outbound connection only: no public IP or webhook URL is needed.
"""
import asyncio
import logging
import threading
from pathlib import Path

import discord
from discord import app_commands

from .config import S

log = logging.getLogger("discordbot")
# discord.py is chatty; keep its warnings but drop the voice-support notice we never need
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.client").addFilter(lambda r: "PyNaCl" not in r.getMessage())
CHANNEL_NAME = "bytebaithak-approvals"
# View channel, Send messages, Embed links, Attach files, Read history, Manage channels (to create the channel)
PERMISSIONS = 1024 + 2048 + 16384 + 32768 + 65536 + 16
UPLOAD_LIMIT = 9_500_000  # bytes per message, under Discord's 10 MB default

_bot = None
_thread = None
_ready = threading.Event()
_start_lock = threading.Lock()
_last_error = None


def enabled():
    return bool(S.discord_token)


def invite_url(app_id):
    return (f"https://discord.com/oauth2/authorize?client_id={app_id}"
            f"&scope=bot+applications.commands&permissions={PERMISSIONS}")


class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.channel = None

    # ---------- setup
    async def setup_hook(self):
        def simple(name, desc, text):
            async def run(inter: discord.Interaction):
                await self.reply_command(inter, text)
            self.tree.command(name=name, description=desc)(run)

        simple("status", "Aaj ki posts aur un ka status", "/status")
        simple("pause", "Saari posting fauran rok do", "/pause")
        simple("resume", "Posting dobara shuru karo", "/resume")

        def with_id(name, desc):
            @app_commands.describe(post_id="Post ID, e.g. 2026-11-11_1300")
            async def run(inter: discord.Interaction, post_id: str):
                await self.reply_command(inter, f"/{name} {post_id}")
            self.tree.command(name=name, description=desc)(run)

        with_id("approve", "Ek post approve karo")
        with_id("reject", "Ek post reject karo")
        with_id("retry", "Fail hui post dobara try karo")

    async def on_ready(self):
        log.info("discord: logged in as %s, %d server(s)", self.user, len(self.guilds))
        if not self.guilds:
            log.warning("bot is in no server yet. Invite it: %s", invite_url(self.application_id))
        self.channel = await self.pick_channel()
        for g in self.guilds:
            await self.sync_commands(g)
        _ready.set()

    async def on_guild_join(self, guild):
        log.info("discord: joined %s", guild.name)
        if self.channel is None:
            self.channel = await self.pick_channel()
        await self.sync_commands(guild)

    async def sync_commands(self, guild):
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        except discord.HTTPException as e:
            log.warning("slash command sync failed in %s: %s", guild.name, e)

    async def pick_channel(self):
        if S.discord_channel:
            ch = self.get_channel(int(S.discord_channel))
            if ch:
                return ch
            log.warning("DISCORD_CHANNEL_ID %s not found, falling back", S.discord_channel)
        for g in self.guilds:
            for ch in g.text_channels:
                if ch.name == CHANNEL_NAME:
                    return ch
        for g in self.guilds:
            private = {g.default_role: discord.PermissionOverwrite(view_channel=False),
                       g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                         attach_files=True, embed_links=True)}
            for overwrites in (private, None):
                try:
                    kwargs = {"overwrites": overwrites} if overwrites else {}
                    ch = await g.create_text_channel(CHANNEL_NAME, topic="ByteBaithak post previews and approvals",
                                                     **kwargs)
                    log.info("created #%s in %s", CHANNEL_NAME, g.name)
                    return ch
                except discord.HTTPException as e:
                    log.warning("could not create channel (%s)", e)
            for ch in g.text_channels:
                if ch.permissions_for(g.me).send_messages:
                    return ch
        return None

    # ---------- who may act
    def allowed(self, inter):
        if S.discord_owner:
            return str(inter.user.id) == str(S.discord_owner)
        return inter.guild is not None and inter.user.id == inter.guild.owner_id

    async def reply_command(self, inter, text):
        if not self.allowed(inter):
            return await inter.response.send_message("Sirf owner yeh kar sakta hai.", ephemeral=True)
        from . import approval
        reply = await asyncio.to_thread(approval.handle, text)
        await inter.response.send_message(reply[:2000])

    async def on_interaction(self, inter):
        if inter.type != discord.InteractionType.component:
            return  # slash commands are handled by the command tree
        cid = (inter.data or {}).get("custom_id", "")
        if not (cid.startswith(("ok:", "no:")) or cid == "pause"):
            return
        if not self.allowed(inter):
            return await inter.response.send_message("Sirf owner yeh kar sakta hai.", ephemeral=True)
        from . import approval
        reply = await asyncio.to_thread(approval.handle, cid)
        try:
            await inter.response.edit_message(content=reply[:2000], view=None)  # removes the buttons
        except discord.HTTPException:
            await inter.followup.send(reply[:2000])


# ---------------------------------------------------------------- thread plumbing
def start():
    global _bot, _thread
    if not enabled():
        return
    with _start_lock:
        if _thread and _thread.is_alive():
            return
        _ready.clear()
        _bot = Bot()

        def runner():
            global _last_error
            try:
                _last_error = None
                _bot.run(S.discord_token, log_handler=None)
            except discord.LoginFailure:
                _last_error = "Discord rejected the token, check DISCORD_BOT_TOKEN"
                log.error(_last_error)
            except Exception as e:  # network down, ...
                _last_error = f"could not connect to Discord: {e}"
                log.error(_last_error)
            finally:
                _ready.set()  # never leave callers waiting

        _thread = threading.Thread(target=runner, name="discord", daemon=True)
        _thread.start()


def _call(make_coro, timeout=120):
    if not enabled():
        return None
    start()
    if not _ready.wait(60):
        log.warning("discord not ready after 60s")
        return None
    if _bot is None or _bot.is_closed() or _bot.channel is None:
        log.warning("discord has no channel to post in (is the bot invited to your server?)")
        return None
    fut = asyncio.run_coroutine_threadsafe(make_coro(_bot), _bot.loop)
    try:
        return fut.result(timeout)
    except Exception as e:
        log.warning("discord call failed: %s", e)
        return None


# ---------------------------------------------------------------- messages
async def _send(bot, text):
    return await bot.channel.send(text[:2000])


def _batches(paths):
    batch, size = [], 0
    for p in paths:
        s = Path(p).stat().st_size
        if batch and (size + s > UPLOAD_LIMIT or len(batch) == 10):
            yield batch
            batch, size = [], 0
        batch.append(p)
        size += s
    if batch:
        yield batch


def build_view(post_id):
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Approve", emoji="✅", style=discord.ButtonStyle.success,
                                    custom_id=f"ok:{post_id}"))
    view.add_item(discord.ui.Button(label="Reject", emoji="❌", style=discord.ButtonStyle.danger,
                                    custom_id=f"no:{post_id}"))
    view.add_item(discord.ui.Button(label="Pause all posting", emoji="⏸", style=discord.ButtonStyle.secondary,
                                    custom_id="pause"))
    return view


def build_embed(row, caption, notes):
    emb = discord.Embed(title=str(row["topic"])[:256], description=caption[:4000], color=0x22D3EE)
    if notes:
        emb.add_field(name="⚠️ Check karein", value="\n".join(f"• {n}" for n in notes[:8])[:1024], inline=False)
    emb.set_footer(text=f"ID: {row['id']}")
    return emb


async def _preview(bot, row, paths, caption, notes):
    video = any(str(p).endswith(".mp4") for p in paths)
    what = "🎬 reel" if video else f"{len(paths)} slides"
    head = f"🗓 **{row['date']} {row['slot']}** · {row['pillar']} · {what}"
    too_big = [p for p in paths if Path(p).stat().st_size > UPLOAD_LIMIT]
    if too_big:  # Discord's upload limit; the file is still on disk for publishing
        head += f"\n⚠️ video {Path(too_big[0]).stat().st_size / 1e6:.1f} MB, Discord par nahi aa sakti: {too_big[0]}"
        paths = [p for p in paths if p not in too_big]
    if not paths:
        await bot.channel.send(head)
    n = 0
    for batch in _batches(paths[:10]):
        files = [discord.File(p, filename=Path(p).name) for p in batch]
        n += len(batch)
        await bot.channel.send(content=head if n == len(batch) else None, files=files)
    view = build_view(row["id"])
    msg = await bot.channel.send(embed=build_embed(row, caption, notes), view=view)
    view.stop()  # clicks are handled in on_interaction, also after restarts
    return msg


def send(text):
    return _call(lambda bot: _send(bot, text))


def preview(row, image_paths, caption, notes):
    return _call(lambda bot: _preview(bot, row, list(image_paths), caption, notes), timeout=240)


def check():
    if not enabled():
        raise RuntimeError("DISCORD_BOT_TOKEN not set in .env")
    start()
    _ready.wait(60)
    if _bot is None or _bot.user is None:
        raise RuntimeError(_last_error or "login failed, check DISCORD_BOT_TOKEN")
    if not _bot.guilds:
        raise RuntimeError(f"bot is in no server yet. Open this link to invite it: {invite_url(_bot.application_id)}")
    if _bot.channel is None:
        raise RuntimeError("no channel the bot can post in")
    if not send("✅ Test message from ByteBaithak bot"):
        raise RuntimeError("could not send a message")
    return f"logged in as {_bot.user}, posting in #{_bot.channel.name} ({_bot.channel.guild.name})"


def setup_info():
    if not enabled():
        raise RuntimeError("DISCORD_BOT_TOKEN not set in .env")
    start()
    _ready.wait(60)
    if _bot is None or _bot.user is None:
        raise RuntimeError(_last_error or "login failed, check DISCORD_BOT_TOKEN")
    return {"bot": str(_bot.user), "invite": invite_url(_bot.application_id),
            "servers": [g.name for g in _bot.guilds],
            "channel": f"#{_bot.channel.name}" if _bot.channel else None}
