import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image

from clutchbot.osu.models import User
from clutchbot.render import images as images_module
from clutchbot.render.images import ImageCache, avatar_url, image_type, prune_image_cache

COVER_URL = "https://assets.ppy.sh/beatmaps/2333834/covers/cover.jpg?1741054285"


def image_bytes(color: tuple[int, int, int], image_format: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, format=image_format)
    return buffer.getvalue()


# file checks live in plain functions: blocking disk calls aren't allowed in async code
def files_in(folder: Path) -> list[Path]:
    return list(folder.iterdir())


def corrupt_the_only_file(folder: Path) -> None:
    (cached_file,) = folder.iterdir()
    cached_file.write_bytes(b"garbage")


def make_everything_old(folder: Path, days: int) -> None:
    old = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    for path in folder.iterdir():
        os.utime(path, (old, old))


async def test_a_cache_hit_keeps_the_image_from_being_pruned(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.get(COVER_URL).mock(return_value=httpx.Response(200, content=image_bytes((1, 1, 1))))
    async with ImageCache(tmp_path) as cache:
        await cache.get(COVER_URL)
    make_everything_old(tmp_path, days=40)

    async with ImageCache(tmp_path) as cache:
        await cache.get(COVER_URL)  # used again today

    assert prune_image_cache(tmp_path, datetime.now(UTC) - timedelta(days=30)) == 0
    assert len(files_in(tmp_path)) == 1


def test_prune_image_cache(tmp_path: Path) -> None:
    (tmp_path / "old").write_bytes(b"x")
    make_everything_old(tmp_path, days=40)
    (tmp_path / "new").write_bytes(b"x")

    assert prune_image_cache(tmp_path, datetime.now(UTC) - timedelta(days=30)) == 1
    assert [p.name for p in files_in(tmp_path)] == ["new"]
    assert prune_image_cache(tmp_path / "missing", datetime.now(UTC)) == 0


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        *(
            (image_bytes((1, 2, 3), image_format), f"image/{image_format.lower()}")
            for image_format in ("PNG", "JPEG", "GIF", "WEBP")
        ),
        (b"<html>Not found</html>", None),
        (b"", None),
    ],
)
def test_image_type(data: bytes, content_type: str | None) -> None:
    assert image_type(data) == content_type


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://a.ppy.sh/8640970?1789222097.jpeg", "https://a.ppy.sh/8640970?1789222097.jpeg"),
        ("https://osu.ppy.sh/images/layout/avatar-guest@2x.png", None),  # guest picture
        ("https://example.com/me.png", None),
        (None, None),  # no user at all
    ],
)
def test_avatar_url(url: str | None, expected: str | None) -> None:
    user = None if url is None else User(id=1, username="x", avatar_url=url, country_code="NL")

    assert avatar_url(user) == expected


async def test_downloads_once_then_uses_the_disk(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    jpeg = image_bytes((255, 0, 0), "JPEG")
    route = respx_mock.get(COVER_URL).mock(return_value=httpx.Response(200, content=jpeg))

    async with ImageCache(tmp_path) as cache:
        first = await cache.get(COVER_URL)
    async with ImageCache(tmp_path) as cache:  # a new run, same folder
        second = await cache.get(COVER_URL)

    assert first == second == (jpeg, "image/jpeg")
    assert route.call_count == 1
    assert len(files_in(tmp_path)) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        httpx.Response(404),
        httpx.Response(500),
        httpx.Response(200, content=b"<html>Not an image</html>"),
        httpx.ConnectTimeout("slow"),
    ],
)
async def test_failures_return_none_and_are_not_cached(
    respx_mock: respx.MockRouter, tmp_path: Path, outcome: httpx.Response | Exception
) -> None:
    respx_mock.get(COVER_URL).mock(side_effect=[outcome])

    async with ImageCache(tmp_path) as cache:
        assert await cache.get(COVER_URL) is None

    assert files_in(tmp_path) == []


async def test_too_large_images_are_refused(
    respx_mock: respx.MockRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(images_module, "MAX_IMAGE_BYTES", 10)
    respx_mock.get(COVER_URL).mock(return_value=httpx.Response(200, content=image_bytes((0, 0, 0))))

    async with ImageCache(tmp_path) as cache:
        assert await cache.get(COVER_URL) is None


async def test_a_broken_cache_file_is_downloaded_again(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    png = image_bytes((0, 255, 0))
    route = respx_mock.get(COVER_URL).mock(return_value=httpx.Response(200, content=png))
    async with ImageCache(tmp_path) as cache:
        await cache.get(COVER_URL)
    corrupt_the_only_file(tmp_path)

    async with ImageCache(tmp_path) as cache:
        assert await cache.get(COVER_URL) == (png, "image/png")
    assert route.call_count == 2
