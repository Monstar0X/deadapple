"""Who may do what: roles, batch limits, monthly quota."""
from dataclasses import dataclass
from typing import Optional, Set, Tuple

from config import Config


@dataclass
class Role:
    name: str                 # owner | admin | subscriber | user
    batch_limit: Optional[int]  # None = unlimited

    @property
    def privileged(self) -> bool:
        return self.name in ("owner", "admin", "subscriber")


class Access:
    def __init__(self, db):
        self.db = db
        self.admin_ids: Set[int] = set(Config.ADMINS)

    async def load(self):
        self.admin_ids |= set(await self.db.list_admins())

    def is_owner(self, uid: int) -> bool:
        return uid == Config.OWNER_ID

    def is_admin(self, uid: int) -> bool:
        return self.is_owner(uid) or uid in self.admin_ids

    async def add_admin(self, uid: int):
        self.admin_ids.add(uid)
        await self.db.add_admin(uid)

    async def remove_admin(self, uid: int):
        self.admin_ids.discard(uid)
        await self.db.remove_admin(uid)

    async def role(self, uid: int) -> Role:
        if self.is_admin(uid):
            return Role("owner" if self.is_owner(uid) else "admin", None)
        if await self.db.get_sub(uid):
            lim = Config.SUBSCRIBER_MAX_BATCH
            return Role("subscriber", None if lim <= 0 else max(lim, Config.MAX_BATCH))
        return Role("user", Config.MAX_BATCH)

    # ── monthly quota (feature flag lives in the DB, default OFF) ──
    async def monthly_enabled(self) -> bool:
        return bool(await self.db.get_cfg("monthly_enabled", Config.MONTHLY_LIMIT_DEFAULT_ON))

    async def monthly_limit(self) -> int:
        return int(await self.db.get_cfg("monthly_limit", Config.MONTHLY_LIMIT))

    async def monthly_remaining(self, uid: int, role: Optional[Role] = None) -> Optional[int]:
        """None = no quota applies."""
        role = role or await self.role(uid)
        if role.privileged or not await self.monthly_enabled():
            return None
        return max(0, await self.monthly_limit() - await self.db.get_usage(uid))

    async def check_request(self, uid: int, n: int) -> Tuple[bool, str]:
        """Can this user queue `n` chapters right now?"""
        role = await self.role(uid)
        if role.batch_limit is not None and n > role.batch_limit:
            hint = ""
            if role.name == "user":
                hint = "\n\n⭐ Subscribers can batch more. Ask the admin if you need it."
            return False, (f"❌ You can download up to <b>{role.batch_limit}</b> chapters per batch "
                           f"(you asked for {n}).{hint}")
        remaining = await self.monthly_remaining(uid, role)
        if remaining is not None and n > remaining:
            return False, (f"❌ Monthly limit reached. You have <b>{remaining}</b> chapter(s) left this month "
                           f"(limit {await self.monthly_limit()}). It resets on the 1st.")
        return True, ""
