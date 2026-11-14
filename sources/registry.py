import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from .asura import AsuraSource
from .base import Source
from .kakalot import KakalotSource
from .madara import MadaraSource
from .mangadex import MangaDexSource
from .themesia import ThemesiaSource
from .weebcentral import WeebCentralSource

log = logging.getLogger("registry")

ENGINES = {
    "madara": MadaraSource,
    "themesia": ThemesiaSource,
    "mangadex": MangaDexSource,
    "weebcentral": WeebCentralSource,
    "kakalot": KakalotSource,
    "asura": AsuraSource,
}


class Registry:
    def __init__(self, http, sites_file: str):
        self.sources: Dict[str, Source] = {}
        data = json.loads(Path(sites_file).read_text(encoding="utf-8"))
        for cfg in data["sites"]:
            if cfg.get("enabled", True) is False:
                continue
            engine = ENGINES.get(cfg.get("engine"))
            if not engine:
                log.warning("sites.json: unknown engine %r for %s", cfg.get("engine"), cfg.get("key"))
                continue
            try:
                self.sources[cfg["key"]] = engine(http, cfg)
            except Exception as e:
                log.warning("sites.json: bad entry %s: %s", cfg.get("key"), e)
        self.order: List[str] = sorted(self.sources, key=lambda k: self.sources[k].name.lower())

    def get(self, key: str) -> Optional[Source]:
        return self.sources.get(key)

    def listing(self, category: str = "all", online_only: bool = False) -> List[Source]:
        """List only the configured non-adult sources."""
        out = []
        for k in self.order:
            s = self.sources[k]
            if category in ("manga", "manhwa") and s.category != category:
                continue
            if online_only and s.online is False:
                continue
            out.append(s)
        return out
