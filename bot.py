import asyncio
import logging
import signal

from aiohttp import web
from pyrogram import idle

from config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("main")

# One event loop for everything (Motor + Pyrogram bind to the loop that exists at import time).
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from ctx import access, app, db, http, jobs, registry  # noqa: E402  (validates config on import)
import handlers.common, handlers.start, handlers.settings, handlers.search, handlers.manga, handlers.admin  # noqa: E402,F401,E501
from handlers.admin import check_all_sites  # noqa: E402


async def health_loop():
    while True:
        try:
            await check_all_sites()
            on = sum(1 for s in registry.sources.values() if s.online)
            log.info("site check: %d/%d online", on, len(registry.sources))
        except Exception:
            log.exception("health loop")
        await asyncio.sleep(Config.HEALTH_INTERVAL)


async def web_server() -> web.AppRunner:
    async def ok(_):
        return web.Response(text="MangaBot is running")
    wa = web.Application()
    wa.router.add_get("/", ok)
    wa.router.add_get("/health", ok)
    runner = web.AppRunner(wa)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", Config.PORT).start()
    return runner


async def main():
    await http.start()
    await db.init()
    await access.load()
    runner = await web_server()
    await jobs.start()
    await app.start()
    me = await app.get_me()
    log.info("Started as @%s with %d sites", me.username, len(registry.sources))
    health = asyncio.create_task(health_loop())
    try:
        await idle()
    finally:
        health.cancel()
        await jobs.stop()
        await app.stop()
        await http.close()
        await runner.cleanup()


if __name__ == "__main__":
    loop.run_until_complete(main())
