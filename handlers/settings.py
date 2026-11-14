from pyrogram import filters

from ctx import access, app, awaiting, db
from handlers.common import gate
from handlers.ui import Btn, edit_card, kb, safe_answer

FORMATS = {"pdf": "📄 PDF", "cbz": "🗜 CBZ", "both": "📄 + 🗜 Both"}


async def settings_view(uid: int):
    cur = (await db.get_user(uid)).get("format", "pdf")
    rows = [[Btn(("✅ " if k == cur else "") + label, callback_data=f"st|fmt|{k}") for k, label in FORMATS.items()]]
    if access.is_admin(uid):
        rows.extend([
            [Btn("📝 Caption", callback_data="st|admin|caption"),
             Btn("📄 File format", callback_data="st|admin|format")],
            [Btn("🖼 Thumbnail", callback_data="st|admin|thumb"),
             Btn("✍️ Watermark", callback_data="st|admin|wm")],
            [Btn("📊 Stats", callback_data="st|admin|stats")],
        ])
    rows.append([Btn("⬅ Back", callback_data="nav|home"), Btn("🔴 CLOSE", callback_data="st|x")])
    text = ("<b>⚙️ Settings</b>\n\n<blockquote>Choose how chapters are delivered.\n"
            "• <b>PDF</b> – best for reading in Telegram\n"
            "• <b>CBZ</b> – for comic reader apps\n"
            "• <b>Both</b> – one of each</blockquote>")
    if access.is_admin(uid):
        text += "\n\n<b>🛠 Admin branding</b>\nUse the buttons below to change the global caption, file name format, thumbnail and watermark."
    return text, kb(rows)


@app.on_message(filters.command("settings") & filters.private)
async def settings_cmd(client, m):
    if await gate(client, m):
        text, markup = await settings_view(m.from_user.id)
        await m.reply(text, reply_markup=markup)


@app.on_callback_query(filters.regex(r"^st\|"))
async def settings_cb(client, cb):
    if not await gate(client, cb):
        return
    parts = cb.data.split("|")
    if parts[1] == "x":
        await cb.message.delete()
        return
    if parts[1] == "admin":
        if not access.is_admin(cb.from_user.id):
            return await safe_answer(cb, "Admins only.", True)
        kind = parts[2]
        if kind == "stats":
            from handlers.admin import stats_text
            await safe_answer(cb)
            return await cb.message.reply(await stats_text())
        prompts = {
            "caption": ("caption", "✍️ Send the new caption template. Variables: <code>{manga}</code> <code>{chapter}</code> <code>{source}</code> <code>{pages}</code>\n/cancel to stop."),
            "format": ("format", "📄 Send the file-name format. Variables: <code>{manga}</code> <code>{chapter}</code> <code>{source}</code>\n/cancel to stop."),
            "wm": ("wm", "✍️ Send watermark settings in this format:\n<code>text | position | #color | opacity | size%</code>\nExample: <code>@MyChannel | bottom-right | #ffffff | 140 | 3</code>\n/cancel to stop."),
            "thumb": ("thumb", "🖼 Send the photo in this chat to save it as the custom thumbnail.\n/cancel to stop."),
        }
        if kind not in prompts:
            return await safe_answer(cb, "Unknown setting.", True)
        pending_kind, prompt = prompts[kind]
        awaiting[cb.from_user.id] = {"kind": f"admin_{pending_kind}", "ts": __import__("time").time()}
        await safe_answer(cb)
        return await cb.message.reply(prompt)
    if parts[1] == "fmt" and parts[2] in FORMATS:
        await db.set_user(cb.from_user.id, format=parts[2])
        text, markup = await settings_view(cb.from_user.id)
        await edit_card(cb.message, text, markup)
        await safe_answer(cb, "Saved ✅")
