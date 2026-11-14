"""Generic engine for the WordPress 'Madara' theme (hundreds of manga/manhwa sites)."""
import json
import re
from typing import Dict, List, Tuple
from urllib.parse import quote_plus

from core.models import Card, Chapter
from core.utils import chapter_number
from .base import Source, soup_of, img_src, is_locked, og_description


class MadaraSource(Source):
    engine = "madara"

    def parse_search(self, html: str) -> List[Card]:
        soup = soup_of(html)
        cards = []
        rows = soup.select("div.c-tabs-item__content") or soup.select("div.page-item-detail")
        for row in rows:
            a = row.select_one("h3 a, h4 a, .post-title a, .tab-thumb a")
            if not a or not a.get("href"):
                continue
            title = (a.get("title") or a.get_text(strip=True)).strip()
            img = row.select_one("img")
            cards.append(Card(self.key, title, a["href"], self.abs(img_src(img)) if img and img_src(img) else None))
        return self.dedupe(cards)

    async def search(self, query: str) -> List[Card]:
        html = await self.http.text(f"{self.base}/?s={quote_plus(query)}&post_type=wp-manga")
        return self.parse_search(html)[:25]

    def parse_chapters(self, html: str) -> List[Chapter]:
        soup = soup_of(html)
        out = []
        for li in soup.select("li.wp-manga-chapter"):
            a = li.select_one("a")
            if not a or not a.get("href") or is_locked(li):
                continue
            href = a["href"].strip()
            if href in ("#", "") or href.startswith("javascript"):
                continue
            title = a.get_text(" ", strip=True)
            out.append(Chapter(title, href, chapter_number(title)))
        return out

    async def load(self, card: Card) -> Tuple[List[Chapter], Dict[str, str]]:
        html = await self.http.text(card.url)
        soup = soup_of(html)
        chapters = self.parse_chapters(html)
        if not chapters:
            chapters = await self._ajax_chapters(card, soup)
        desc_el = soup.select_one("div.summary__content, div.description-summary")
        status_el = soup.select_one(".post-status .summary-content")
        details = {
            "description": desc_el.get_text(" ", strip=True) if desc_el else og_description(soup),
            "status": status_el.get_text(strip=True) if status_el else "",
            "genres": ", ".join(a.get_text(strip=True) for a in soup.select(".genres-content a")[:6]),
        }
        if not card.cover:
            img = soup.select_one(".summary_image img")
            if img and img_src(img):
                card.cover = self.abs(img_src(img))
        return self.order_chapters(chapters), details

    async def _ajax_chapters(self, card: Card, soup) -> List[Chapter]:
        hdr = {"X-Requested-With": "XMLHttpRequest", "Referer": card.url}
        try:  # newer Madara
            html = await self.http.text(card.url.rstrip("/") + "/ajax/chapters/", method="POST", data={}, headers=hdr)
            found = self.parse_chapters(html)
            if found:
                return found
        except Exception:
            pass
        holder = soup.select_one("#manga-chapters-holder[data-id]")
        post_id = holder.get("data-id") if holder else None
        if not post_id:
            inp = soup.select_one("input.rating-post-id, input[name=manga_id]")
            post_id = inp.get("value") if inp else None
        if not post_id:
            m = re.search(r"postid-(\d+)", " ".join(soup.body.get("class", [])) if soup.body else "")
            post_id = m.group(1) if m else None
        if not post_id:
            return []
        html = await self.http.text(f"{self.base}/wp-admin/admin-ajax.php", method="POST",
                                    data={"action": "manga_get_chapters", "manga": post_id}, headers=hdr)
        return self.parse_chapters(html)

    def parse_pages(self, html: str, url: str) -> List[str]:
        soup = soup_of(html)
        imgs = soup.select("div.reading-content img, div.page-break img, #readerarea img")
        urls = [self.abs(s.strip(), url) for s in (img_src(i) for i in imgs) if s]
        if not urls:  # some themes ship the list as JSON
            m = re.search(r"chapter_preloaded_images\s*=\s*(\[.*?\])", html, re.S)
            if m:
                try:
                    urls = [self.abs(u, url) for u in json.loads(m.group(1))]
                except Exception:
                    pass
        return list(dict.fromkeys(urls))

    async def pages(self, chapter: Chapter) -> List[str]:
        html = await self.http.text(chapter.url, params={"style": "list"} if "?" not in chapter.url else None)
        return self.parse_pages(html, chapter.url)
