"""Polite shared HTTP client with robots.txt checks and per-host throttling.

The scraper remains enabled for sources that permit automated access. It does not
try to evade bot challenges or access controls.
"""
import asyncio
import json as _json
import logging
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode, urlparse
from urllib.robotparser import RobotFileParser

from config import Config

log = logging.getLogger("http")
BOT_TOKEN = "MangaBot"
UA = f"{BOT_TOKEN}/1.0" + (f" (+mailto:{Config.CRAWLER_CONTACT})" if Config.CRAWLER_CONTACT else "")


class HttpError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def looks_like_cloudflare(status: int, headers, body: str) -> bool:
    if status not in (403, 429, 503):
        return False
    server = str(headers.get("Server", "")).lower()
    return ("cloudflare" in server or headers.get("cf-mitigated") is not None
            or "just a moment" in body[:4000].lower() or "cf-chl" in body[:8000].lower())


class Http:
    def __init__(self):
        self.session = None
        self._robots: Dict[str, Tuple[RobotFileParser, float]] = {}
        self._host_locks: Dict[str, asyncio.Lock] = {}
        self._host_next: Dict[str, float] = {}
        self._host_delay: Dict[str, float] = {}

    async def start(self):
        import aiohttp
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=aiohttp.ClientTimeout(total=40, connect=15),
            connector=aiohttp.TCPConnector(limit=30, limit_per_host=2),
        )

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def _robots_parser(self, url: str) -> RobotFileParser:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        cached = self._robots.get(origin)
        if cached and cached[1] > time.monotonic():
            return cached[0]

        robots_url = origin + "/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            async with self.session.get(robots_url, headers={"User-Agent": UA}, allow_redirects=True) as r:
                if r.status in (404, 410):
                    parser.parse([])  # no published rules
                elif r.status in (401, 403):
                    parser.parse(["User-agent: *", "Disallow: /"])
                elif 200 <= r.status < 300:
                    parser.parse((await r.text(errors="replace")).splitlines())
                else:
                    # Do not scrape when the site's policy file cannot be checked.
                    parser.parse(["User-agent: *", "Disallow: /"])
                    log.warning("robots.txt unavailable for %s (HTTP %s); pausing scraping", origin, r.status)
        except Exception as e:
            parser.parse(["User-agent: *", "Disallow: /"])
            log.warning("Could not read robots.txt for %s (%s); pausing scraping", origin, type(e).__name__)

        delay = 1.5
        try:
            crawl = parser.crawl_delay(BOT_TOKEN) or parser.crawl_delay("*")
            if crawl:
                delay = max(delay, float(crawl))
            rate = parser.request_rate(BOT_TOKEN) or parser.request_rate("*")
            if rate and rate.requests > 0:
                delay = max(delay, float(rate.seconds) / rate.requests)
        except (TypeError, ValueError):
            pass
        self._host_delay[origin] = min(delay, 30.0)
        self._robots[origin] = (parser, time.monotonic() + 6 * 3600)
        return parser

    async def _respect_site_policy(self, url: str):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HttpError("invalid URL", 400)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser = await self._robots_parser(url)
        if not parser.can_fetch(BOT_TOKEN, url):
            raise HttpError("Blocked by the site's robots.txt policy", 451)
        lock = self._host_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            wait = self._host_next.get(origin, now) - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_next[origin] = time.monotonic() + self._host_delay.get(origin, 1.5)

    # ── public API ───────────────────────────────────────────────
    async def text(self, url: str, *, method: str = "GET", params: Optional[dict] = None,
                   data: Optional[dict] = None, headers: Optional[dict] = None,
                   retries: int = 2) -> str:
        request_url = url if not params else f"{url}?{urlencode(params)}"
        last: Exception = HttpError("request failed")
        for attempt in range(retries + 1):
            try:
                await self._respect_site_policy(request_url)
                async with self.session.request(
                        method, url, params=params, data=data, headers=headers) as r:
                    body = await r.text(errors="replace")
                    if r.status == 200:
                        return body
                    if looks_like_cloudflare(r.status, r.headers, body):
                        raise HttpError("Site challenge/access restriction; not bypassed", r.status)
                    if r.status == 429:
                        retry = r.headers.get("Retry-After", "3")
                        try:
                            delay = min(max(int(retry), 1), 60)
                        except ValueError:
                            delay = 5
                        await asyncio.sleep(delay)
                        last = HttpError("rate limited", 429)
                        continue
                    if r.status >= 500:
                        last = HttpError(f"HTTP {r.status}", r.status)
                        await asyncio.sleep(1 + attempt)
                        continue
                    raise HttpError(f"HTTP {r.status}", r.status)
            except HttpError as e:
                if e.status and e.status < 500 and e.status != 429:
                    raise
                last = e
            except asyncio.TimeoutError:
                last = HttpError("timeout")
            except Exception as e:
                last = HttpError(f"{type(e).__name__}: {e}")
                await asyncio.sleep(0.5)
        raise last

    async def json(self, url: str, **kw):
        return _json.loads(await self.text(url, **kw))

    async def fetch_image(self, url: str, headers: Optional[dict] = None, retries: int = 3) -> bytes:
        last: Exception = HttpError("image failed")
        for attempt in range(retries):
            try:
                await self._respect_site_policy(url)
                async with self.session.get(url, headers=headers) as r:
                    if r.status == 200:
                        data = await r.read()
                        if len(data) > 500:
                            return data
                        last = HttpError("empty image")
                    elif r.status == 429:
                        await asyncio.sleep(min(3 + attempt * 2, 15))
                        last = HttpError("rate limited", 429)
                    elif r.status in (403, 404, 410):
                        raise HttpError(f"HTTP {r.status}", r.status)
                    else:
                        last = HttpError(f"HTTP {r.status}", r.status)
            except HttpError as e:
                if e.status in (403, 404, 410, 451):
                    raise
                last = e
            except Exception as e:
                last = HttpError(f"{type(e).__name__}: {e}")
            await asyncio.sleep(0.7 * (attempt + 1))
        raise last

    async def probe(self, url: str) -> Tuple[bool, str]:
        """Reachability check; follows the same robots and throttle policy."""
        try:
            await self._respect_site_policy(url)
            async with self.session.get(url, timeout=15, allow_redirects=True) as r:
                body = await r.text(errors="replace")
                if r.status < 400:
                    return True, "ok"
                if looks_like_cloudflare(r.status, r.headers, body):
                    return False, "Site challenge/access restriction"
                return False, f"HTTP {r.status}"
        except HttpError as e:
            return False, str(e)
        except asyncio.TimeoutError:
            return False, "timeout"
        except Exception as e:
            return False, type(e).__name__
