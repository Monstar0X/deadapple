import time
from datetime import datetime

from pyrogram import filters

from config import Config
from ctx import access, app, awaiting, db, registry
from handlers.common import gate
from handlers.ui import Btn, edit_card, kb, safe_answer, send_card
from core.utils import esc


def home_markup():
    rows = [[Btn("📖 How to use", callback_data="nav|help"), Btn("📊 My plan", callback_data="nav|plan")]]
    if Config.UPDATES_URL:
        rows.append([Btn("⚙️ Settings", callback_data="nav|settings"),
                     Btn("📢 Updates", url=Config.UPDATES_URL)])
    else:
        rows.append([Btn("⚙️ Settings", callback_data="nav|settings")])
    return kb(rows)


def home_text(name: str) -> str:
    n = len(registry.sources)
    return (f"👋 <b>Hello {esc(name)}!</b>\n\n"
            f"<blockquote>Send me the name of any manga / manhwa and I'll search "
            f"<b>{n}</b> sites for you. Pick a result, then download a single chapter "
            f"or a whole batch as PDF / CBZ — right here in Telegram.</blockquote>\n"
            f"<i>Just type a title to begin ⬇️</i>")


async def help_text() -> str:
    sub = Config.SUBSCRIBER_MAX_BATCH
    sub_txt = "unlimited" if sub <= 0 else str(max(sub, Config.MAX_BATCH))
    return ("<b>📖 How to use</b>\n\n"
            "<blockquote>1️⃣ Send a manga name\n"
            "2️⃣ Choose a site (or ♞ ALL SEARCH)\n"
            "3️⃣ Pick a result\n"
            "4️⃣ Tap <b>Single chapter</b> or <b>Batch</b></blockquote>\n"
            f"📦 Batch limit: <b>{Config.MAX_BATCH}</b> chapters (Subscribers: <b>{sub_txt}</b>)\n"
            "🧾 Output format: /settings\n"
            "📊 Your limits: /plan\n"
            "🛑 Cancel a pending input: /cancel")


@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, m):
    if not await gate(client, m):
        return
    await send_card(client, m.chat.id, home_text(m.from_user.first_name), home_markup())


@app.on_message(filters.command("help") & filters.private)
async def help_cmd(client, m):
    if await gate(client, m):
        await m.reply(await help_text(), disable_web_page_preview=True)


@app.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, m):
    if awaiting.pop(m.from_user.id, None):
        await m.reply("🛑 Cancelled.")
    else:
        await m.reply("Nothing to cancel.")


async def plan_text(uid: int) -> str:
    role = await access.role(uid)
    sub = await db.get_sub(uid) if role.name == "subscriber" else None
    names = {"owner": "👑 Owner", "admin": "🛡 Admin", "subscriber": "⭐ Subscriber", "user": "👤 Free user"}
    lines = [f"<b>📊 Your plan</b>\n", f"Status: <b>{names[role.name]}</b>"]
    if sub and sub.get("expires"):
        lines.append(f"Expires: <b>{datetime.utcfromtimestamp(sub['expires']):%Y-%m-%d}</b>")
    lines.append("Batch limit: <b>" + ("unlimited" if role.batch_limit is None else f"{role.batch_limit} chapters") + "</b>")
    remaining = await access.monthly_remaining(uid, role)
    if await access.monthly_enabled() and remaining is not None:
        lines.append(f"Monthly chapters left: <b>{remaining}</b> / {await access.monthly_limit()}")
    else:
        lines.append("Monthly chapters: <b>unlimited</b>")
    return "\n".join(lines)


@app.on_message(filters.command(["plan", "me"]) & filters.private)
async def plan_cmd(client, m):
    if await gate(client, m):
        await m.reply(await plan_text(m.from_user.id))


@app.on_callback_query(filters.regex(r"^nav\|"))
async def nav_cb(client, cb):
    if not await gate(client, cb):
        return
    where = cb.data.split("|")[1]
    back = kb([[Btn("⬅ Back", callback_data="nav|home")]])
    if where == "help":
        await edit_card(cb.message, await help_text(), back)
    elif where == "plan":
        await edit_card(cb.message, await plan_text(cb.from_user.id), back)
    elif where == "settings":
        from handlers.settings import settings_view
        text, markup = await settings_view(cb.from_user.id)
        await edit_card(cb.message, text, markup)
    else:
        await edit_card(cb.message, home_text(cb.from_user.first_name), home_markup())
    await safe_answer(cb)
