import io
from pathlib import Path

import pytest
from PIL import Image

from clutchbot.render import browser
from clutchbot.render.browser import CardRenderer


def as_image(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    return image.convert("RGB")


async def test_renders_a_png_at_twice_the_card_size() -> None:
    async with CardRenderer() as renderer:
        png = await renderer.render("<body style='margin: 0; background: #202020'></body>")

    image = as_image(png)
    assert image.size == (2400, 1350)
    assert image.getpixel((1200, 675)) == (32, 32, 32)


async def test_renders_several_cards_with_one_browser() -> None:
    async with CardRenderer() as renderer:
        red = as_image(await renderer.render("<body style='background: #ff0000'></body>"))
        blue = as_image(await renderer.render("<body style='background: #0000ff'></body>"))

    assert red.getpixel((100, 100)) == (255, 0, 0)
    assert blue.getpixel((100, 100)) == (0, 0, 255)


async def test_loads_static_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "card.css").write_text("body { margin: 0; background: #00ff00 }")
    monkeypatch.setattr(browser, "STATIC_DIR", tmp_path)

    async with CardRenderer() as renderer:
        png = await renderer.render('<link rel="stylesheet" href="/static/card.css"><body></body>')

    assert as_image(png).getpixel((100, 100)) == (0, 255, 0)


async def test_other_hosts_are_blocked(caplog: pytest.LogCaptureFixture) -> None:
    html = '<body><img src="https://example.com/tracker.png"></body>'

    async with CardRenderer() as renderer:
        png = await renderer.render(html)

    assert as_image(png).size == (2400, 1350)
    assert "Blocked a request from a card: https://example.com/tracker.png" in caplog.text


COVER = "https://assets.ppy.sh/beatmaps/1/covers/cover.jpg"
# a full-card image over a grey placeholder background, like the cards use
IMAGE_CARD = (
    "<body style='margin: 0'>"
    "<div style='width: 1200px; height: 675px; background: #808080 center / cover "
    f'url("{COVER}")\'></div></body>'
)


def red_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_osu_images_come_from_the_image_source() -> None:
    requested: list[str] = []

    async def fake_images(url: str) -> tuple[bytes, str] | None:
        requested.append(url)
        return red_png(), "image/png"

    async with CardRenderer(fake_images) as renderer:
        png = await renderer.render(IMAGE_CARD)

    assert requested == [COVER]
    assert as_image(png).getpixel((1200, 675)) == (255, 0, 0)


async def test_missing_images_leave_the_placeholder() -> None:
    async def no_images(url: str) -> tuple[bytes, str] | None:
        return None

    async with CardRenderer(no_images) as renderer:
        failed = await renderer.render(IMAGE_CARD)
    async with CardRenderer() as renderer:  # no image source at all: never goes online
        offline = await renderer.render(IMAGE_CARD)

    assert as_image(failed).getpixel((1200, 675)) == (128, 128, 128)
    assert as_image(offline).getpixel((1200, 675)) == (128, 128, 128)


def test_read_static(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "card.css").write_text("body {}")
    (tmp_path / "secret.txt").write_text("nope")  # next to the folder, not in it
    monkeypatch.setattr(browser, "STATIC_DIR", static)

    assert browser._read_static("card.css") == (b"body {}", "text/css")
    assert browser._read_static("card.css?v=2") == (b"body {}", "text/css")
    assert browser._read_static("missing.css") is None
    assert browser._read_static("../secret.txt") is None


def text_card(font: str, weight: int = 500) -> str:
    return (
        '<link rel="stylesheet" href="/static/card.css">'
        # single quotes around the style, since font names are in double quotes
        f"<p style='font: {weight} 90px {font}'>Clutch 7-6 ąęł</p>"
    )


async def test_bundled_font_and_its_weights_are_used() -> None:
    async with CardRenderer() as renderer:
        exo = await renderer.render(text_card('"Exo 2"'))
        fallback = await renderer.render(text_card('"No Such Font", sans-serif'))
        regular = await renderer.render(text_card('"Exo 2"', 400))
        black = await renderer.render(text_card('"Exo 2"', 900))

    # if Exo 2 failed to load, both would fall back to the same default font
    assert exo != fallback
    # the weights come from the variable font
    assert regular != black


async def test_render_needs_async_with() -> None:
    with pytest.raises(RuntimeError, match="async with"):
        await CardRenderer().render("<body></body>")
