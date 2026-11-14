"""Download queue: fetch pages -> build PDF/CBZ -> upload -> cache file_ids."""
import asyncio
import hashlib
import logging
import math
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import Config
from core import files
from core.models import Card, Chapter
from core.utils import esc, fmt_num, progress_bar, safe_filename, trunc

log = logging.getLogger("jobs")

DEFAULT_CAPTION = "<b>{manga}</b>\n{chapter}\n<i>Source: {source}</i>"
DEFAULT_FILENAME = "{manga} - {chapter}"


class JobError(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass(eq=False)
class Job:
    id: str
    user_id: int
    chat_id: int
    source: object
    card: Card
    chapters: List[Chapter]
    fmts: List[str]
    status_msg: object = None
    cancelled: bool = False
    done: int = 0
    failed: List[str] = field(default_factory=list)
    _last_edit: float = 0.0


class JobManager:
    def __init__(self, app, db, http):
        self.app, self.db, self.http = app, db, http
        self.queue: "asyncio.Queue[Job]" = asyncio.Queue()
        self.by_user: dict = {}
        self.by_id: dict = {}
        self._tasks: List[asyncio.Task] = []
        self._seq = 0

    async def start(self):
        Path(Config.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
        shutil.rmtree(Config.DOWNLOAD_DIR, ignore_errors=True)
        Path(Config.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
        self._tasks = [asyncio.create_task(self._worker(i)) for i in range(Config.WORKERS)]

    async def stop(self):
        for t in self._tasks:
            t.cancel()

    # ── public ────────────────────────────────────────────────
    def user_busy(self, uid: int) -> bool:
        return uid in self.by_user

    async def submit(self, *, user_id, chat_id, source, card, chapters, fmts) -> Job:
        self._seq += 1
        job = Job(id=f"{int(time.time()) % 100000}{self._seq}", user_id=user_id, chat_id=chat_id,
                  source=source, card=card, chapters=list(chapters), fmts=list(fmts))
        pos = self.queue.qsize() + 1
        job.status_msg = await self.app.send_message(
            chat_id, f"⏳ <b>Queued</b> — {esc(trunc(card.title, 60))}\n"
                     f"{len(job.chapters)} chapter(s) · position {pos}",
            reply_markup=self._cancel_markup(job))
        self.by_user[user_id] = job
        self.by_id[job.id] = job
        await self.queue.put(job)
        return job

    def cancel(self, job_id: str, uid: int) -> bool:
        job = self.by_id.get(job_id)
        if not job or job.user_id != uid:
            return False
        job.cancelled = True
        return True

    # ── internals ─────────────────────────────────────────────
    @staticmethod
    def _cancel_markup(job: Job):
        return InlineKeyboardMarkup([[InlineKeyboardButton("✖ Cancel", callback_data=f"job|{job.id}|cancel")]])

    async def _edit(self, job: Job, text: str, force: bool = False, markup=True):
        now = time.time()
        if not force and now - job._last_edit < 3:
            return
        job._last_edit = now
        try:
            await job.status_msg.edit_text(text, reply_markup=self._cancel_markup(job) if markup else None)
        except MessageNotModified:
            pass
        except FloodWait as e:
            job._last_edit = now + e.value
        except Exception as e:
            log.debug("status edit failed: %s", e)

    async def _worker(self, n: int):
        while True:
            job = await self.queue.get()
            try:
                await self._run(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("job %s crashed", job.id)
            finally:
                self.by_user.pop(job.user_id, None)
                self.by_id.pop(job.id, None)
                self.queue.task_done()

    async def _run(self, job: Job):
        total = len(job.chapters)
        title = esc(trunc(job.card.title, 60))
        for i, ch in enumerate(job.chapters, 1):
            if job.cancelled:
                break
            head = f"📥 <b>{title}</b>\n<b>{esc(trunc(ch.title, 50))}</b> ({i}/{total})\n"
            await self._edit(job, head + "⏳ Preparing…", force=True)

            async def prog(done, tot, head=head):
                await self._edit(job, head + f"⬇️ Pages {done}/{tot}\n{progress_bar(done, tot)}")

            try:
                await self._process(job, ch, head, prog)
                job.done += 1
                await self.db.add_usage(job.user_id, 1)
            except Cancelled:
                break
            except JobError as e:
                job.failed.append(f"{ch.title}: {e}")
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                job.failed.append(f"{ch.title}: Telegram flood wait")
            except Exception as e:
                log.exception("chapter failed")
                job.failed.append(f"{ch.title}: {type(e).__name__}")
        if job.cancelled:
            text = f"🛑 <b>Cancelled.</b> {job.done}/{total} chapter(s) were sent."
        else:
            text = f"✅ <b>Done!</b> {job.done}/{total} chapter(s) sent — {title}"
        if job.failed:
            text += "\n\n⚠️ <b>Failed:</b>\n" + "\n".join("• " + esc(trunc(f, 90)) for f in job.failed[:8])
        await self._edit(job, text, force=True, markup=False)

    # one chapter -> one file per requested format
    async def _process(self, job: Job, ch: Chapter, head: str, prog, retry_fresh: bool = False,
                       skip: frozenset = frozenset()):
        src = job.source
        base_key = hashlib.sha1(f"{src.key}|{ch.url}".encode()).hexdigest()
        keys = {f: f"{base_key}:{f}" for f in job.fmts}
        cached = {} if retry_fresh else {f: await self.db.get_file(k) for f, k in keys.items()}
        missing = [f for f in job.fmts if not cached.get(f)]

        caption_tpl = await self.db.get_cfg("caption", DEFAULT_CAPTION)
        name_tpl = await self.db.get_cfg("filename", DEFAULT_FILENAME)
        ctx = {"manga": esc(job.card.title), "chapter": esc(ch.title), "source": esc(src.name)}
        built: dict = {}
        thumb = None
        sent = set(skip)
        tmp = Path(Config.DOWNLOAD_DIR) / f"{job.id}_{abs(hash(ch.url)) % 10**8}"
        try:
            if missing:
                tmp.mkdir(parents=True, exist_ok=True)
                pages = await self._fetch_pages(job, ch, tmp, prog)
                await self._edit(job, head + "📦 Building files…")
                loop = asyncio.get_running_loop()
                fname = safe_filename(name_tpl.format(manga=job.card.title, chapter=ch.title,
                                                      source=src.name))
                title = f"{job.card.title} - {ch.title}"
                if "pdf" in missing:
                    built["pdf"] = await loop.run_in_executor(
                        None, files.write_pdf, pages, tmp / f"{fname}.pdf", title)
                if "cbz" in missing:
                    built["cbz"] = await loop.run_in_executor(None, files.write_cbz, pages, tmp / f"{fname}.cbz")
                thumb = await self._thumb(loop, pages, tmp)
                ctx["pages"] = str(len(pages))
            ctx.setdefault("pages", "")
            try:
                caption = caption_tpl.format(**ctx)
            except Exception:
                caption = DEFAULT_CAPTION.format(**ctx)

            await self._edit(job, head + "📤 Uploading…")
            for f in job.fmts:
                if f in sent:
                    continue
                if cached.get(f):
                    try:
                        await self.app.send_document(job.chat_id, cached[f], caption=caption)
                        sent.add(f)
                        continue
                    except FloodWait:
                        raise
                    except Exception as e:  # stale file_id -> rebuild once
                        log.warning("cached file_id invalid (%s), rebuilding", e)
                        await self.db.drop_file(keys[f])
                        if not retry_fresh:
                            return await self._process(job, ch, head, prog, retry_fresh=True,
                                                   skip=frozenset(sent))
                        raise JobError("could not send file")
                path = built[f]
                msg = await self.app.send_document(
                    job.chat_id, str(path), caption=caption, thumb=thumb, file_name=path.name,
                    force_document=True)
                sent.add(f)
                if msg and msg.document:
                    await self.db.save_file(keys[f], msg.document.file_id)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def _thumb(self, loop, pages, tmp: Path) -> Optional[str]:
        custom = Path(Config.DATA_DIR) / "thumb.jpg"
        if custom.exists():
            return str(custom)
        try:
            return str(await loop.run_in_executor(None, files.make_thumb, pages[0][0], tmp / "thumb.jpg"))
        except Exception:
            return None

    async def _fetch_pages(self, job: Job, ch: Chapter, tmp: Path, prog):
        urls = await job.source.pages(ch)
        if not urls:
            raise JobError("no images found (chapter removed, locked/paid, or site changed)")
        headers = job.source.image_headers(ch)
        wm = await self.db.get_cfg("watermark")
        sem = asyncio.Semaphore(Config.PAGE_CONCURRENCY)
        loop = asyncio.get_running_loop()
        results: list = [None] * len(urls)
        done = 0

        async def one(i: int, url: str):
            nonlocal done
            async with sem:
                if job.cancelled:
                    return
                try:
                    raw = await self.http.fetch_image(url, headers)
                    out = tmp / f"{i + 1:04d}.jpg"
                    w, h = await loop.run_in_executor(None, files.save_jpeg, raw, out, wm)
                    results[i] = (out, w, h)
                except Exception as e:
                    log.debug("page %s failed: %s", url, e)
                done += 1
                await prog(done, len(urls))

        await asyncio.gather(*(one(i, u) for i, u in enumerate(urls)))
        if job.cancelled:
            raise Cancelled()
        ok = [r for r in results if r]
        if len(ok) < max(1, math.ceil(len(urls) * 0.95)):
            raise JobError(f"only {len(ok)}/{len(urls)} pages could be downloaded")
        return ok
