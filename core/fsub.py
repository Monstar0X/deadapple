"""Force-subscribe: user must be a member of every configured channel."""
import logging
import time
from typing import Dict, List, Tuple

from config import Config

log = logging.getLogger("fsub")


def _norm(chat):
    chat = str(chat).strip()
    if chat.lstrip("-").isdigit():
        return int(chat)
    return chat.lstrip("@").replace("https://t.me/", "")


class ForceSub:
    def __init__(self, app, db):
        self.app, self.db = app, db
        self._ok: Dict[int, float] = {}
        self._links: Dict[str, Tuple[str, str]] = {}

    async def channels(self) -> List[str]:
        return list(dict.fromkeys([*Config.FORCE_SUB, *await self.db.list_fsub()]))

    def forget(self, uid: int):
        self._ok.pop(uid, None)

    async def _link(self, chat) -> Tuple[str, str]:
        key = str(chat)
        if key in self._links:
            return self._links[key]
        c = await self.app.get_chat(_norm(chat))
        if c.username:
            link = f"https://t.me/{c.username}"
        else:
            link = c.invite_link or await self.app.export_chat_invite_link(c.id)
        self._links[key] = (c.title or "Channel", link)
        return self._links[key]

    async def missing(self, uid: int) -> List[Tuple[str, str]]:
        """[(title, join_url), …] for channels the user has not joined."""
        from pyrogram.enums import ChatMemberStatus
        from pyrogram.errors import UserNotParticipant
        if time.time() - self._ok.get(uid, 0) < 300:
            return []
        out = []
        for chat in await self.channels():
            try:
                m = await self.app.get_chat_member(_norm(chat), uid)
                if m.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                    raise UserNotParticipant()
            except UserNotParticipant:
                try:
                    out.append(await self._link(chat))
                except Exception as e:
                    log.warning("fsub: cannot build link for %s: %s", chat, e)
            except Exception as e:  # bot not admin / bad channel -> don't lock everyone out
                log.warning("fsub: skipping %s (%s: %s)", chat, type(e).__name__, e)
        if not out:
            self._ok[uid] = time.time()
        return out
