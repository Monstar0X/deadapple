import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from core.models import Card, Chapter

LAZY_ATTRS = ("data-src", "data-lazy-src", "data-original", "data-cfsrc", "data-url", "src")
_PLACEHOLDER = re.compile(r"(placeholder|loading|lazy\.|spinner|blank\.|pixel\.)", re.I)
# Chapters flagged like this are paid / coin / premium -> never offered.
_LOCK_WORDS = ("premium", "locked", "paid", "coin", "vip", "lockchapter")


def soup_of(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def img_src(tag) -> Optional[str]:
    for attr in LAZY_ATTRS:
        v = (tag.get(attr) or "").strip()
        if v and not v.startswith("data:") and not _PLACEHOLDER.search(v):
            return v
    srcset = (tag.get("data-srcset") or tag.get("srcset") or "").strip()
    if srcset:
        return srcset.split(",")[0].split()[0]
    return None


def is_locked(el) -> bool:
    """True for paid / premium / coin-locked chapter entries."""
    classes = " ".join(el.get("class", [])).lower()
    if any(w in classes for w in _LOCK_WORDS):
        return True
    if "🔒" in el.get_text():
        return True
    return bool(el.select_one(".premium, .locked, .fa-lock, .lock-icon, .chapter-coin, .coin"))


def og_description(soup) -> str:
    m = soup.find("meta", attrs={"property": "og:description"}) or soup.find("meta", attrs={"name": "description"})
    return (m.get("content") or "").strip() if m else ""


class Source:
    """One website. Subclasses implement search / load / pages."""
    engine = "base"

    def __init__(self, http, cfg: dict):
        self.http = http
        self.key: str = cfg["key"]
        self.name: str = cfg["name"]
        self.base: str = cfg["url"].rstrip("/")
        self.category: str = cfg.get("category", "manga")
        self.cfg = cfg
        self.online: Optional[bool] = None   # None = not checked yet
        self.reason: str = ""

    # ── to implement ──────────────────────────────────────────
    async def search(self, query: str) -> List[Card]:
        raise NotImplementedError

    async def load(self, card: Card) -> Tuple[List[Chapter], Dict[str, str]]:
        """-> (chapters oldest→newest, details {description,status,genres})"""
        raise NotImplementedError

    async def pages(self, chapter: Chapter) -> List[str]:
        raise NotImplementedError

    # ── helpers ───────────────────────────────────────────────
    def abs(self, url: str, base: Optional[str] = None) -> str:
        if url.startswith("//"):
            return "https:" + url
        return urljoin((base or self.base) + "/", url)

    def image_headers(self, chapter: Chapter) -> Dict[str, str]:
        return {"Referer": chapter.url, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}

    async def check(self) -> bool:
        ok, reason = await self.http.probe(self.base if "api." not in self.base else self.base + "/ping")
        self.online, self.reason = ok, reason
        return ok

    @staticmethod
    def dedupe(items: List[Card]) -> List[Card]:
        seen, out = set(), []
        for c in items:
            if c.url not in seen:
                seen.add(c.url)
                out.append(c)
        return out

    @staticmethod
    def order_chapters(chapters: List[Chapter]) -> List[Chapter]:
        """Oldest -> newest. Sorts by number when most have one, else reverses page order
        (sites list newest first)."""
        seen, uniq = set(), []
        for c in chapters:
            if c.url not in seen:
                seen.add(c.url)
                uniq.append(c)
        numbered = [c for c in uniq if c.number is not None]
        if len(numbered) >= max(1, len(uniq) * 0.7):
            return sorted(uniq, key=lambda c: (c.number is None, c.number or 0))
        return list(reversed(uniq))
