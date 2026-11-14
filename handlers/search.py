"""Search flow: type a title -> site picker -> results (same layout as the reference screenshots)."""
import asyncio
import difflib
import math
import time
from dataclasses import dataclass, field
from typing import List, Optional

from pyrogram import filters

from config import Config
from core.models import Card
from core.content import is_safe_title
from core.utils import esc, trunc
from ctx import app, awaiting, db, registry, sessions
from handlers.common import gate
from handlers.ui import Btn, edit_card, kb, safe_answer, send_card

SITES_PER_PAGE = 14
RESULTS_PER_PAGE = 10
CAT_TITLES = {"all": "🌐 ALL SUPPORTED SITES", "manga": "📚 MANGA SITES", "manhwa": "🔥 MANHWA SITES"}


@dataclass(eq=False)
class SearchSession:
    user_id: int
    query: str
    cat: str = "all"
    page: int = 0
    results: List[Card] = field(default_factory=list)
    found_sites: List[str] = field(default_factory=list)
    empty_sites: List[str] = field(default_factory=list)
    scope: tuple = ("all",)          # ("all",) | ("site", key)
    site_filter: Optional[str] = None
    rpage: int = 0
    view: str = "picker"             # picker | results

    def found_sites_names(self) -> List[str]:
        return [registry.get(k).name for k in self.found_sites]

    def empty_names(self) -> List[str]:
        return [registry.get(k).name for k in self.empty_sites]


def dot(src) -> str:
    return "⚪" if src.online is None else ("🟢" if src.online else "🔴")


# ── picker ────────────────────────────────────────────────────
async def picker_view(tok: str, s: SearchSession):
    sites = registry.listing(s.cat)
    pages = max(1, math.ceil(len(sites) / SITES_PER_PAGE))
    s.page = min(max(s.page, 0), pages - 1)
    chunk = sites[s.page * SITES_PER_PAGE:(s.page + 1) * SITES_PER_PAGE]

    def cat_btn(cat, label):
        return Btn(("📍 " if s.cat == cat else "") + label, callback_data=f"s|{tok}|c|{cat}")

    rows = [[cat_btn("manga", "📚 MANGA"), cat_btn("manhwa", "🔥 MANHWA")],
            [cat_btn("all", "🌐 ALL")]]
    pair = []
    for src in chunk:
        pair.append(Btn(f"{dot(src)} {src.name.upper()}", callback_data=f"s|{tok}|go|{src.key}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    nav = []
    if s.page > 0:
        nav.append(Btn("❮ PREV", callback_data=f"s|{tok}|pg|{s.page - 1}"))
    nav.append(Btn(f"📄 {s.page + 1}/{pages}", callback_data="noop"))
    if s.page < pages - 1:
        nav.append(Btn("NEXT ❯", callback_data=f"s|{tok}|pg|{s.page + 1}"))
    rows.append(nav)
    rows.append([Btn("♞ ALL SEARCH ♞", callback_data=f"s|{tok}|all"), Btn("🔴 CLOSE", callback_data=f"s|{tok}|x")])
    text = (f"<b>➲ {CAT_TITLES.get(s.cat, CAT_TITLES['all'])}</b>\n"
            f"<blockquote>▸ <b>Query:</b> <code>{esc(trunc(s.query, 80))}</code>\n\n"
            f"Select a site below to start searching:</blockquote>")
    return text, kb(rows)


# ── results ───────────────────────────────────────────────────
def visible(s: SearchSession) -> List[Card]:
    return [c for c in s.results if s.site_filter in (None, c.source_key)]


def _join_limited(names: List[str], limit: int) -> str:
    out, used = [], 0
    for i, n in enumerate(names):
        if used + len(n) + 2 > limit:
            out.append(f"+{len(names) - i} more")
            break
        out.append(esc(n))
        used += len(n) + 2
    return ", ".join(out)


def results_view(tok: str, s: SearchSession):
    vis = visible(s)
    pages = max(1, math.ceil(len(vis) / RESULTS_PER_PAGE))
    s.rpage = min(max(s.rpage, 0), pages - 1)
    chunk = vis[s.rpage * RESULTS_PER_PAGE:(s.rpage + 1) * RESULTS_PER_PAGE]
    rows = []
    for card in chunk:
        src = registry.get(card.source_key)
        label = f"{dot(src)} {trunc(card.title, 34)} • {src.name}"
        rows.append([Btn(label, callback_data=f"s|{tok}|pick|{s.results.index(card)}")])
    nav = []
    if s.rpage > 0:
        nav.append(Btn("❮ PREV", callback_data=f"s|{tok}|rp|{s.rpage - 1}"))
    if s.rpage + 2 < pages:
        nav.append(Btn("NEXT 2x ❯❯", callback_data=f"s|{tok}|rp|{s.rpage + 2}"))
    if s.rpage < pages - 1:
        nav.append(Btn("NEXT ❯", callback_data=f"s|{tok}|rp|{s.rpage + 1}"))
    rows.append(nav)
    if len(s.found_sites) > 1:
        rows.append([Btn("❮ PREV SITE", callback_data=f"s|{tok}|ps|-1"),
                     Btn("NEXT SITE ❯", callback_data=f"s|{tok}|ps|1")])
    rows.append([Btn("↻ RETRY", callback_data=f"s|{tok}|retry")])
    rows.append([Btn("⬅ BACK", callback_data=f"s|{tok}|back"), Btn("🔴 CLOSE", callback_data=f"s|{tok}|x")])

    scope = "All" if s.scope[0] == "all" else registry.get(s.scope[1]).name
    lines = [f"<b>➲ SEARCH RESULTS</b>\n",
             f"<blockquote>▸ <b>Query:</b> <code>{esc(trunc(s.query, 60))}</code>\n"
             f"▸ <b>Source:</b> {esc(scope)}\n"
             f"▸ <b>Results:</b> {len(s.results)} found"]
    if s.site_filter:
        lines[-1] += f"\n▸ <b>Showing:</b> {esc(registry.get(s.site_filter).name)} ({len(vis)})"
    lines[-1] += "</blockquote>"
    if s.scope[0] == "all":
        lines.append(f"<blockquote expandable><b>Found Sites:</b> <i>{_join_limited(s.found_sites_names(), 330)}</i></blockquote>")
        if s.empty_sites:
            lines.append(f"<b>Sites with no results:</b> {_join_limited(s.empty_names(), 220)}")
    return "\n".join(lines), kb(rows)


def _score(query: str, title: str) -> float:
    q, t = query.lower().strip(), title.lower()
    r = difflib.SequenceMatcher(None, q, t).ratio()
    if t == q:
        r += 2
    elif t.startswith(q):
        r += 1
    elif q in t:
        r += 0.6
    return r


async def run_search(s: SearchSession, sources) -> None:
    sem = asyncio.Semaphore(12)

    async def one(src):
        async with sem:
            try:
                return src, await asyncio.wait_for(src.search(s.query), Config.SEARCH_TIMEOUT)
            except Exception:
                return src, []

    outcomes = await asyncio.gather(*(one(src) for src in sources))
    s.results, s.found_sites, s.empty_sites = [], [], []
    for src, cards in outcomes:
        cards = [card for card in cards if is_safe_title(card.title)]
        if cards:
            s.found_sites.append(src.key)
            s.results.extend(cards)
        else:
            s.empty_sites.append(src.key)
    s.results.sort(key=lambda c: -_score(s.query, c.title))  # stable: keeps site order on ties
    s.found_sites.sort(key=lambda k: -sum(1 for c in s.results if c.source_key == k))
    s.site_filter, s.rpage = None, 0


# ── entry: user sends text ────────────────────────────────────
@app.on_message(filters.private & filters.text & ~filters.regex(r"^/"))
async def on_text(client, m):
    if not await gate(client, m):
        return
    pending = awaiting.get(m.from_user.id)
    if pending and time.time() - pending["ts"] < 600:
        if str(pending.get("kind", "")).startswith("admin_"):
            from handlers.admin import handle_admin_input
            return await handle_admin_input(client, m, pending)
        from handlers.manga import handle_pending_text
        return await handle_pending_text(client, m, pending)
    awaiting.pop(m.from_user.id, None)
    query = m.text.strip()
    if len(query) < 2:
        return await m.reply("Please send at least 2 characters.")
    s = SearchSession(user_id=m.from_user.id, query=query[:100])
    tok = sessions.new(s)
    text, markup = await picker_view(tok, s)
    await send_card(client, m.chat.id, text, markup, reply_to=m.id)


# ── callbacks ─────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^s\|"))
async def on_search_cb(client, cb):
    if not await gate(client, cb):
        return
    _, tok, action, *rest = cb.data.split("|")
    arg = rest[0] if rest else ""
    s: Optional[SearchSession] = sessions.get(tok)
    if s is None:
        return await safe_answer(cb, "This search expired — please send the name again.", True)
    if s.user_id != cb.from_user.id:
        return await safe_answer(cb, "This isn't your search.", True)
    msg = cb.message

    if action == "x":
        await safe_answer(cb)
        sessions.drop(tok)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    if action == "c":                       # category tab
        if arg not in ("all", "manga", "manhwa"):
            return await safe_answer(cb, "Unknown category.", True)
        s.cat, s.page = arg, 0
        text, markup = await picker_view(tok, s)
        await edit_card(msg, text, markup)
        return await safe_answer(cb)

    if action == "pg":
        s.page = int(arg)
        text, markup = await picker_view(tok, s)
        await edit_card(msg, text, markup)
        return await safe_answer(cb)

    if action in ("back",):
        s.view = "picker"
        text, markup = await picker_view(tok, s)
        await edit_card(msg, text, markup)
        return await safe_answer(cb)

    if action in ("go", "all", "retry"):
        if action == "go":
            src = registry.get(arg)
            if not src:
                return await safe_answer(cb, "Unknown site.", True)
            s.scope = ("site", arg)
            sources = [src]
        elif action == "all":
            s.scope = ("all",)
            sources = registry.listing(s.cat, online_only=True)
        else:
            sources = ([registry.get(s.scope[1])] if s.scope[0] == "site" else
                       registry.listing(s.cat, online_only=True))
        if not sources:
            return await safe_answer(cb, "No sites available in this category.", True)
        await safe_answer(cb, f"🔎 Searching {len(sources)} site(s)…")
        await edit_card(msg, f"<b>🔎 Searching {len(sources)} site(s)…</b>\n"
                             f"<blockquote>▸ <b>Query:</b> <code>{esc(trunc(s.query, 80))}</code></blockquote>\n"
                             f"<i>This can take up to {Config.SEARCH_TIMEOUT}s.</i>")
        await run_search(s, sources)
        s.view = "results"
        if not s.results:
            return await edit_card(
                msg, f"😕 <b>No results</b> for <code>{esc(trunc(s.query, 60))}</code>\n\n"
                     "<i>Try a different spelling or another site.</i>",
                kb([[Btn("↻ RETRY", callback_data=f"s|{tok}|retry")],
                    [Btn("⬅ BACK", callback_data=f"s|{tok}|back"), Btn("🔴 CLOSE", callback_data=f"s|{tok}|x")]]))
        text, markup = results_view(tok, s)
        return await edit_card(msg, text, markup)

    if action == "rp":
        s.rpage = int(arg)
        text, markup = results_view(tok, s)
        await edit_card(msg, text, markup)
        return await safe_answer(cb)

    if action == "ps":                      # cycle through sites that had results
        keys = [None] + s.found_sites
        idx = keys.index(s.site_filter) if s.site_filter in keys else 0
        s.site_filter, s.rpage = keys[(idx + int(arg)) % len(keys)], 0
        text, markup = results_view(tok, s)
        await edit_card(msg, text, markup)
        return await safe_answer(cb, "Showing all sites" if s.site_filter is None else registry.get(s.site_filter).name)

    if action == "pick":
        card = s.results[int(arg)]
        from handlers.manga import open_card
        return await open_card(client, cb, s, tok, card)
