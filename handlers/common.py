"""Ban check + force-sub gate used at the start of every handler."""
import logging

from pyrogram import filters
from pyrogram.types import CallbackQuery

from ctx import access, app, db, fsub
from handlers.ui import Btn, kb, safe_answer

log = logging.getLogger("gate")
_known: set = set()


def admin_filter_fn(_, __, m):
    return bool(getattr(m, "from_user", None) and access.is_admin(m.from_user.id))


def owner_filter_fn(_, __, m):
    return bool(getattr(m, "from_user", None) and access.is_owner(m.from_user.id))


admin_only = filters.create(admin_filter_fn)
owner_only = filters.create(owner_filter_fn)


async def join_prompt(client, chat_id: int, missing):
    rows = [[Btn(f"📢 Join {title}", url=url)] for title, url in missing]
    rows.append([Btn("✅ I've joined", callback_data="fs|check")])
    await client.send_message(
        chat_id, "🔒 <b>Please join our channel(s) to use this bot.</b>\n\n"
                 "Join below, then tap <b>I've joined</b>.", reply_markup=kb(rows))


async def gate(client, obj) -> bool:
    """True when the user may proceed. Sends the right message otherwise."""
    user = obj.from_user
    if not user:
        return False
    is_cb = isinstance(obj, CallbackQuery)
    chat_id = obj.message.chat.id if is_cb else obj.chat.id
    if user.id not in _known:
        await db.add_user(user)
        _known.add(user.id)
    if await db.is_banned(user.id):
        if is_cb:
            await safe_answer(obj, "🚫 You are banned from using this bot.", True)
        else:
            await obj.reply("🚫 <b>You are banned from using this bot.</b>")
        return False
    if access.is_admin(user.id):
        return True
    missing = await fsub.missing(user.id)
    if missing:
        if is_cb:
            await safe_answer(obj, "Join the channel(s) first.", True)
        await join_prompt(client, chat_id, missing)
        return False
    return True


@app.on_callback_query(filters.regex(r"^fs\|check$"))
async def fsub_recheck(client, cb: CallbackQuery):
    fsub.forget(cb.from_user.id)
    missing = await fsub.missing(cb.from_user.id)
    if missing:
        return await safe_answer(cb, "You haven't joined all channels yet.", True)
    await safe_answer(cb, "Thanks! ✅")
    try:
        await cb.message.edit_text("✅ <b>Thanks for joining!</b>\n\nSend me the name of a manga to search.")
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^noop$"))
async def noop(client, cb):
    await safe_answer(cb)
