import io
from dataclasses import replace

from PIL import Image

from clutchbot.processing.clutch import ClutchMap
from clutchbot.processing.pipeline import ProcessedMatch, process_match
from clutchbot.processing.tournament import TournamentName
from clutchbot.render.browser import CardRenderer
from clutchbot.render.clutch_card import build_clutch_card, clutch_card_html, render_clutch_cards
from tests.conftest import HEAD_TO_HEAD_ID, TEAM_VS_ID, WARMUP_MATCH_ID, load_detail

NAMES = {
    HEAD_TO_HEAD_ID: TournamentName("DBHS", "DBHS Tournament", "NINERIK", "Welter"),
    TEAM_VS_ID: TournamentName("CRTOA", "CRTOA Tournament", "Furball Pups", "Junior Sleuth Teds"),
    WARMUP_MATCH_ID: TournamentName("BLB", "BLB Tournament", "Hessen", "Berlin"),
}


def processed(match_id: int) -> ProcessedMatch:
    return process_match(load_detail(match_id), NAMES[match_id])


def clutch_at(match: ProcessedMatch, map_number: int) -> ClutchMap:
    return next(c for c in match.clutches if c.map_number == map_number)


def test_head_to_head_clutch_card() -> None:
    match = processed(HEAD_TO_HEAD_ID)

    card = build_clutch_card(match, clutch_at(match, 10))

    assert (card.map_number, card.maps_played) == (10, 10)
    assert (card.title, card.artist, card.version) == ("Ragnarok", "Gram VS Camellia", "Desolation")
    assert (card.stars, card.mods) == ("8.68", ["HR"])
    assert card.cover_url is not None
    assert card.cover_url.endswith("/covers/cover@2x.jpg?1772657500")
    assert (card.margin, card.winner_name) == ("0.63%", "Welter")
    assert (card.red.name, card.red.total, card.red.is_winner) == ("NINERIK", "340,567", False)
    assert (card.blue.name, card.blue.total, card.blue.is_winner) == ("Welter", "342,711", True)
    ninerik = card.red.players[0]
    assert (ninerik.accuracy, ninerik.combo, ninerik.mods) == ("95.75%", "694x", "HR")
    assert ninerik.avatar_url is not None
    assert ninerik.avatar_url.startswith("https://a.ppy.sh/10549880?")
    assert not card.long_title  # "Ragnarok"
    assert (card.red_wins_after, card.blue_wins_after) == (3, 7)
    assert card.is_head_to_head


def test_score_after_the_map_counts_only_maps_so_far() -> None:
    match = processed(HEAD_TO_HEAD_ID)

    card = build_clutch_card(match, clutch_at(match, 5))

    assert (card.red_wins_after, card.blue_wins_after) == (2, 3)


def test_team_vs_clutch_card() -> None:
    match = processed(TEAM_VS_ID)

    card = build_clutch_card(match, clutch_at(match, 7))

    assert not card.is_head_to_head
    assert card.title == "awa~awa~AWESOMENESS OVEDRIVE!!!"
    assert card.long_title
    assert (card.red.total, card.blue.total) == ("694,726", "661,520")
    assert card.red.is_winner
    assert card.winner_name == "Furball Pups"
    for side in (card.red, card.blue):
        assert len(side.players) == 2
        scores = [int(p.score.replace(",", "")) for p in side.players]
        assert scores == sorted(scores, reverse=True)
        assert sum(scores) == int(side.total.replace(",", ""))


def test_many_players_end_in_more() -> None:
    match = processed(WARMUP_MATCH_ID)  # 3 v 3

    card = build_clutch_card(match, match.clutches[0], max_rows=2)

    assert (len(card.red.players), card.red.hidden_players) == (1, 2)


def test_deleted_beatmap() -> None:
    match = processed(HEAD_TO_HEAD_ID)
    clutch = clutch_at(match, 10)
    game = clutch.game.game.model_copy(update={"beatmap": None})

    card = build_clutch_card(match, replace(clutch, game=replace(clutch.game, game=game)))

    assert card.title == f"Beatmap {game.beatmap_id}"
    assert (card.artist, card.version, card.stars, card.cover_url) == ("", "", "", None)
    assert "--cover" not in clutch_card_html(card)


def test_cover_is_used_in_the_html() -> None:
    match = processed(HEAD_TO_HEAD_ID)
    card = build_clutch_card(match, clutch_at(match, 10))

    html = clutch_card_html(card)

    assert f"--cover: url('{card.cover_url}')" in html


async def test_renders_every_clutch_card() -> None:
    requested: list[str] = []

    async def fake_images(url: str) -> tuple[bytes, str] | None:
        requested.append(url)
        buffer = io.BytesIO()
        Image.new("RGB", (18, 5), (40, 90, 160)).save(buffer, format="PNG")
        return buffer.getvalue(), "image/png"

    async with CardRenderer(fake_images) as renderer:
        pngs = await render_clutch_cards(renderer, processed(HEAD_TO_HEAD_ID))
        team_vs = await render_clutch_cards(renderer, processed(TEAM_VS_ID))

    assert len(pngs) == 4
    assert len(team_vs) == 1
    for png in pngs + team_vs:
        assert Image.open(io.BytesIO(png)).size == (2400, 1350)
    covers = [url for url in requested if "assets.ppy.sh" in url]
    avatars = [url for url in requested if "a.ppy.sh/" in url and url not in covers]
    assert len(covers) == 5
    assert all("cover@2x" in url for url in covers)
    assert avatars  # players' pictures were asked for too
    assert set(requested) == set(covers) | set(avatars)
