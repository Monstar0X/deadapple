from dataclasses import dataclass, field
from typing import Optional


@dataclass(eq=False)
class Card:
    """A search result (one manga / manhwa)."""
    source_key: str
    title: str
    url: str
    cover: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass(eq=False)
class Chapter:
    title: str
    url: str
    number: Optional[float] = None
    extra: dict = field(default_factory=dict)
