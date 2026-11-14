"""MangaKakalot / Manganato / Manganelo style sites."""
from typing import Dict, List, Tuple
from urllib.parse import quote

from core.models import Card, Chapter
from core.utils import chapter_number
from .base import Source, soup_of, img_src, og_description


class KakalotSource(Source):
    engine = "kakalot"

    def parse_search(self, html: str) -> List[Card]:
        soup = soup_of(html)
        out = []
        for item in soup.select("div.story_item, div.search-story-item, div.list-truyen-item-wrap, div.itemupdate"):
            a = item.select_one("h3 a, a.item-img, a.tooltip, a[href]")
            if not a or not a.get("href"):
                continue
            title = (a.get("title") or a.get_text(strip=True)).strip()
            if not title:
                t = item.select_one("h3, .story_name")
                title = t.get_text(strip=True) if t else ""
            img = item.select_one("img")
            if title:
                out.append(Card(self.key, title, self.abs(a["href"]),
                                self.abs(img_src(img)) if img and img_src(img) else None))
        return self.dedupe(out)

    async def search(self, query: str) -> List[Card]:
        slug = quote(query.strip().replace(" ", "_").lower())
        for path in (f"/search/story/{slug}", f"/search/{slug}"):
            try:
                found = self.parse_search(await self.http.text(self.base + path))
                if found:
                    return found[:25]
            except Exception:
                continue
        return []

    def parse_chapters(self, html: str) -> List[Chapter]:
        soup = soup_of(html)
        out = []
        for a in soup.select("div.chapter-list a, ul.row-content-chapter a, .chapter-list li a"):
            if a.get("href"):
                title = a.get_text(" ", strip=True)
                out.append(Chapter(title, self.abs(a["href"]), chapter_number(title)))
        return out

    async def load(self, card: Card) -> Tuple[List[Chapter], Dict[str, str]]:
        html = await self.http.text(card.url)
        soup = soup_of(html)
        desc = soup.select_one("#contentBox, #panel-story-info-description, #noidungm")
        if not card.cover:
            img = soup.select_one(".manga-info-pic img, .info-image img")
            if img and img_src(img):
                card.cover = self.abs(img_src(img))
        details = {"description": desc.get_text(" ", strip=True) if desc else og_description(soup),
                   "status": "", "genres": ""}
        return self.order_chapters(self.parse_chapters(html)), details

    def parse_pages(self, html: str, url: str) -> List[str]:
        soup = soup_of(html)
        imgs = soup.select("div.container-chapter-reader img, div.reading-content img, div.read-content img")
        return list(dict.fromkeys(self.abs(s, url) for s in (img_src(i) for i in imgs) if s))

    async def pages(self, chapter: Chapter) -> List[str]:
        return self.parse_pages(await self.http.text(chapter.url), chapter.url)
