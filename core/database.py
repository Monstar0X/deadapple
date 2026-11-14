"""MongoDB (motor) storage."""
import time
from datetime import datetime, timedelta
from typing import List, Optional

from config import Config


def month_key() -> str:
    return datetime.utcnow().strftime("%Y-%m")


class Database:
    def __init__(self, url: str, name: str):
        from motor.motor_asyncio import AsyncIOMotorClient
        self.client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=15000)
        db = self.client[name]
        self.users = db["users"]
        self.admins = db["admins"]
        self.subs = db["subscribers"]
        self.fsub = db["fsub"]
        self.cfg = db["config"]
        self.usage = db["usage"]
        self.files = db["files"]

    async def init(self):
        await self.client.admin.command("ping")
        await self.files.create_index("created")

    # ── users ─────────────────────────────────────────────────
    async def add_user(self, user) -> bool:
        """Returns True if the user is new."""
        res = await self.users.update_one(
            {"_id": user.id},
            {"$set": {"name": user.first_name or "", "username": user.username or ""},
             "$setOnInsert": {"joined": time.time(), "banned": False,
                              "format": Config.DEFAULT_FORMAT}},
            upsert=True)
        return res.upserted_id is not None

    async def get_user(self, uid: int) -> dict:
        return await self.users.find_one({"_id": uid}) or {}

    async def set_user(self, uid: int, **fields):
        await self.users.update_one({"_id": uid}, {"$set": fields}, upsert=True)

    async def is_banned(self, uid: int) -> bool:
        u = await self.users.find_one({"_id": uid}, {"banned": 1})
        return bool(u and u.get("banned"))

    async def all_user_ids(self) -> List[int]:
        return [u["_id"] async for u in self.users.find({}, {"_id": 1})]

    async def count_users(self) -> int:
        return await self.users.count_documents({})

    async def delete_user(self, uid: int):
        await self.users.delete_one({"_id": uid})

    # ── admins ────────────────────────────────────────────────
    async def list_admins(self) -> List[int]:
        return [a["_id"] async for a in self.admins.find({})]

    async def add_admin(self, uid: int):
        await self.admins.update_one({"_id": uid}, {"$set": {"added": time.time()}}, upsert=True)

    async def remove_admin(self, uid: int):
        await self.admins.delete_one({"_id": uid})

    # ── subscribers (granted manually by owner/admins) ────────
    async def add_sub(self, uid: int, days: int, by: int):
        expires = time.time() + days * 86400 if days > 0 else None
        await self.subs.update_one(
            {"_id": uid}, {"$set": {"expires": expires, "by": by, "added": time.time()}}, upsert=True)
        return expires

    async def remove_sub(self, uid: int) -> bool:
        res = await self.subs.delete_one({"_id": uid})
        return res.deleted_count > 0

    async def get_sub(self, uid: int) -> Optional[dict]:
        doc = await self.subs.find_one({"_id": uid})
        if not doc:
            return None
        if doc.get("expires") and doc["expires"] < time.time():
            await self.subs.delete_one({"_id": uid})
            return None
        return doc

    async def list_subs(self) -> List[dict]:
        return [s async for s in self.subs.find({})]

    # ── force-sub channels ────────────────────────────────────
    async def list_fsub(self) -> List[str]:
        return [c["_id"] async for c in self.fsub.find({})]

    async def add_fsub(self, chat: str):
        await self.fsub.update_one({"_id": str(chat)}, {"$set": {"added": time.time()}}, upsert=True)

    async def remove_fsub(self, chat: str) -> bool:
        res = await self.fsub.delete_one({"_id": str(chat)})
        return res.deleted_count > 0

    # ── key/value config ──────────────────────────────────────
    async def get_cfg(self, key: str, default=None):
        doc = await self.cfg.find_one({"_id": key})
        return doc["value"] if doc and "value" in doc else default

    async def set_cfg(self, key: str, value):
        if value is None:
            await self.cfg.delete_one({"_id": key})
        else:
            await self.cfg.update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)

    # ── monthly usage ─────────────────────────────────────────
    async def get_usage(self, uid: int) -> int:
        doc = await self.usage.find_one({"_id": uid})
        return doc["count"] if doc and doc.get("month") == month_key() else 0

    async def add_usage(self, uid: int, n: int = 1):
        m = month_key()
        res = await self.usage.update_one({"_id": uid, "month": m}, {"$inc": {"count": n}})
        if res.matched_count == 0:
            await self.usage.replace_one({"_id": uid}, {"_id": uid, "month": m, "count": n}, upsert=True)

    # ── uploaded-file cache (Telegram file_ids) ───────────────
    async def get_file(self, key: str) -> Optional[str]:
        doc = await self.files.find_one({"_id": key})
        return doc["file_id"] if doc else None

    async def save_file(self, key: str, file_id: str):
        await self.files.update_one({"_id": key}, {"$set": {"file_id": file_id, "created": time.time()}},
                                    upsert=True)

    async def drop_file(self, key: str):
        await self.files.delete_one({"_id": key})

    async def clear_files(self) -> int:
        res = await self.files.delete_many({})
        return res.deleted_count

    async def count_files(self) -> int:
        return await self.files.count_documents({})
