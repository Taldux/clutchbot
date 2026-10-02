"""Turn link or id into match id"""

import re

_PLAIN_ID = re.compile(r"^(?:mp\s+)?(?P<id>\d+)$", re.IGNORECASE)
_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?osu\.ppy\.sh/(?:community/matches|matches|mp)/(?P<id>\d+)"
    r"/?(?:[?#].*)?$",
    re.IGNORECASE,
)


class MatchLinkError(ValueError):
    pass


def parse_match_id(text: str) -> int:
    """Accepts `117358530`, `mp 117358530`, `osu.ppy.sh/mp/117358530` and
    `https://osu.ppy.sh/community/matches/117358530`
    """
    cleaned = text.strip()
    found = _PLAIN_ID.match(cleaned) or _URL.match(cleaned)
    if found is None:
        raise MatchLinkError(f"Not a match id or osu! match link: {text!r}")
    match_id = int(found["id"])
    if match_id == 0:
        raise MatchLinkError(f"Match id can't be 0: {text!r}")
    return match_id
