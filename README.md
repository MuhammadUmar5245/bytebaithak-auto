# ByteBaithak Auto-Poster (Stage 1)

Yeh system roz khud:

1. `data/calendar.csv` se aaj ke topics leta hai (AI news ke liye khud research karta hai)
2. AI (Gemini free, backup Groq free) se slides, caption aur hashtags likhwata hai
3. **Check 1:** rules (lambai, hashtags, links, hype words, duplicate topic)
4. **Check 2:** alag AI call har claim aur command ko check karti hai; news sirf source se match hone par pass hoti hai
5. Brand design mein 1080×1350 slides banata hai; text khud fit hota hai, kata hua text pakar leta hai
6. **Check 3:** AI tayyar slides ki images dekh kar galtiyan dhoondti hai
7. Discord par preview bhejta hai (✅ Approve / ❌ Reject buttons)
8. Waqt par Instagram ki official API se post karta hai (abhi `DRY_RUN=true`, yani post nahi hogi)
9. 24 aur 72 ghante baad numbers note karta hai; har Sunday report aur naye topic ideas bhejta hai

Ghalti ho to post **ruk jaati hai** aur Discord par wajah aati hai. Kuch bhi bina check ke post nahi hota.
Reels abhi Stage 2 mein hain; calendar mein `reel` wali rows ka sirf reminder aata hai.

---

## 1. Ek dafa ka setup (Windows)

1. **Docker Desktop** install karein: https://www.docker.com/products/docker-desktop/ (WSL2 option on rakhein).
   - Docker Desktop → Settings → General → **"Start Docker Desktop when you sign in"** on.
2. **PC sleep band:** Windows Settings → System → Power → Screen and sleep → "When plugged in, put my device to sleep" = **Never**.
3. **UPS** lagayein (loadshedding). PC dobara on hoga to system khud chooti post chala dega (`CATCHUP_HOURS`).
4. Yeh folder `C:\bytebaithak-auto` mein unzip karein.
5. `.env.example` ko copy karke **`.env`** naam se save karein (Notepad mein kholein).

## 2. Free AI keys

| Key | Kahan se | `.env` mein |
| --- | --- | --- |
| Gemini | https://aistudio.google.com → Get API key | `GEMINI_API_KEY=` |
| Groq (backup) | https://console.groq.com → API Keys | `GROQ_API_KEY=` |

Keys sirf `.env` mein rakhein. Kisi ko na bhejein, chat mein bhi paste na karein.

## 3. Pehla test (kuch post nahi hoga)

Folder mein PowerShell kholein (folder mein Shift + Right-click → "Open PowerShell window here"):

```powershell
docker compose build
docker compose run --rm worker python -m app.main check
docker compose run --rm worker python -m app.main preview --topic "What is Docker?" --pillar devops
```

- Pehla `build` 5–10 minute lega (Chromium download hota hai).
- `check` batayega kaun si setting theek hai (Discord, R2, Instagram abhi FAIL dikhayenge, yeh normal hai).
- `preview` ke baad `output\` folder mein slides (`slide-01.jpg` ...), `caption.txt` aur `report.json` (saare checks ka result) dekhein.

Pillars: `ai_news`, `super_ai`, `ml`, `devops`, `cloud`, `code`.

## 4. Discord approval bot (10 minute)

1. **Apna server:** Discord mein left side **+** → **Create My Own** → **For me and my friends** → naam `ByteBaithak HQ`.
2. **Bot banayein:** browser mein `discord.com/developers/applications` → **New Application** → naam `ByteBaithak Bot` → Create.
3. Left menu **Bot** → **Reset Token** → **Yes, do it!** → token copy karein → `.env` mein:
   ```
   DISCORD_BOT_TOKEN=yahan-token
   ```
   (Token sirf ek dafa dikhta hai. "Privileged Gateway Intents" on karne ki zaroorat nahi.)
4. **Invite link nikalein:**
   ```powershell
   docker compose run --rm worker python -m app.main discord-setup
   ```
   Jo link aaye, browser mein kholein → apna server `ByteBaithak HQ` chunein → **Authorize**.
5. **Test:**
   ```powershell
   docker compose run --rm worker python -m app.main check
   ```
   Bot khud ek private channel **#bytebaithak-approvals** banayega aur wahan test message bhejega.

Har post ki slides is channel mein aayengi, saath mein **✅ Approve / ❌ Reject / ⏸ Pause** buttons. Sirf server owner (aap) buttons daba sakta hai.
Slash commands (channel mein `/` likhein): `/status`, `/pause`, `/resume`, `/approve`, `/reject`, `/retry`.
Agar preview ke waqt Discord band ho, bot har 5 minute baad dobara bhejta hai.

**Telegram pasand ho to:** `.env` mein `NOTIFY=telegram`, `TELEGRAM_BOT_TOKEN` aur `TELEGRAM_CHAT_ID` (chat id: `... app.main telegram-chat-id`).

## 5. System chalana

```powershell
docker compose up -d          # start (PC restart ke baad khud chalega)
docker compose logs -f        # live logs dekhna (Ctrl+C se bahar)
docker compose down           # band karna
```

Roz `GENERATE_AT` (06:00) par posts banti hain, Discord par preview aata hai, aur approve hone par `SLOTS` (1 PM, 6 PM, 9 PM) par post hoti hain.

## 6. Content calendar

`data/calendar.csv` Excel ya Notepad mein edit karein:

```
date,slot,pillar,format,topic
2026-11-11,13:00,cloud,carousel,"IaaS vs PaaS vs SaaS"
2026-11-11,21:00,ai_news,carousel,AUTO_NEWS
```

`AUTO_NEWS` = system khud us din ki top AI story dhoondega (OpenAI, Google, DeepMind, Hugging Face, Microsoft, NVIDIA, TechCrunch, The Verge, Hacker News). Agar koi achhi story ya source text na mile to post nahi banegi.

## 7. Live posting (Stage 3, saath mil kar karenge)

Jab tak `DRY_RUN=true` hai, Instagram par kuch nahi jayega. Live karne ke liye:

1. **Cloudflare R2:** free account → R2 bucket `bytebaithak-media` → "Public access" on → API token → `.env` ki R2 values.
2. **Meta developer app:** developers.facebook.com → app → "Instagram API with Instagram Login" → @byte_baithak connect → long-lived token aur IG user id → `.env`.
3. `docker compose run --rm worker python -m app.main check` → sab OK.
4. `.env` mein `DRY_RUN=false`, phir `docker compose up -d`.

Pehle 2–4 hafte `APPROVAL_MODE=all` rakhein. Bharosa ho jaye to `news_only` (sirf AI news aur Super AI par approval).

## 8. Agar AI quality kam lage

`.env` mein ek line badlein:

- `LLM_PROVIDER=claude` + `ANTHROPIC_API_KEY` (paid, behtar likhai)
- `LLM_PROVIDER=ollama` (PC par free local model): `docker compose --profile local-llm up -d`, phir `docker exec -it bytebaithak-ollama ollama pull qwen2.5:14b`

## 9. Masle

| Masla | Hal |
| --- | --- |
| `check` mein AI FAIL | Key ghalat ya free limit khatam; `LLM_FALLBACK=groq` rakhein |
| Post "failed_check" | Discord par wajah aati hai; `output\...\report.json` dekhein |
| Discord check FAIL | `discord-setup` chala kar invite link dobara kholein; token ghalat ho to portal se Reset Token |
| "Skipped (waqt nikal gaya)" | PC band tha `CATCHUP_HOURS` se zyada; calendar mein naya din daal dein |
| Kuch samajh na aaye | `docker compose logs --tail 100` ka output Claude ko bhejein (`.env` nahi) |

Offline tests (koi key nahi chahiye):
`docker compose run --rm -e LLM_PROVIDER=mock -e LLM_FALLBACK=mock worker python -m tests.test_offline`
`docker compose run --rm -e LLM_PROVIDER=mock worker python -m tests.test_discord`

Fonts: Space Grotesk aur JetBrains Mono, SIL Open Font License (`assets/fonts/`).
