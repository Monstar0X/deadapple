"""Owner / admin tools: subscribers, force-sub, limits, broadcast, bans, branding, site health."""
import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path

from pyrogram import filters
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked

from config import Config
from core.utils import esc, trunc
from ctx import access, app, awaiting, db, fsub, jobs, registry
from handlers.common import admin_only, owner_only
from handlers.ui import Btn, edit_card, kb, safe_answer

log = logging.getLogger("admin")
P = filters.private


def _args(m):
    return m.command[1:] if m.command else []


def _uid(m):
    a = _args(m)
    if a and a[0].lstrip("-").isdigit():
        return int(a[0])
    if m.reply_to_message and m.reply_to_message.from_user:
        return m.reply_to_message.from_user.id
    return None


# ── panel ─────────────────────────────────────────────────────
async def panel_view(uid: int):
    monthly = await access.monthly_enabled()
    sub_lim = "unlimited" if Config.SUBSCRIBER_MAX_BATCH <= 0 else Config.SUBSCRIBER_MAX_BATCH
    text = ("<b>🛠 Admin panel</b>\n<blockquote>"
            f"📦 Max batch (users): <b>{Config.MAX_BATCH}</b> <i>(env MAX_BATCH)</i>\n"
            f"⭐ Max batch (subscribers): <b>{sub_lim}</b> <i>(env SUBSCRIBER_MAX_BATCH)</i>\n"
            f"📅 Monthly limit: <b>{'ON · ' + str(await access.monthly_limit()) + '/month' if monthly else 'OFF'}</b>\n"
            f"</blockquote>\n"
            "<i>Commands: /help_admin</i>")
    rows = [[Btn(f"📅 Monthly limit: {'ON' if monthly else 'OFF'}", callback_data="ad|monthly")],
            [Btn("🧹 Clear cache", callback_data="ad|clearcache"), Btn("📊 Stats", callback_data="ad|stats")],
            [Btn("➕ Add force-sub", callback_data="ad|addfsub")]]
    if access.is_owner(uid):
        rows.append([Btn("🛡 Add admin", callback_data="ad|addadmin")])
    rows.extend([[Btn("🌐 Sites", callback_data="ad|sites")],
                 [Btn("🔴 CLOSE", callback_data="ad|x")]])
    return text, kb(rows)


@app.on_message(filters.command("admin") & P & admin_only)
async def admin_cmd(client, m):
    text, markup = await panel_view(m.from_user.id)
    await m.reply(text, reply_markup=markup)


async def stats_text() -> str:
    on = sum(1 for s in registry.sources.values() if s.online)
    return ("<b>📊 Stats</b>\n<blockquote>"
            f"👥 Users: <b>{await db.count_users()}</b>\n"
            f"⭐ Subscribers: <b>{len(await db.list_subs())}</b>\n"
            f"💾 Cached files: <b>{await db.count_files()}</b>\n"
            f"⏳ Queue: <b>{jobs.queue.qsize()}</b> waiting · <b>{len(jobs.by_id)}</b> total jobs\n"
            f"🌐 Sites online: <b>{on}/{len(registry.sources)}</b></blockquote>")


def sites_text() -> str:
    lines = ["<b>🌐 Site status</b>\n"]
    for k in registry.order:
        s = registry.sources[k]
        mark = "⚪" if s.online is None else ("🟢" if s.online else "🔴")
        extra = f" — <i>{esc(s.reason)}</i>" if s.online is False else ""
        lines.append(f"{mark} {esc(s.name)}{extra}")
    return trunc("\n".join(lines), 3900)


@app.on_callback_query(filters.regex(r"^ad\|") & admin_only)
async def admin_cb(client, cb):
    action = cb.data.split("|")[1]
    if action == "x":
        await safe_answer(cb)
        return await cb.message.delete()
    if action == "monthly":
        await db.set_cfg("monthly_enabled", not await access.monthly_enabled())
    elif action == "clearcache":
        n = await db.clear_files()
        await safe_answer(cb, f"Cleared {n} cached file(s).")
        text, markup = await panel_view(cb.from_user.id)
        return await edit_card(cb.message, text, markup)
    elif action in ("addadmin", "addfsub"):
        if action == "addadmin" and not access.is_owner(cb.from_user.id):
            return await safe_answer(cb, "Owner only.", True)
        kind = "admin_addadmin" if action == "addadmin" else "admin_addfsub"
        awaiting[cb.from_user.id] = {"kind": kind, "ts": time.time()}
        prompt = ("🛡 Send the user ID to promote to admin.\n/cancel to stop." if action == "addadmin"
                  else "📢 Send the force-sub channel username or ID (bot must be an admin there).\n/cancel to stop.")
        await safe_answer(cb)
        return await cb.message.reply(prompt)
    elif action == "stats":
        await safe_answer(cb)
        return await cb.message.reply(await stats_text())
    elif action == "sites":
        await safe_answer(cb)
        return await cb.message.reply(sites_text())
    text, markup = await panel_view(cb.from_user.id)
    await edit_card(cb.message, text, markup)
    await safe_answer(cb, "Updated ✅")


ADMIN_HELP = """<b>🛠 Admin commands</b>

<b>Subscribers</b>
/addsub <code>user_id [days]</code> – grant Subscriber (0/none = permanent)
/delsub <code>user_id</code> · /subs – list

<b>Limits</b>
/monthly <code>on|off|&lt;number&gt;</code> – monthly chapter quota (users only)

<b>Force-sub</b>
/addfsub <code>@channel or -100id</code> · /delfsub <code>…</code> · /fsub

<b>Users</b>
/ban <code>id [reason]</code> · /unban <code>id</code> · /broadcast (reply to a message) · /stats

<b>Branding</b>
/setcaption <code>text</code> – vars: {manga} {chapter} {source} {pages}
/setformat <code>text</code> – file name, vars: {manga} {chapter} {source}
/setwm <code>text | position | #color | opacity | size%</code> · /delwm
/setthumb (reply to a photo) · /delthumb · /resetbranding · /clearcache

<b>Sites</b>
/sites – status · /checksites – re-check now

<b>Owner only</b>
/addadmin <code>id</code> · /deladmin <code>id</code> · /admins"""


@app.on_message(filters.command(["help_admin", "adminhelp"]) & P & admin_only)
async def admin_help(client, m):
    await m.reply(ADMIN_HELP, disable_web_page_preview=True)


@app.on_message(filters.command("stats") & P & admin_only)
async def stats_cmd(client, m):
    await m.reply(await stats_text())


async def handle_admin_input(client, m, pending: dict):
    """Complete an admin-panel button flow using the next text message."""
    kind = pending.get("kind")
    value = (m.text or "").strip()
    if kind == "admin_addadmin":
        if not access.is_owner(m.from_user.id):
            awaiting.pop(m.from_user.id, None)
            return await m.reply("Owner only.")
        if not value.lstrip("-").isdigit():
            return await m.reply("Send a numeric Telegram user ID, or /cancel.")
        uid = int(value)
        await access.add_admin(uid)
        awaiting.pop(m.from_user.id, None)
        return await m.reply(f"✅ <code>{uid}</code> is now an admin.")
    if kind == "admin_addfsub":
        try:
            c = await client.get_chat(int(value) if value.lstrip("-").isdigit() else value.lstrip("@"))
            await client.get_chat_member(c.id, "me")
        except Exception as e:
            return await m.reply(f"❌ Couldn't use that channel: <code>{esc(type(e).__name__)}</code>. Make sure the bot is an admin there.")
        await db.add_fsub(str(c.id))
        awaiting.pop(m.from_user.id, None)
        return await m.reply(f"✅ Force-sub added: <b>{esc(c.title)}</b> (<code>{c.id}</code>)")
    if kind == "admin_caption":
        try:
            value.format(manga="a", chapter="b", source="c", pages="d")
        except Exception:
            return await m.reply("❌ Invalid template. Use only {manga}, {chapter}, {source}, {pages}.")
        await db.set_cfg("caption", value)
        awaiting.pop(m.from_user.id, None)
        return await m.reply("✅ Caption saved.")
    if kind == "admin_format":
        try:
            value.format(manga="a", chapter="b", source="c")
        except Exception:
            return await m.reply("❌ Invalid template. Use {manga}, {chapter}, {source} only.")
        await db.set_cfg("filename", value)
        awaiting.pop(m.from_user.id, None)
        return await m.reply("✅ File name format saved.")
    if kind == "admin_wm":
        parts = [p.strip() for p in value.split("|")]
        wm = {"text": parts[0], "position": "bottom-right", "color": "#ffffff", "opacity": 128, "size": 3}
        try:
            if len(parts) > 1 and parts[1]:
                assert parts[1] in ("top-left", "top-right", "bottom-left", "bottom-right", "center")
                wm["position"] = parts[1]
            if len(parts) > 2 and parts[2]:
                assert len(parts[2].lstrip("#")) == 6
                int(parts[2].lstrip("#"), 16)
                wm["color"] = parts[2]
            if len(parts) > 3 and parts[3]:
                wm["opacity"] = max(0, min(255, int(parts[3])))
            if len(parts) > 4 and parts[4]:
                wm["size"] = max(1, min(10, float(parts[4])))
        except Exception:
            return await m.reply("❌ Invalid watermark options. Check position, color and numbers.")
        await db.set_cfg("watermark", wm)
        awaiting.pop(m.from_user.id, None)
        return await m.reply("✅ Watermark saved. It applies to new downloads; use Clear cache to rebuild old ones.")


admin_photo_only = filters.create(lambda _, __, m: bool(getattr(m, "photo", None) and m.from_user and access.is_admin(m.from_user.id)))


@app.on_message(filters.private & admin_photo_only)
async def admin_thumb_photo(client, m):
    pending = awaiting.get(m.from_user.id)
    if not pending or pending.get("kind") != "admin_thumb":
        return
    Path(Config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    path = await client.download_media(m, file_name=str(Path(Config.DATA_DIR) / "thumb_raw"))
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((320, 320))
    im.save(Path(Config.DATA_DIR) / "thumb.jpg", "JPEG", quality=85)
    awaiting.pop(m.from_user.id, None)
    await m.reply("✅ Custom thumbnail saved.")


# ── subscribers ───────────────────────────────────────────────
@app.on_message(filters.command("addsub") & P & admin_only)
async def addsub_cmd(client, m):
    uid = _uid(m)
    if uid is None:
        return await m.reply("Usage: /addsub <code>user_id [days]</code> (or reply to the user)")
    a = _args(m)
    days = int(a[1]) if len(a) > 1 and a[1].isdigit() else 0
    expires = await db.add_sub(uid, days, m.from_user.id)
    until = f"until {datetime.utcfromtimestamp(expires):%Y-%m-%d}" if expires else "permanently"
    await m.reply(f"⭐ <code>{uid}</code> is now a <b>Subscriber</b> {until}.")
    lim = "unlimited" if Config.SUBSCRIBER_MAX_BATCH <= 0 else str(max(Config.SUBSCRIBER_MAX_BATCH, Config.MAX_BATCH))
    try:
        await client.send_message(uid, f"⭐ <b>You are now a Subscriber</b> {until}!\n"
                                       f"Batch limit: <b>{lim}</b> chapters · no monthly limit.")
    except Exception:
        pass


@app.on_message(filters.command("delsub") & P & admin_only)
async def delsub_cmd(client, m):
    uid = _uid(m)
    if uid is None:
        return await m.reply("Usage: /delsub <code>user_id</code>")
    await m.reply("✅ Removed." if await db.remove_sub(uid) else "That user is not a subscriber.")


@app.on_message(filters.command("subs") & P & admin_only)
async def subs_cmd(client, m):
    subs = await db.list_subs()
    if not subs:
        return await m.reply("No subscribers yet.")
    lines = ["<b>⭐ Subscribers</b>\n"]
    for s in subs[:80]:
        exp = f"{datetime.utcfromtimestamp(s['expires']):%Y-%m-%d}" if s.get("expires") else "permanent"
        lines.append(f"• <code>{s['_id']}</code> — {exp}")
    await m.reply("\n".join(lines))


# ── limits / toggles ──────────────────────────────────────────
@app.on_message(filters.command("monthly") & P & admin_only)
async def monthly_cmd(client, m):
    a = _args(m)
    if not a:
        state = "ON" if await access.monthly_enabled() else "OFF"
        return await m.reply(f"📅 Monthly limit is <b>{state}</b> ({await access.monthly_limit()} chapters/month).\n"
                             "Usage: /monthly <code>on|off|&lt;number&gt;</code>")
    if a[0].lower() in ("on", "off"):
        await db.set_cfg("monthly_enabled", a[0].lower() == "on")
    elif a[0].isdigit() and int(a[0]) > 0:
        await db.set_cfg("monthly_limit", int(a[0]))
        await db.set_cfg("monthly_enabled", True)
    else:
        return await m.reply("Usage: /monthly <code>on|off|&lt;number&gt;</code>")
    await m.reply(f"✅ Monthly limit: <b>{'ON' if await access.monthly_enabled() else 'OFF'}</b> "
                  f"({await access.monthly_limit()} chapters/month for free users; subscribers are exempt).")


# ── force-sub ─────────────────────────────────────────────────
@app.on_message(filters.command("addfsub") & P & admin_only)
async def addfsub_cmd(client, m):
    a = _args(m)
    if not a:
        return await m.reply("Usage: /addfsub <code>@channel</code> or <code>-100123…</code>\n"
                             "The bot must be an admin of that channel.")
    chat = a[0]
    try:
        c = await client.get_chat(int(chat) if chat.lstrip("-").isdigit() else chat.lstrip("@"))
        await client.get_chat_member(c.id, "me")
    except Exception as e:
        return await m.reply(f"❌ Couldn't use that channel: <code>{esc(type(e).__name__)}</code>.\n"
                             "Make sure the bot is an <b>admin</b> there.")
    await db.add_fsub(str(c.id))
    await m.reply(f"✅ Force-sub added: <b>{esc(c.title)}</b> (<code>{c.id}</code>)")


@app.on_message(filters.command("delfsub") & P & admin_only)
async def delfsub_cmd(client, m):
    a = _args(m)
    if not a:
        return await m.reply("Usage: /delfsub <code>channel id</code>")
    await m.reply("✅ Removed." if await db.remove_fsub(a[0]) else "Not found (env FORCE_SUB entries must be removed from the env).")


@app.on_message(filters.command("fsub") & P & admin_only)
async def fsub_cmd(client, m):
    chans = await fsub.channels()
    await m.reply("<b>📢 Force-sub channels</b>\n" + ("\n".join(f"• <code>{esc(c)}</code>" for c in chans) or "None"))


# ── users: ban / broadcast ────────────────────────────────────
@app.on_message(filters.command("ban") & P & admin_only)
async def ban_cmd(client, m):
    uid = _uid(m)
    if uid is None or access.is_admin(uid):
        return await m.reply("Usage: /ban <code>user_id</code> (admins can't be banned)")
    await db.set_user(uid, banned=True)
    await m.reply(f"🚫 <code>{uid}</code> banned.")


@app.on_message(filters.command("unban") & P & admin_only)
async def unban_cmd(client, m):
    uid = _uid(m)
    if uid is None:
        return await m.reply("Usage: /unban <code>user_id</code>")
    await db.set_user(uid, banned=False)
    await m.reply(f"✅ <code>{uid}</code> unbanned.")


@app.on_message(filters.command("broadcast") & P & admin_only)
async def broadcast_cmd(client, m):
    if not m.reply_to_message:
        return await m.reply("Reply to the message you want to broadcast with /broadcast")
    ids = await db.all_user_ids()
    status = await m.reply(f"📢 Broadcasting to {len(ids)} users…")
    ok = fail = 0
    for i, uid in enumerate(ids, 1):
        try:
            await m.reply_to_message.copy(uid)
            ok += 1
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            try:
                await m.reply_to_message.copy(uid)
                ok += 1
            except Exception:
                fail += 1
        except (UserIsBlocked, InputUserDeactivated):
            fail += 1
        except Exception:
            fail += 1
        if i % 40 == 0:
            try:
                await status.edit_text(f"📢 Broadcasting… {i}/{len(ids)}")
            except Exception:
                pass
        await asyncio.sleep(0.06)
    await status.edit_text(f"📢 <b>Broadcast finished</b>\n✅ Sent: {ok}\n❌ Failed: {fail}")


# ── branding ──────────────────────────────────────────────────
@app.on_message(filters.command("setcaption") & P & admin_only)
async def setcaption_cmd(client, m):
    if len(m.command) < 2:
        return await m.reply("Usage: /setcaption <code>text</code>\nVars: {manga} {chapter} {source} {pages}")
    text = m.text.split(None, 1)[1]
    try:
        text.format(manga="a", chapter="b", source="c", pages="d")
    except Exception:
        return await m.reply("❌ Invalid template. Only {manga} {chapter} {source} {pages} are allowed.")
    await db.set_cfg("caption", text)
    await m.reply("✅ Caption saved.")


@app.on_message(filters.command("setformat") & P & admin_only)
async def setformat_cmd(client, m):
    if len(m.command) < 2:
        return await m.reply("Usage: /setformat <code>{manga} - {chapter}</code>")
    text = m.text.split(None, 1)[1]
    try:
        text.format(manga="a", chapter="b", source="c")
    except Exception:
        return await m.reply("❌ Invalid. Only {manga} {chapter} {source} are allowed.")
    await db.set_cfg("filename", text)
    await m.reply("✅ File name format saved.")


@app.on_message(filters.command("setwm") & P & admin_only)
async def setwm_cmd(client, m):
    if len(m.command) < 2:
        return await m.reply("Usage: /setwm <code>text | position | #color | opacity | size%</code>\n"
                             "Example: /setwm <code>@MyChannel | bottom-right | #ffffff | 140 | 3</code>\n"
                             "Positions: top-left, top-right, bottom-left, bottom-right, center")
    parts = [p.strip() for p in m.text.split(None, 1)[1].split("|")]
    wm = {"text": parts[0], "position": "bottom-right", "color": "#ffffff", "opacity": 128, "size": 3}
    try:
        if len(parts) > 1 and parts[1]:
            assert parts[1] in ("top-left", "top-right", "bottom-left", "bottom-right", "center")
            wm["position"] = parts[1]
        if len(parts) > 2 and parts[2]:
            assert len(parts[2].lstrip("#")) == 6
            int(parts[2].lstrip("#"), 16)
            wm["color"] = parts[2]
        if len(parts) > 3 and parts[3]:
            wm["opacity"] = max(0, min(255, int(parts[3])))
        if len(parts) > 4 and parts[4]:
            wm["size"] = max(1, min(10, float(parts[4])))
    except Exception:
        return await m.reply("❌ Invalid option. Check position / #rrggbb colour / numbers.")
    await db.set_cfg("watermark", wm)
    await m.reply("✅ Watermark saved. It is applied to <b>new</b> downloads — run /clearcache to rebuild old ones.")


@app.on_message(filters.command("delwm") & P & admin_only)
async def delwm_cmd(client, m):
    await db.set_cfg("watermark", None)
    await m.reply("✅ Watermark removed.")


@app.on_message(filters.command("setthumb") & P & admin_only)
async def setthumb_cmd(client, m):
    r = m.reply_to_message
    if not (r and r.photo):
        return await m.reply("Reply to a <b>photo</b> with /setthumb")
    Path(Config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    path = await client.download_media(r, file_name=str(Path(Config.DATA_DIR) / "thumb_raw"))
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((320, 320))
    im.save(Path(Config.DATA_DIR) / "thumb.jpg", "JPEG", quality=85)
    await m.reply("✅ Custom thumbnail saved.")


@app.on_message(filters.command("delthumb") & P & admin_only)
async def delthumb_cmd(client, m):
    (Path(Config.DATA_DIR) / "thumb.jpg").unlink(missing_ok=True)
    await m.reply("✅ Custom thumbnail removed (auto cover thumbnails are used).")


@app.on_message(filters.command("resetbranding") & P & admin_only)
async def resetbranding_cmd(client, m):
    for k in ("caption", "filename", "watermark"):
        await db.set_cfg(k, None)
    (Path(Config.DATA_DIR) / "thumb.jpg").unlink(missing_ok=True)
    await m.reply("✅ Caption, file name, watermark and thumbnail reset to defaults.")


@app.on_message(filters.command("clearcache") & P & admin_only)
async def clearcache_cmd(client, m):
    n = await db.clear_files()
    await m.reply(f"🧹 Cleared {n} cached file(s). Chapters will be rebuilt on next request.")


# ── sites ─────────────────────────────────────────────────────
@app.on_message(filters.command("sites") & P & admin_only)
async def sites_cmd(client, m):
    await m.reply(sites_text())


@app.on_message(filters.command("checksites") & P & admin_only)
async def checksites_cmd(client, m):
    status = await m.reply("🔎 Checking all sites…")
    await check_all_sites()
    await status.edit_text(sites_text())


async def check_all_sites():
    sem = asyncio.Semaphore(15)

    async def one(s):
        async with sem:
            try:
                await s.check()
            except Exception as e:
                s.online, s.reason = False, type(e).__name__
    await asyncio.gather(*(one(s) for s in registry.sources.values()))


# ── owner only ────────────────────────────────────────────────
@app.on_message(filters.command("addadmin") & P & owner_only)
async def addadmin_cmd(client, m):
    uid = _uid(m)
    if uid is None:
        return await m.reply("Usage: /addadmin <code>user_id</code>")
    await access.add_admin(uid)
    await m.reply(f"✅ <code>{uid}</code> is now an admin.")


@app.on_message(filters.command("deladmin") & P & owner_only)
async def deladmin_cmd(client, m):
    uid = _uid(m)
    if uid is None or access.is_owner(uid):
        return await m.reply("Usage: /deladmin <code>user_id</code>")
    await access.remove_admin(uid)
    await m.reply(f"✅ <code>{uid}</code> is no longer an admin.")


@app.on_message(filters.command("admins") & P & owner_only)
async def admins_cmd(client, m):
    ids = sorted(access.admin_ids)
    await m.reply("<b>🛡 Admins</b>\n👑 <code>%d</code> (owner)\n" % Config.OWNER_ID +
                  "\n".join(f"• <code>{i}</code>" for i in ids))
