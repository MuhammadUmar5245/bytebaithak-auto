"""Settings, read once from environment variables (.env via docker compose)."""
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def _bool(v, default=False):
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _list(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


BASE = Path(os.environ.get("APP_HOME", Path(__file__).resolve().parent.parent))


@dataclass
class Settings:
    handle: str = os.environ.get("HANDLE", "@byte_baithak")
    language: str = os.environ.get("CONTENT_LANGUAGE", "English")

    llm_provider: str = os.environ.get("LLM_PROVIDER", "gemini").lower()
    llm_fallback: str = os.environ.get("LLM_FALLBACK", "groq").lower()
    gemini_key: str = os.environ.get("GEMINI_API_KEY", "")
    gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    groq_key: str = os.environ.get("GROQ_API_KEY", "")
    groq_model: str = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    anthropic_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    claude_model: str = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://ollama:11434")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
    vision_check: bool = _bool(os.environ.get("VISION_CHECK"), True)

    # Reels: offline Kokoro voice. Owner picked bm_george (British male). Others: am_michael, am_adam,
    # am_eric (male), af_heart, af_bella, bf_emma (female) ...
    reel_voice: str = os.environ.get("REEL_VOICE", "bm_george").strip()
    reel_speed: float = float(os.environ.get("REEL_SPEED", "1.0"))
    models_dir: Path = Path(os.environ.get("MODELS_DIR", BASE / "models"))

    tz: str = os.environ.get("TZ", "Asia/Karachi")
    generate_at: str = os.environ.get("GENERATE_AT", "06:00")
    slots: list = field(default_factory=lambda: _list(os.environ.get("SLOTS", "13:00,18:00,21:00")))
    catchup_hours: float = float(os.environ.get("CATCHUP_HOURS", "3"))

    dry_run: bool = _bool(os.environ.get("DRY_RUN"), True)
    approval_mode: str = os.environ.get("APPROVAL_MODE", "all").lower()

    notify_setting: str = os.environ.get("NOTIFY", "").strip().lower()
    discord_token: str = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    discord_channel: str = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    discord_owner: str = os.environ.get("DISCORD_OWNER_ID", "").strip()
    tg_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat: str = os.environ.get("TELEGRAM_CHAT_ID", "")

    r2_account: str = os.environ.get("R2_ACCOUNT_ID", "")
    r2_key: str = os.environ.get("R2_ACCESS_KEY_ID", "")
    r2_secret: str = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    r2_bucket: str = os.environ.get("R2_BUCKET", "bytebaithak-media")
    r2_public: str = os.environ.get("R2_PUBLIC_BASE_URL", "").rstrip("/")

    ig_user: str = os.environ.get("IG_USER_ID", "")
    ig_token: str = os.environ.get("IG_ACCESS_TOKEN", "")
    ig_version: str = os.environ.get("IG_API_VERSION", "").strip()

    # Dashboard login. Without a password (10+ characters) the dashboard only opens on this PC.
    dashboard_user: str = os.environ.get("DASHBOARD_USER", "admin").strip()
    dashboard_password: str = os.environ.get("DASHBOARD_PASSWORD", "")
    tunnel_url: str = os.environ.get("TUNNEL_METRICS", "http://tunnel:2000").rstrip("/")

    data_dir: Path = BASE / "data"
    output_dir: Path = BASE / "output"
    assets_dir: Path = BASE / "assets"

    def __post_init__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cal = self.data_dir / "calendar.csv"
        default = BASE / "defaults" / "calendar.csv"
        if not cal.exists() and default.exists():
            cal.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")

    @property
    def telegram_on(self):
        return bool(self.tg_token and self.tg_chat)

    @property
    def notify(self):
        """discord | telegram | none. NOTIFY wins; otherwise whichever bot has a token."""
        if self.notify_setting in ("discord", "telegram", "none"):
            return self.notify_setting
        if self.discord_token:
            return "discord"
        if self.tg_token:
            return "telegram"
        return "none"


S = Settings()


def now():
    """Current wall-clock time in the account's time zone (naive, for simple comparisons)."""
    return datetime.now(ZoneInfo(S.tz)).replace(tzinfo=None)


def today():
    return now().date()

# Topics that must always be approved by a human unless APPROVAL_MODE=none
SENSITIVE_PILLARS = {"ai_news", "cyber_news", "super_ai"}
NEWS_PILLARS = {"ai_news", "cyber_news"}   # AUTO_NEWS rows; facts only from the fetched article
PILLARS = {
    "ai_news": ("AI NEWS", "#22D3EE"),
    "cyber_news": ("CYBER NEWS", "#F43F5E"),
    "super_ai": ("SUPER AI", "#F59E0B"),
    "ml": ("MACHINE LEARNING", "#A78BFA"),
    "devops": ("DEVOPS", "#22C55E"),
    "cloud": ("CLOUD", "#38BDF8"),
    "code": ("CODE", "#F472B6"),
}
