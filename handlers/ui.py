"""Small Telegram UI helpers shared by all handlers."""
import asyncio
import random
from io import BytesIO
from typing import Optional

from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup

from config import Config

CAPTION_LIMIT = 1000


def random_pic() -> Optional[str]:
    return random.choice(Config.PICS) if Config.PICS else None


def kb(rows) -> Markup:
    return Markup([[b for b in row if b] for row in rows if any(row)])


async def edit_card(msg, text: str, markup=None):
    """Edit a message whether it is a photo (caption) or plain text."""
    try:
        if msg.photo or msg.video or msg.document or msg.animation:
            await msg.edit_caption(text[:1024], reply_markup=markup)
        else:
            await msg.edit_text(text[:4096], reply_markup=markup, disable_web_page_preview=True)
    except MessageNotModified:
        pass
    except FloodWait as e:
        await asyncio.sleep(min(e.value, 5))


async def send_card(client, chat_id: int, text: str, markup=None, photo=None, reply_to=None):
    """Send photo+caption when a picture is available, else plain text."""
    photo = photo or random_pic()
    if photo:
        try:
            return await client.send_photo(chat_id, photo, caption=text[:1024], reply_markup=markup,
                                           reply_to_message_id=reply_to)
        except Exception:
            pass
    return await client.send_message(chat_id, text[:4096], reply_markup=markup,
                                     reply_to_message_id=reply_to, disable_web_page_preview=True)


def _to_jpeg(raw: bytes) -> Optional[bytes]:
    from PIL import Image
    im = Image.open(BytesIO(raw)).convert("RGB")
    im.thumbnail((900, 1300))
    out = BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()


async def cover_photo(http, source, url: Optional[str]) -> Optional[BytesIO]:
    """Download a cover (with the site's referer) and return it as an uploadable JPEG."""
    if not url:
        return None
    try:
        raw = await http.fetch_image(url, {"Referer": source.base + "/"}, retries=2)
        data = await asyncio.get_running_loop().run_in_executor(None, _to_jpeg, raw)
        bio = BytesIO(data)
        bio.name = "cover.jpg"
        return bio
    except Exception:
        return None


async def safe_answer(cb, text: str = "", alert: bool = False):
    try:
        await cb.answer(text, show_alert=alert)
    except Exception:
        pass
