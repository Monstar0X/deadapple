"""Conservative metadata filters for explicitly sexual/adult-labelled titles."""
import re

# These are classification hints, not a promise that an unlabelled source is safe.
_BLOCKED = re.compile(
    r"(?i)(?<![a-z0-9])(?:adult|18\s*\+|nsfw|hentai|porn(?:ographic)?|xxx|"
    r"erotica|erotic|ecchi|smut|explicit|nudity|sexual\s+content)(?![a-z0-9])"
)


def is_safe_title(title: str) -> bool:
    return not bool(_BLOCKED.search(title or ""))


def is_safe_metadata(details: dict) -> bool:
    values = []
    for key in ("genres", "tags", "description"):
        value = details.get(key, "")
        if isinstance(value, (list, tuple, set)):
            values.extend(str(x) for x in value)
        else:
            values.append(str(value or ""))
    return not any(_BLOCKED.search(value) for value in values)
