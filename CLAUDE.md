# ByteBaithak Auto-Poster

Automation for the Instagram account **@byte_baithak** (AI + computer science education, run by a senior
network engineer and Cisco Networking Academy instructor in Pakistan). It writes, checks, renders and
publishes daily carousel posts. The owner talks in Hinglish / Roman Urdu and wants direct, actionable answers.

## Hard rules

- **Docker only.** Everything runs with `docker compose`. Do not install Python, databases or packages
  directly on the Windows host. Test commands go through `docker compose run --rm worker ...`.
- **Never read, print or edit `.env`.** It holds API keys and tokens. Ask the owner to change values
  themselves. Use `.env.example` to see which settings exist.
- **Keep `DRY_RUN=true`** until the owner explicitly says to go live. Never publish to Instagram on your own.
- Only the official Instagram API (Instagram Login) is used. No password logins, no unofficial libraries,
  no automated likes, follows, comments or DMs.
- Never invent facts in content code or prompts. News posts must stay grounded in the fetched source text.

## Commands

```powershell
docker compose build
docker compose run --rm worker python -m app.main check        # test AI / Discord / R2 / Instagram settings
docker compose run --rm worker python -m app.main discord-setup  # print the bot invite link
docker compose run --rm worker python -m app.main preview --topic "What is Docker?" --pillar devops
docker compose run --rm worker python -m app.main preview --format reel --pillar devops --topic "Kubernetes 22/30: HPA"
docker compose run --rm worker python -m app.main report      # weekly AI review now (also every Sunday 10:00)
docker compose run --rm worker python -m app.main status --date 2026-11-11
docker compose run --rm -e LLM_PROVIDER=mock -e LLM_FALLBACK=mock worker python -m tests.test_offline
docker compose run --rm -e LLM_PROVIDER=mock worker python -m tests.test_discord
docker compose up -d        # start the scheduler
docker compose logs -f      # watch logs
```

Pillars: `ai_news`, `cyber_news`, `super_ai`, `ml`, `devops`, `cloud`, `code`. Content plan lives in `data/calendar.csv`
(`AUTO_NEWS` as topic = research the day's top story for that row's pillar, `ai_news` or `cyber_news`; `Name N/30: topic` = day N of a reel series,
e.g. the Kubernetes 1/30..30/30 series at 11:00 from 2026-11-27; `X Course LN: topic` = course lesson).

## How it works (app/)

| File | Job |
| --- | --- |
| `main.py` | CLI + APScheduler (06:00 generate, every minute publish due posts, resend missed previews, insights, Sunday report) |
| `pipeline.py` | calendar row -> write -> rule + fact check -> one auto-revision -> render -> overflow fix -> vision check -> approval -> publish; `weekly_report()` = top/weak posts + AI review (patterns, 5 experiments, ideas, missing topics), sent in <2,000-char parts and saved to `output/reports/` |
| `llm.py` | one `ask_json()` over providers: gemini (default, free), groq (fallback, free), claude, ollama, mock |
| `writer.py` | carousel/reel JSON schemas and prompts (`ABOUT` = audience profile, `HOOKS` = draft 5 hooks then pick, make it shareable); `full_caption()` |
| `checker.py` | `rules()`, `facts()`, `visual()`, `news_suitable()` |
| `research.py` | RSS feeds per news pillar (AI; cyber: Talos, BleepingComputer, The Hacker News, Krebs, SecurityWeek, Dark Reading, CISA), `rank()`, `top_story()` tries the top 5 until one passes `checker.news_suitable` (non-political, confirmed, has a defensive lesson) |
| `render_carousel.py` + `templates/carousel.html.j2` | Playwright renders 1080x1350 JPEG slides; JS shrinks text to fit and reports overflow |
| `render_reel.py` + `templates/reel.html.j2` | Reels: 1080x1920 scenes (Instagram safe zone); JS `seek(i, t)` drives entrance animations that are screenshotted frame by frame, + Kokoro TTS voice (offline, `REEL_VOICE`), ffmpeg holds each scene for its narration and cuts -> `reel.mp4` |
| `approval.py` | Facade for the approvals chat (`NOTIFY=discord|telegram|none`) + shared command logic `handle()` |
| `discord_bot.py` | Default. discord.py client on its own thread; auto-creates private #bytebaithak-approvals; buttons ok:/no:/pause handled in `on_interaction`; slash commands; owner-only |
| `telegram_bot.py` | Alternative, polling getUpdates |
| `storage.py` | Cloudflare R2 upload (Instagram needs public https image URLs) |
| `instagram.py` | container -> wait FINISHED -> media_publish; insights; token refresh |
| `db.py` | SQLite in `data/posts.db` |
| `dashboard.py` + `templates/dashboard.html.j2` | Local dashboard at http://localhost:8787 (host 8787 -> container 8080; host port 8080 is taken by another program). Posts, approve/reject/retry/pause (same `approval.handle()` as Discord, per-start token + Host check), Instagram insights cached every 30 min, course progress, calendar, health. Internet access: compose service `tunnel` (Cloudflare quick tunnel, random https://*.trycloudflare.com, new link posted to Discord); requires `DASHBOARD_USER` / `DASHBOARD_PASSWORD` (10+ chars) in `.env`, else only this PC gets in |

Post status flow: `drafting -> pending_approval -> approved -> published` (or `failed_check`, `rejected`,
`publish_failed`, `skipped`, `dry_run`, `manual` for reels).

## Brand

Navy `#0B1020`, cyan `#22D3EE`, violet `#8B5CF6`, amber `#F59E0B`, green `#22C55E`. Fonts: Space Grotesk
(text) and JetBrains Mono (code), bundled in `assets/fonts`. Handle `@byte_baithak`. Instagram API images
must be JPEG.

## Status and next stages

- Stage 1 (done, tested offline with mock AI and a fake Discord): carousel pipeline, checks, Discord approvals,
  dry-run publish. Not yet tested live: real Gemini/Groq calls, Discord login, R2, Instagram, the Docker image build.
- The owner chose Discord over Telegram for approvals.
- Live so far: Gemini/Groq, Discord, R2 and Instagram all pass `check`; one carousel was published on
  2026-11-12 on the owner's request. The Instagram token is a Facebook-type `EAA...` token, so `instagram.py`
  talks to graph.facebook.com (it picks the host from the token prefix).
- The owner's PC can switch off any time, so all calendar slots are before 17:00 (11:00 reel, 13:00, 15:30, 16:30 reel from 2026-11-20: "DevOps Interview N/30" series).
- Stage 2 (done 2026-11-12): Reels are generated like carousels from a JSON script (`writer.write_reel`,
  `checker.reel_rules`). Rows whose topic starts with `FACE REEL` stay manual (owner records them).
  No music: licensed music cannot be added via the API.
- Stage 3: go live (R2 bucket, Meta developer app, long-lived token, `DRY_RUN=false`), keep `APPROVAL_MODE=all`
  for the first 2-4 weeks, then `news_only`.
