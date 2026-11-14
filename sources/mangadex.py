"""MangaDex – official public API. Chapters hosted elsewhere (external / paid links) are skipped."""
from typing import Dict, List, Tuple
from urllib.parse import quote

from core.models import Card, Chapter
from core.utils import chapter_number, fmt_num
from .base import Source

API = "https://api.mangadex.org"


def _title(titles: dict) -> str:
    return titles.get("en") or next(iter(titles.values()), "Unknown")


class MangaDexSource(Source):
    engine = "mangadex"

    def __init__(self, http, cfg):
        cfg = dict(cfg, url=API)
        super().__init__(http, cfg)
        self.lang = cfg.get("language", "en")
        self.web = "https://mangadex.org"

    def _ratings(self) -> str:
        # Strict safe-only mode: exclude suggestive, erotica and pornographic titles.
        r = ["safe"]
        return "".join(f"&contentRating[]={x}" for x in r)

    def parse_search(self, data: dict) -> List[Card]:
        out = []
        for m in data.get("data", []):
            attrs = m["attributes"]
            cover = None
            for rel in m.get("relationships", []):
                if rel["type"] == "cover_art" and rel.get("attributes", {}).get("fileName"):
                    cover = f"https://uploads.mangadex.org/covers/{m['id']}/{rel['attributes']['fileName']}.512.jpg"
            desc = (attrs.get("description") or {}).get("en", "")
            out.append(Card(self.key, _title(attrs["title"]), f"{self.web}/title/{m['id']}", cover,
                            {"id": m["id"], "description": desc, "status": attrs.get("status", "")}))
        return out

    async def search(self, query: str) -> List[Card]:
        url = (f"{API}/manga?limit=25&title={quote(query)}&includes[]=cover_art&order[relevance]=desc"
               f"&availableTranslatedLanguage[]={self.lang}{self._ratings()}")
        return self.parse_search(await self.http.json(url))

    def parse_feed(self, items: List[dict]) -> List[Chapter]:
        seen, out = set(), []
        for c in items:
            a = c["attributes"]
            if a.get("externalUrl") or not a.get("pages"):
                continue  # hosted on official/paid sites or empty
            num = a.get("chapter")
            if num in seen and num is not None:
                continue  # keep first scanlation group only
            seen.add(num)
            try:
                number = float(num) if num is not None else None
            except ValueError:
                number = chapter_number(str(num))
            label = f"Chapter {num}" if num else "Oneshot"
            if a.get("title"):
                label += f" - {a['title']}"
            out.append(Chapter(label, f"{self.web}/chapter/{c['id']}", number, {"id": c["id"]}))
        return out

    async def load(self, card: Card) -> Tuple[List[Chapter], Dict[str, str]]:
        mid = card.extra.get("id") or card.url.rsplit("/", 1)[-1]
        items, offset = [], 0
        while True:
            url = (f"{API}/manga/{mid}/feed?limit=500&offset={offset}&translatedLanguage[]={self.lang}"
                   f"&order[chapter]=asc&includeExternalUrl=0{self._ratings()}")
            data = await self.http.json(url)
            items += data.get("data", [])
            offset += 500
            if offset >= data.get("total", 0) or not data.get("data"):
                break
        details = {"description": card.extra.get("description", ""), "status": card.extra.get("status", ""),
                   "genres": ""}
        return self.order_chapters(self.parse_feed(items)), details

    async def pages(self, chapter: Chapter) -> List[str]:
        data = await self.http.json(f"{API}/at-home/server/{chapter.extra['id']}")
        base, h = data["baseUrl"], data["chapter"]["hash"]
        return [f"{base}/data/{h}/{f}" for f in data["chapter"]["data"]]

    def image_headers(self, chapter):
        return {}
