"""Manga card -> chapter list -> single / batch downloads."""
import math
import time
from dataclasses import dataclass, field
from typing import List, Optional

from pyrogram import filters
from pyrogram.types import CallbackQuery

from config import Config
from core.models import Card, Chapter
from core.content import is_safe_metadata
from core.utils import esc, fmt_num, select_by_spec, trunc
from ctx import access, app, awaiting, db, http, jobs, registry, sessions
from handlers.common import gate
from handlers.ui import Btn, cover_photo, edit_card, kb, random_pic, safe_answer, send_card

PER_PAGE = 12


@dataclass(eq=False)
class MangaSession:
    user_id: int
    source: object
    card: Card
    chapters: List[Chapter]                 # oldest -> newest
    details: dict = field(default_factory=dict)
    page: int = 0
    newest_first: bool = True
    select_mode: bool = False
    selected: set = field(default_factory=set)   # indices into .chapters
    back_tok: Optional[str] = None


async def _limit_text(uid: int) -> str:
    role = await access.role(uid)
    return "unlimited" if role.batch_limit is None else str(role.batch_limit)


def _user_formats(user: dict) -> List[str]:
    return {"pdf": ["pdf"], "cbz": ["cbz"], "both": ["pdf", "cbz"]}.get(user.get("format", "pdf"), ["pdf"])


# ── card ──────────────────────────────────────────────────────
async def card_view(tok: str, ms: MangaSession):
    total = len(ms.chapters)
    last = fmt_num(ms.chapters[-1].number) if total and ms.chapters[-1].number is not None else "?"
    d = ms.details
    lines = [f"<b>{esc(trunc(ms.card.title, 120))}</b>",
             f"<blockquote>▸ <b>Source:</b> {esc(ms.source.name)}\n▸ <b>Chapters:</b> {total} (latest: {last})"]
    if d.get("status"):
        lines[-1] += f"\n▸ <b>Status:</b> {esc(trunc(d['status'], 30))}"
    if d.get("genres"):
        lines[-1] += f"\n▸ <b>Genres:</b> {esc(trunc(d['genres'], 70))}"
    lines[-1] += "</blockquote>"
    if d.get("description"):
        lines.append(f"<blockquote expandable>{esc(trunc(d['description'], 380))}</blockquote>")
    lines.append(f"📦 Batch limit: <b>{await _limit_text(ms.user_id)}</b>")
    rows = [[Btn("📥 Single chapter", callback_data=f"m|{tok}|list|0|1"),
             Btn("📦 Batch", callback_data=f"m|{tok}|bt")],
            [Btn("🔗 Open on site", url=ms.card.url)],
            [Btn("🔴 CLOSE", callback_data=f"m|{tok}|x")]]
    return "\n".join(lines), kb(rows)


async def open_card(client, cb, search_sess, search_tok: str, card: Card):
    src = registry.get(card.source_key)
    await safe_answer(cb, "Loading…")
    try:
        chapters, details = await src.load(card)
        if not is_safe_metadata(details):
            return await safe_answer(cb, "This title is hidden by the content-safety filter.", True)
    except Exception as e:
        return await safe_answer(cb, f"Couldn't open this title ({type(e).__name__}). Try another site.", True)
    if not chapters:
        return await safe_answer(cb, "No free chapters found here (they may be locked/paid or the site changed).", True)
    ms = MangaSession(cb.from_user.id, src, card, chapters, details, back_tok=search_tok)
    tok = sessions.new(ms)
    text, markup = await card_view(tok, ms)
    photo = await cover_photo(http, src, card.cover) or random_pic()
    await send_card(client, cb.message.chat.id, text, markup, photo=photo)


# ── chapter list ──────────────────────────────────────────────
def _label(ms: MangaSession, idx: int) -> str:
    ch = ms.chapters[idx]
    name = f"Ch. {fmt_num(ch.number)}" if ch.number is not None and len(ch.title) > 18 else trunc(ch.title, 22)
    return ("✅ " if idx in ms.selected else "") + name


async def list_view(tok: str, ms: MangaSession):
    order = list(range(len(ms.chapters)))
    if ms.newest_first:
        order.reverse()
    pages = max(1, math.ceil(len(order) / PER_PAGE))
    ms.page = min(max(ms.page, 0), pages - 1)
    chunk = order[ms.page * PER_PAGE:(ms.page + 1) * PER_PAGE]
    rows, pair = [], []
    for idx in chunk:
        pair.append(Btn(_label(ms, idx), callback_data=f"m|{tok}|ch|{idx}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    nav = []
    if ms.page > 0:
        nav.append(Btn("❮ PREV", callback_data=f"m|{tok}|list|{ms.page - 1}"))
    nav.append(Btn(f"📄 {ms.page + 1}/{pages}", callback_data="noop"))
    if ms.page < pages - 1:
        nav.append(Btn("NEXT ❯", callback_data=f"m|{tok}|list|{ms.page + 1}"))
    rows.append(nav)
    rows.append([Btn("🔼 Oldest first" if ms.newest_first else "🔽 Newest first", callback_data=f"m|{tok}|ord"),
                 Btn("☑️ Select: ON" if ms.select_mode else "☑️ Select: OFF", callback_data=f"m|{tok}|sel")])
    if ms.select_mode:
        rows.append([Btn(f"📥 Download selected ({len(ms.selected)})", callback_data=f"m|{tok}|dls"),
                     Btn("🧹 Clear", callback_data=f"m|{tok}|clr")])
    rows.append([Btn("⬅ BACK", callback_data=f"m|{tok}|home"), Btn("🔴 CLOSE", callback_data=f"m|{tok}|x")])
    mode = (f"Select mode — tap chapters, then <b>Download selected</b> (max {await _limit_text(ms.user_id)})"
            if ms.select_mode else "Tap a chapter to download it")
    text = (f"<b>{esc(trunc(ms.card.title, 80))}</b> · {esc(ms.source.name)}\n"
            f"<blockquote>{mode}\n{len(ms.chapters)} chapters · page {ms.page + 1}/{pages}</blockquote>")
    return text, kb(rows)


# ── batch menu ────────────────────────────────────────────────
async def batch_view(tok: str, ms: MangaSession):
    role = await access.role(ms.user_id)
    total, lim = len(ms.chapters), role.batch_limit
    sizes = [n for n in (5, 10, 20, 50) if (lim is None or n <= lim) and n <= total]
    if lim is not None and lim not in sizes and lim <= total and lim < 100:
        sizes.append(lim)
    if not sizes:
        sizes = [min(total, lim or total)]
    sizes = sorted(set(sizes))[:4]
    rows = [[Btn(f"⏩ Latest {n}", callback_data=f"m|{tok}|lat|{n}"),
             Btn(f"⏪ First {n}", callback_data=f"m|{tok}|fst|{n}")] for n in sizes]
    all_ok = lim is None or total <= lim
    rows.append([Btn("✍️ Custom range", callback_data=f"m|{tok}|rng"),
                 Btn("☑️ Pick chapters", callback_data=f"m|{tok}|pick")])
    rows.append([Btn(f"♾ All chapters ({total})" + ("" if all_ok else " ⭐"), callback_data=f"m|{tok}|all")])
    rows.append([Btn("⬅ BACK", callback_data=f"m|{tok}|home"), Btn("🔴 CLOSE", callback_data=f"m|{tok}|x")])
    who = {"user": "Free users", "subscriber": "Subscribers"}.get(role.name, "You")
    limit_line = ("no batch limit" if lim is None else f"up to <b>{lim}</b> chapters per batch")
    text = (f"<b>📦 Batch download</b> · {esc(trunc(ms.card.title, 70))}\n"
            f"<blockquote>{who}: {limit_line}.\n"
            f"Choose how many chapters to send:</blockquote>")
    if lim is not None and role.name == "user":
        text += "\n⭐ <i>Need more? Ask the admin for Subscriber access.</i>"
    return text, kb(rows)


# ── submit ────────────────────────────────────────────────────
async def submit(client, cb_or_msg, ms: MangaSession, chapters: List[Chapter]) -> bool:
    """Validate limits and queue the job. Works for callbacks and plain messages.
    Returns True when the job was queued."""
    uid = ms.user_id
    chat_id = cb_or_msg.message.chat.id if isinstance(cb_or_msg, CallbackQuery) else cb_or_msg.chat.id

    async def say(text):
        await client.send_message(chat_id, text)
        return False

    if not chapters:
        return await say("❌ No chapters matched that selection.")
    if jobs.user_busy(uid):
        return await say("⏳ You already have a download running. Wait for it to finish or tap <b>Cancel</b> on it.")
    ok, why = await access.check_request(uid, len(chapters))
    if not ok:
        return await say(why)
    user = await db.get_user(uid)
    await jobs.submit(user_id=uid, chat_id=chat_id, source=ms.source, card=ms.card,
                      chapters=chapters, fmts=_user_formats(user))
    return True


# ── callbacks ─────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^m\|"))
async def on_manga_cb(client, cb):
    if not await gate(client, cb):
        return
    _, tok, action, *rest = cb.data.split("|")
    arg = rest[0] if rest else ""
    ms: Optional[MangaSession] = sessions.get(tok)
    if ms is None:
        return await safe_answer(cb, "This card expired — please search again.", True)
    if ms.user_id != cb.from_user.id:
        return await safe_answer(cb, "This isn't your card.", True)
    msg = cb.message

    if action == "x":
        await safe_answer(cb)
        try:
            await msg.delete()
        except Exception:
            pass
        return
    if action == "home":
        ms.select_mode = False
        text, markup = await card_view(tok, ms)
        await edit_card(msg, text, markup)
        return await safe_answer(cb)
    if action == "list":
        ms.page = int(arg)
        if len(rest) > 1:           # entering from the card = plain single mode
            ms.select_mode, ms.selected = False, set()
        text, markup = await list_view(tok, ms)
        await edit_card(msg, text, markup)
        return await safe_answer(cb)
    if action == "ord":
        ms.newest_first, ms.page = not ms.newest_first, 0
        text, markup = await list_view(tok, ms)
        await edit_card(msg, text, markup)
        return await safe_answer(cb)
    if action == "bt":
        text, markup = await batch_view(tok, ms)
        await edit_card(msg, text, markup)
        return await safe_answer(cb)
    if action in ("sel", "pick"):
        ms.select_mode = True if action == "pick" else not ms.select_mode
        if not ms.select_mode:
            ms.selected = set()
        text, markup = await list_view(tok, ms)
        await edit_card(msg, text, markup)
        return await safe_answer(cb)
    if action == "clr":
        ms.selected = set()
        text, markup = await list_view(tok, ms)
        await edit_card(msg, text, markup)
        return await safe_answer(cb, "Cleared")
    if action == "ch":
        idx = int(arg)
        if ms.select_mode:
            role = await access.role(ms.user_id)
            if idx in ms.selected:
                ms.selected.discard(idx)
            elif role.batch_limit is not None and len(ms.selected) >= role.batch_limit:
                return await safe_answer(cb, f"Limit reached: {role.batch_limit} chapters per batch.", True)
            else:
                ms.selected.add(idx)
            text, markup = await list_view(tok, ms)
            await edit_card(msg, text, markup)
            return await safe_answer(cb)
        await safe_answer(cb, "Queued ✅")
        return await submit(client, cb, ms, [ms.chapters[idx]])
    if action == "dls":
        chosen = [ms.chapters[i] for i in sorted(ms.selected)]
        await safe_answer(cb, "Queued ✅" if chosen else "Select at least one chapter.", not chosen)
        if chosen:
            ms.selected, ms.select_mode = set(), False
            await submit(client, cb, ms, chosen)
        return
    if action in ("lat", "fst"):
        n = max(1, int(arg))
        chosen = ms.chapters[-n:] if action == "lat" else ms.chapters[:n]
        await safe_answer(cb, "Queued ✅")
        return await submit(client, cb, ms, chosen)
    if action == "all":
        role = await access.role(ms.user_id)
        if role.batch_limit is not None and len(ms.chapters) > role.batch_limit:
            return await safe_answer(cb, f"'All chapters' needs Subscriber access for series longer than "
                                         f"{role.batch_limit} chapters.", True)
        await safe_answer(cb, "Queued ✅")
        return await submit(client, cb, ms, list(ms.chapters))
    if action == "rng":
        awaiting[cb.from_user.id] = {"kind": "range", "token": tok, "ts": time.time()}
        await safe_answer(cb)
        first = fmt_num(ms.chapters[0].number) if ms.chapters[0].number is not None else "1"
        last = fmt_num(ms.chapters[-1].number) if ms.chapters[-1].number is not None else str(len(ms.chapters))
        return await client.send_message(
            cb.message.chat.id,
            f"✍️ <b>Send the chapter numbers</b> (available: {first} – {last})\n\n"
            "<blockquote>Examples:\n<code>10-15</code>\n<code>7</code>\n<code>1-3,8,12-14</code></blockquote>\n"
            f"Limit: <b>{await _limit_text(ms.user_id)}</b> chapters · /cancel to stop")


@app.on_callback_query(filters.regex(r"^job\|"))
async def on_job_cb(client, cb):
    _, job_id, action = cb.data.split("|")
    if action == "cancel":
        ok = jobs.cancel(job_id, cb.from_user.id)
        await safe_answer(cb, "Cancelling…" if ok else "Nothing to cancel.", not ok)


async def handle_pending_text(client, m, pending: dict):
    ms = sessions.get(pending["token"])
    if pending["kind"] != "range" or ms is None:
        awaiting.pop(m.from_user.id, None)
        return await m.reply("That request expired — please search again.")
    chosen = select_by_spec(ms.chapters, m.text)
    if chosen is None:
        return await m.reply("❌ Couldn't read that. Use a format like <code>10-15</code> or <code>1-3,8</code>. "
                             "(/cancel to stop)")
    if not chosen:
        return await m.reply("❌ No chapters match those numbers. Try again or /cancel.")
    if await submit(client, m, ms, chosen):
        awaiting.pop(m.from_user.id, None)
