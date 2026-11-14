import secrets
import time
from collections import OrderedDict
from typing import Any, Optional


class SessionStore:
    """Short-token -> object store so callback_data stays under Telegram's 64 byte limit."""

    def __init__(self, ttl: int = 6 * 3600, max_items: int = 3000):
        self.ttl = ttl
        self.max_items = max_items
        self._items: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()

    def new(self, obj: Any) -> str:
        self._purge()
        token = secrets.token_hex(3)
        while token in self._items:
            token = secrets.token_hex(3)
        self._items[token] = (time.time(), obj)
        return token

    def get(self, token: str) -> Optional[Any]:
        item = self._items.get(token)
        if not item:
            return None
        ts, obj = item
        if time.time() - ts > self.ttl:
            self._items.pop(token, None)
            return None
        self._items[token] = (time.time(), obj)
        self._items.move_to_end(token)
        return obj

    def drop(self, token: str):
        self._items.pop(token, None)

    def _purge(self):
        now = time.time()
        for k in [k for k, (ts, _) in self._items.items() if now - ts > self.ttl]:
            self._items.pop(k, None)
        while len(self._items) >= self.max_items:
            self._items.popitem(last=False)
