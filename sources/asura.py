"""AsuraScans (Next.js site). Premium/locked chapters have no images and are skipped."""
import re
from typing import Dict, List, Tuple
from urllib.parse import quote_plus

from core.models import Card, Chapter
from core.utils import chapter_number
from .base import Source, soup_of, img_src, is_locked, og_description

_IMG_RE = re.compile(r'https://[^"\'\\\s]+/storage/media/\d+/[^"\'\\\s]+?\.(?:webp|jpg|jpeg|png)', re.I)


class AsuraSource(Source):
    engine = "asura"

    def parse_search(self, html: str) -> List[Card]:
        soup = soup_of(html)
        out = []
        for a in soup.find_all("a", href=re.compile(r"series/[^/]+/?$")):
            t = a.select_one("span.block, span.font-bold, h3")
            img = a.select_one("img")
            title = (t.get_text(strip=True) if t else (img.get("alt") if img else "")) or ""
            if title:
                out.append(Card(self.key, title.strip(), self.abs(a["href"]),
                                self.abs(img_src(img)) if img and img_src(img) else None))
        return self.dedupe(out)

    async def search(self, query: str) -> List[Card]:
        return self.parse_search(await self.http.text(f"{self.base}/series?page=1&name={quote_plus(query)}"))[:25]

    def parse_chapters(self, html: str, series_url: str) -> List[Chapter]:
        soup = soup_of(html)
        out = []
        for a in soup.find_all("a", href=re.compile(r"/chapter/[\w.\-]+/?$")):
            if is_locked(a):
                continue
            href = self.abs(a["href"], series_url)
            m = re.search(r"/chapter/([\w.\-]+)", href)
            num = chapter_number(m.group(1)) if m else None
            out.append(Chapter(f"Chapter {m.group(1)}" if m else a.get_text(strip=True), href, num))
        return out

    async def load(self, card: Card) -> Tuple[List[Chapter], Dict[str, str]]:
        html = await self.http.text(card.url)
        soup = soup_of(html)
        return self.order_chapters(self.parse_chapters(html, card.url)), \
            {"description": og_description(soup), "status": "", "genres": ""}

    def parse_pages(self, html: str) -> List[str]:
        soup = soup_of(html)
        urls = [img_src(i) for i in soup.find_all("img", alt=re.compile(r"chapter page", re.I))]
        urls = [u for u in urls if u]
        if not urls:
            urls = _IMG_RE.findall(html.replace("\\/", "/"))
        return list(dict.fromkeys(urls))

    async def pages(self, chapter: Chapter) -> List[str]:
        return self.parse_pages(await self.http.text(chapter.url))
