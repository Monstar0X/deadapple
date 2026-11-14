"""Generic engine for the WordPress 'MangaThemesia' theme."""
import json
import re
from typing import Dict, List, Tuple
from urllib.parse import quote_plus

from core.models import Card, Chapter
from core.utils import chapter_number
from .base import Source, soup_of, img_src, is_locked, og_description


class ThemesiaSource(Source):
    engine = "themesia"

    def parse_search(self, html: str) -> List[Card]:
        soup = soup_of(html)
        cards = []
        for box in soup.select("div.listupd div.bs, div.bsx, div.utao"):
            a = box.select_one("a[href]")
            if not a:
                continue
            title_el = box.select_one(".tt, h2, .title")
            title = (a.get("title") or (title_el.get_text(strip=True) if title_el else "") or a.get_text(strip=True)).strip()
            img = box.select_one("img")
            if title and "/" in a["href"]:
                cards.append(Card(self.key, title, a["href"],
                                  self.abs(img_src(img)) if img and img_src(img) else None))
        return self.dedupe(cards)

    async def search(self, query: str) -> List[Card]:
        html = await self.http.text(f"{self.base}/?s={quote_plus(query)}")
        return self.parse_search(html)[:25]

    def parse_chapters(self, html: str) -> List[Chapter]:
        soup = soup_of(html)
        out = []
        for li in soup.select("#chapterlist li, div.eplister li"):
            a = li.select_one("a[href]")
            if not a or is_locked(li):
                continue
            num = li.get("data-num") or ""
            name_el = li.select_one(".chapternum, .epl-num")
            title = (name_el.get_text(" ", strip=True) if name_el else a.get_text(" ", strip=True)).strip()
            try:
                number = float(num) if num else chapter_number(title)
            except ValueError:
                number = chapter_number(title)
            out.append(Chapter(title, a["href"], number))
        return out

    async def load(self, card: Card) -> Tuple[List[Chapter], Dict[str, str]]:
        html = await self.http.text(card.url)
        soup = soup_of(html)
        desc = soup.select_one("div.entry-content[itemprop=description], .synp .entry-content")
        status = soup.select_one(".imptdt i, .tsinfo .imptdt")
        if not card.cover:
            img = soup.select_one(".thumb img")
            if img and img_src(img):
                card.cover = self.abs(img_src(img))
        details = {
            "description": desc.get_text(" ", strip=True) if desc else og_description(soup),
            "status": status.get_text(strip=True) if status else "",
            "genres": ", ".join(a.get_text(strip=True) for a in soup.select(".mgen a")[:6]),
        }
        return self.order_chapters(self.parse_chapters(html)), details

    def parse_pages(self, html: str, url: str) -> List[str]:
        m = re.search(r"ts_reader\.run\((\{.*?\})\)\s*;", html, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
                images = data["sources"][0]["images"]
                if images:
                    return [self.abs(u, url) for u in images]
            except Exception:
                pass
        soup = soup_of(html)
        urls = [self.abs(s, url) for s in (img_src(i) for i in soup.select("#readerarea img")) if s]
        return list(dict.fromkeys(urls))

    async def pages(self, chapter: Chapter) -> List[str]:
        return self.parse_pages(await self.http.text(chapter.url), chapter.url)
