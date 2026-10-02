import io
from dataclasses import replace

import pytest
from PIL import Image

from clutchbot.processing.pipeline import process_match
from clutchbot.processing.result import Team
from clutchbot.processing.tournament import TournamentName
from clutchbot.render import font_check
from clutchbot.render.browser import CardRenderer
from clutchbot.render.font_check import SAMPLES, check_fonts
from clutchbot.render.result_card import build_result_card, render_result_card, result_card_html
from tests.conftest import TEAM_VS_ID, load_detail


async def test_every_script_renders_as_text() -> None:
    async with CardRenderer() as renderer:
        results = await check_fonts(renderer)

    assert results == dict.fromkeys(SAMPLES, True)


async def test_a_result_card_with_japanese_korean_and_emoji_names() -> None:
    names = TournamentName("日韓", "日韓カップ 🔥", "チーム桜", "한국팀")
    match = process_match(load_detail(TEAM_VS_ID), names)
    match = replace(
        match,
        result=replace(
            match.result,
            red=Team("チーム桜", match.result.red.player_ids),
            blue=Team("한국팀 🔥", match.result.blue.player_ids),
        ),
    )

    html = result_card_html(build_result_card(match))
    async with CardRenderer() as renderer:
        png = await render_result_card(renderer, match)

    assert "チーム桜" in html
    assert "한국팀 🔥" in html
    assert "日韓カップ 🔥" in html
    assert Image.open(io.BytesIO(png)).size == (2400, 1350)


async def test_refuses_to_answer_if_boxes_cant_be_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    # pretend "missing" characters render as nothing: then the check can't tell
    monkeypatch.setattr(font_check, "_NO_FONT_HAS_THIS", "")

    async with CardRenderer() as renderer:
        with pytest.raises(RuntimeError, match="can't be detected"):
            await check_fonts(renderer)
