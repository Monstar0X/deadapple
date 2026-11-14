import re
from typing import Dict, List, Tuple
from urllib.parse import quote

from core.models import Card, Chapter
from core.utils import chapter_number
from .base import Source, soup_of, img_src, og_description

HX = {"HX-Request": "true", "X-Requested-With": "XMLHttpRequest"}
_BAD = ("logo", "avatar", "icon", "banner", "loader", "broken_image")


class WeebCentralSource(Source):
    engine = "weebcentral"

    def parse_search(self, html: str) -> List[Card]:
        soup = soup_of(html)
        out = []
        for a in soup.select('a[href*="/series/"]'):
            href = a["href"]
            title = (a.get("title") or "").strip()
            if not title:
                t = a.select_one(".line-clamp-1, .line-clamp-2, span")
                title = (t or a).get_text(" ", strip=True)
            if not title:
                continue
            m = re.search(r"/series/([0-9A-Z]+)", href)
            cover = None
            img = a.select_one("img")
            if img and img_src(img):
                cover = self.abs(img_src(img))
            elif m:
                cover = f"https://temp.compsci88.com/cover/normal/{m.group(1)}.webp"
            out.append(Card(self.key, title, href, cover, {"sid": m.group(1) if m else None}))
        return self.dedupe(out)

    async def search(self, query: str) -> List[Card]:
        url = (f"{self.base}/search/data?text={quote(query)}&sort=Best%20Match&order=Descending"
               f"&official=Any&anime=Any&adult=No&display_mode=Full%20Display&limit=24&offset=0")
        return self.parse_search(await self.http.text(url, headers=HX))[:25]

    def parse_chapters(self, html: str) -> List[Chapter]:
        soup = soup_of(html)
        out = []
        for a in soup.select('a[href*="/chapters/"]'):
            span = a.select_one("span.grow span, span")
            title = (span or a).get_text(" ", strip=True)
            if not title or "chapter" not in title.lower() and not re.search(r"\d", title):
                continue
            out.append(Chapter(title, a["href"], chapter_number(title)))
        return out

    async def load(self, card: Card) -> Tuple[List[Chapter], Dict[str, str]]:
        sid = card.extra.get("sid") or re.search(r"/series/([^/]+)", card.url).group(1)
        html = await self.http.text(f"{self.base}/series/{sid}/full-chapter-list", headers=HX)
        details = {"description": "", "status": "", "genres": ""}
        try:
            page = soup_of(await self.http.text(card.url))
            details["description"] = og_description(page)
        except Exception:
            pass
        return self.order_chapters(self.parse_chapters(html)), details

    def parse_pages(self, html: str) -> List[str]:
        urls = []
        for img in soup_of(html).find_all("img"):
            src = img_src(img)
            if not src or any(b in src.lower() for b in _BAD):
                continue
            urls.append(self.abs(src))
        return list(dict.fromkeys(urls))

    async def pages(self, chapter: Chapter) -> List[str]:
        await self.http.text(chapter.url)  # warms cookies
        url = chapter.url.rstrip("/") + "/images?is_prev=False&current_page=1&reading_style=long_strip"
        return self.parse_pages(await self.http.text(url, headers={**HX, "Referer": chapter.url}))
