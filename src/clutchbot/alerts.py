import logging
from collections.abc import Sequence
from enum import IntEnum
from types import TracebackType
from typing import Self
from urllib.parse import urlsplit

import httpx

from clutchbot.osu.client import USER_AGENT

log = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 10.0


class Priority(IntEnum):
    MIN = 1
    LOW = 2
    DEFAULT = 3
    HIGH = 4
    URGENT = 5


def split_topic_url(topic_url: str) -> tuple[str, str]:
    parts = urlsplit(topic_url)
    topic = parts.path.strip("/")
    if parts.scheme not in ("http", "https") or not parts.netloc or not topic or "/" in topic:
        raise ValueError("NTFY_URL must look like https://ntfy.sh/<topic>")
    return f"{parts.scheme}://{parts.netloc}", topic


class Alerter:
    # without topic, alerts are never sent

    def __init__(self, topic_url: str | None) -> None:
        self._target = split_topic_url(topic_url) if topic_url else None
        self._http = httpx.AsyncClient(
            timeout=SEND_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    @property
    def enabled(self) -> bool:
        return self._target is not None

    async def send(
        self,
        title: str,
        message: str,
        *,
        priority: Priority = Priority.DEFAULT,
        tags: Sequence[str] = (),
    ) -> None:
        level = logging.WARNING if priority >= Priority.HIGH else logging.INFO
        log.log(level, "Alert: %s: %s", title, message)
        if self._target is None:
            return
        server, topic = self._target
        # JSON body, since headers can't hold non-Latin text like some lobby names
        body = {
            "topic": topic,
            "title": title,
            "message": message,
            "priority": int(priority),
            "tags": list(tags),
        }
        try:
            response = await self._http.post(f"{server}/", json=body)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Couldn't send the alert to ntfy: %s", type(exc).__name__)
