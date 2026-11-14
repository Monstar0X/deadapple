import html
import re
from typing import List, Optional, Sequence, Tuple

_BAD_FS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def trunc(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: max(1, n - 1)].rstrip() + "…"


def safe_filename(name: str, max_len: int = 100) -> str:
    name = _BAD_FS.sub("", name or "").strip(" .")
    name = re.sub(r"\s+", " ", name)
    return (name[:max_len].rstrip(" .") or "file")


def chapter_number(title: str) -> Optional[float]:
    """Best-effort chapter number from a title like 'Chapter 12.5 - Foo'."""
    m = re.search(r"(?:chapter|chap|ch|episode|ep)\.?\s*(\d+(?:\.\d+)?)", title or "", re.I)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)", title or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def fmt_num(n: Optional[float]) -> str:
    if n is None:
        return "?"
    return str(int(n)) if float(n).is_integer() else str(n)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def progress_bar(done: int, total: int, width: int = 10) -> str:
    total = max(total, 1)
    filled = int(width * min(done, total) / total)
    return "▰" * filled + "▱" * (width - filled) + f" {int(100 * min(done, total) / total)}%"


def parse_spec(spec: str) -> Optional[List[Tuple[float, float]]]:
    """'3,5,8-10' -> [(3,3),(5,5),(8,10)]. Returns None if invalid."""
    spec = (spec or "").strip().replace(" ", "")
    if not spec or not re.fullmatch(r"\d+(\.\d+)?(-\d+(\.\d+)?)?(,\d+(\.\d+)?(-\d+(\.\d+)?)?)*", spec):
        return None
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            a, b = float(a), float(b)
            if a > b:
                a, b = b, a
            out.append((a, b))
        else:
            out.append((float(part), float(part)))
    return out


def select_by_spec(chapters: Sequence, spec: str) -> Optional[list]:
    """Pick chapters (ascending list) by chapter number; falls back to
    1-based positions when the site gives no usable numbers."""
    ranges = parse_spec(spec)
    if ranges is None:
        return None
    numbered = sum(1 for c in chapters if c.number is not None)
    use_numbers = numbered >= max(1, len(chapters) // 2)
    picked = []
    for i, ch in enumerate(chapters, 1):
        value = ch.number if use_numbers else float(i)
        if value is None:
            continue
        if any(a <= value <= b for a, b in ranges):
            picked.append(ch)
    return picked
