"""Shared singletons (imported by every handler module)."""
from pathlib import Path

from pyrogram import Client, enums

from config import Config
from core.access import Access
from core.database import Database
from core.fsub import ForceSub
from core.http import Http
from core.jobs import JobManager
from core.store import SessionStore
from sources.registry import Registry

Config.validate()

# Heroku does not preserve empty directories from Git deployments. Pyrogram
# must have an existing, writable workdir before it opens its SQLite session.
DATA_DIR = Path(Config.DATA_DIR).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = Client("mangabot", api_id=Config.API_ID, api_hash=Config.API_HASH, bot_token=Config.BOT_TOKEN,
             workdir=str(DATA_DIR), parse_mode=enums.ParseMode.HTML, max_concurrent_transmissions=4)
http = Http()
db = Database(Config.DB_URL, Config.DB_NAME)
access = Access(db)
registry = Registry(http, Config.SITES_FILE)
jobs = JobManager(app, db, http)
fsub = ForceSub(app, db)

sessions = SessionStore()      # search / manga sessions (short tokens for callback_data)
awaiting: dict = {}            # user_id -> pending text input {"kind","token","ts"}

