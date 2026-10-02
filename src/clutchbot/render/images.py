import asyncio
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from clutchbot.osu.client import USER_AGENT
from clutchbot.osu.models import User
from clutchbot.render.browser import ALLOWED_IMAGE_HOSTS

log = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT_SECONDS = 10.0
MAX_IMAGE_BYTES = 5 * 1024 * 1024

_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


def avatar_url(user: User | None) -> str | None:
    if user is None:
        return None
    host = urlsplit(user.avatar_url).hostname
    return user.avatar_url if host in ALLOWED_IMAGE_HOSTS else None


def image_type(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for signature, content_type in _SIGNATURES:
        if data.startswith(signature):
            return content_type
    return None


class ImageCache:
    # image saved on disk

    def __init__(self, folder: Path) -> None:
        self._folder = folder
        self._http = httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
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

    async def get(self, url: str) -> tuple[bytes, str] | None:
        """Image's bytes and content types"""
        path = self._folder / hashlib.sha256(url.encode()).hexdigest()
        cached = await asyncio.to_thread(_read_if_exists, path)
        if cached is not None and (content_type := image_type(cached)) is not None:
            return cached, content_type

        data = await self._download(url)
        if data is None:
            return None
        content_type = image_type(data)
        if content_type is None:
            log.warning("Not an image, ignoring: %s", url)
            return None
        await asyncio.to_thread(_write_atomically, path, data)
        return data, content_type

    async def _download(self, url: str) -> bytes | None:
        try:
            response = await self._http.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Couldn't download image %s: %s", url, type(exc).__name__)
            return None
        if len(response.content) > MAX_IMAGE_BYTES:
            log.warning("Image too large (%d bytes), ignoring: %s", len(response.content), url)
            return None
        return response.content


def _read_if_exists(path: Path) -> bytes | None:
    if not path.is_file():
        return None
    path.touch()
    return path.read_bytes()


def prune_image_cache(folder: Path, unused_since: datetime) -> int:
    if not folder.is_dir():
        return 0
    cutoff = unused_since.timestamp()
    deleted = 0
    for path in folder.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            deleted += 1
    return deleted


def _write_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # a name of its own to have a temp copy
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.part")
    temporary.write_bytes(data)
    temporary.replace(path)
