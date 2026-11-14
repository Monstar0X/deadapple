"""All configuration comes from environment variables (or a .env file)."""
import os

try:  # optional – only used for local development
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str) -> list:
    return [x.strip() for x in os.getenv(name, "").replace("\n", ",").split(",") if x.strip()]


class Config:
    # ── Telegram ──────────────────────────────────────────────
    API_ID = _int("API_ID", 0)
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    OWNER_ID = _int("OWNER_ID", 0)
    ADMINS = {int(x) for x in _list("ADMINS") if x.lstrip("-").isdigit()}

    # ── Database (MongoDB) ────────────────────────────────────
    DB_URL = os.getenv("DB_URL", "")
    DB_NAME = os.getenv("DB_NAME", "MangaBot")

    # ── Limits ────────────────────────────────────────────────
    # Max chapters a normal user can request in ONE batch.
    MAX_BATCH = max(1, _int("MAX_BATCH", 5))
    # Max chapters per batch for Subscribers. 0 = unlimited ("all").
    SUBSCRIBER_MAX_BATCH = _int("SUBSCRIBER_MAX_BATCH", 20)
    # Monthly chapter quota for normal users. Only enforced while the
    # feature is switched ON (admin: /monthly on). Default: OFF.
    MONTHLY_LIMIT = max(1, _int("MONTHLY_LIMIT", 500))
    MONTHLY_LIMIT_DEFAULT_ON = _bool("MONTHLY_LIMIT_ENABLED", False)

    # ── Force-sub (extra channels can be added at runtime with /addfsub) ──
    FORCE_SUB = _list("FORCE_SUB")

    # ── Content ───────────────────────────────────────────────
    DEFAULT_FORMAT = os.getenv("DEFAULT_FORMAT", "pdf").lower()  # pdf | cbz | both

    # ── Performance ───────────────────────────────────────────
    WORKERS = max(1, _int("WORKERS", 3))                  # chapters processed in parallel
    PAGE_CONCURRENCY = max(1, _int("PAGE_CONCURRENCY", 8))  # images downloaded in parallel
    SEARCH_TIMEOUT = _int("SEARCH_TIMEOUT", 20)
    HEALTH_INTERVAL = _int("HEALTH_INTERVAL", 1800)        # seconds between site checks

    # ── Scraper identity ──────────────────────────────────────
    CRAWLER_CONTACT = os.getenv("CRAWLER_CONTACT", "").strip()  # optional email/contact shown in HTTP User-Agent

    # ── Misc ──────────────────────────────────────────────────
    PICS = _list("PICS")                       # banner images (URLs), picked at random
    UPDATES_URL = os.getenv("UPDATES_URL", "")  # shown as a button on /start
    PORT = _int("PORT", 8080)
    SITES_FILE = os.getenv("SITES_FILE", "sites.json")
    DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
    DATA_DIR = os.getenv("DATA_DIR", "data")

    @classmethod
    def validate(cls):
        missing = [k for k in ("API_ID", "API_HASH", "BOT_TOKEN", "OWNER_ID", "DB_URL")
                   if not getattr(cls, k)]
        if missing:
            raise SystemExit("Missing required environment variables: " + ", ".join(missing))
