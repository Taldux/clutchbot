import asyncio
import logging
import mimetypes
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Playwright, Route, async_playwright

log = logging.getLogger(__name__)

CARD_WIDTH = 1200
CARD_HEIGHT = 675
SCALE = 2

# Cards are served from a made up origin so they can load the bundled static files by URL
ORIGIN = "https://card.clutchbot.invalid"
STATIC_DIR = Path(__file__).parent / "static"
# The only outside hosts a card may load from: osu! beatmap covers and avatars
ALLOWED_IMAGE_HOSTS = frozenset({"assets.ppy.sh", "a.ppy.sh"})
RENDER_TIMEOUT_MS = 15_000
SHUTDOWN_TIMEOUT_SECONDS = 10


ImageSource = Callable[[str], Awaitable[tuple[bytes, str] | None]]


class CardRenderer:
    """One Chromium for many cards"""

    def __init__(self, images: ImageSource | None = None) -> None:
        self._images = images
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> Self:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch()
        self._context = await self._browser.new_context(
            viewport={"width": CARD_WIDTH, "height": CARD_HEIGHT},
            device_scale_factor=SCALE,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._browser is not None:
            await _with_time_limit(self._browser.close(), "closing Chromium")
        if self._playwright is not None:
            await _with_time_limit(self._playwright.stop(), "stopping Playwright")

    async def render(self, html: str) -> bytes:
        if self._context is None:
            raise RuntimeError("CardRenderer must be used with `async with`")
        page = await self._context.new_page()
        try:

            async def handle(route: Route) -> None:
                await _answer(route, html, self._images)

            await page.route("**/*", handle)
            await page.goto(f"{ORIGIN}/", wait_until="networkidle", timeout=RENDER_TIMEOUT_MS)
            await page.evaluate("document.fonts.ready.then(() => true)")
            return await page.screenshot(type="png")
        finally:
            await page.close()


async def _with_time_limit(step: Coroutine[Any, Any, None], what: str) -> None:
    try:
        await asyncio.wait_for(step, SHUTDOWN_TIMEOUT_SECONDS)
    except TimeoutError:
        log.warning("Gave up %s after %d s", what, SHUTDOWN_TIMEOUT_SECONDS)
    except Exception as exc:
        log.warning("Error while %s: %s", what, exc)


async def _answer(route: Route, html: str, images: ImageSource | None) -> None:
    """The card itself, bundled static files, osu! images, everything else is blocked"""
    url = route.request.url
    host = urlsplit(url).hostname or ""
    if url == f"{ORIGIN}/":
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
    elif url.startswith(f"{ORIGIN}/static/"):
        await _serve_static(route, url.removeprefix(f"{ORIGIN}/static/"))
    elif host in ALLOWED_IMAGE_HOSTS:
        image = await images(url) if images is not None else None
        if image is None:
            await route.fulfill(status=404)  # fallback to placeholder
        else:
            body, content_type = image
            await route.fulfill(status=200, content_type=content_type, body=body)
    else:
        log.warning("Blocked a request from a card: %s", url)
        await route.abort()


async def _serve_static(route: Route, relative: str) -> None:
    found = await asyncio.to_thread(_read_static, relative)
    if found is None:
        await route.fulfill(status=404)
        return
    body, content_type = found
    await route.fulfill(status=200, content_type=content_type, body=body)


def _read_static(relative: str) -> tuple[bytes, str] | None:
    """A file from STATIC_DIR and its content type, None if missing or outside the folder"""
    static_dir = STATIC_DIR.resolve()
    path = (static_dir / urlsplit(relative).path).resolve()
    if not path.is_relative_to(static_dir) or not path.is_file():
        return None
    return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"
