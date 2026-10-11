import io
import math
from dataclasses import replace

from PIL import Image

from clutchbot.processing.clutch import ClutchMap
from clutchbot.processing.highlights import HugeScore
from clutchbot.processing.pipeline import ProcessedMatch, process_match
from clutchbot.processing.result import Side
from clutchbot.processing.tournament import TournamentName
from clutchbot.render.browser import CardRenderer
from clutchbot.render.cards import render_match_cards
from clutchbot.render.map_card import (
    build_clutch_card,
    build_huge_score_card,
    build_stomp_card,
    map_card_html,
    render_map_card,
)
from tests.conftest import HEAD_TO_HEAD_ID, TEAM_VS_ID, WARMUP_MATCH_ID, load_detail

NAMES = {
    HEAD_TO_HEAD_ID: TournamentName("DBHS", "DBHS Tournament", "NINERIK", "Welter"),
    TEAM_VS_ID: TournamentName("CRTOA", "CRTOA Tournament", "Furball Pups", "Junior Sleuth Teds"),
    WARMUP_MATCH_ID: TournamentName("BLB", "BLB Tournament", "Hessen", "Berlin"),
}
TIMES = "\N{MULTIPLICATION SIGN}"
INFINITY = "\N{INFINITY}"


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
    assert (card.kind, card.label) == ("clutch", "Clutch moment")
    assert (card.headline, card.note_name) == ("+2,144", "Welter")  # 342,711 - 340,567
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
    assert card.note_name == "Furball Pups"
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
    assert "--cover" not in map_card_html(card)


def test_cover_is_used_in_the_html() -> None:
    match = processed(HEAD_TO_HEAD_ID)
    card = build_clutch_card(match, clutch_at(match, 10))

    html = map_card_html(card)

    assert f"--cover: url('{card.cover_url}')" in html


async def test_renders_clutch_cards() -> None:
    requested: list[str] = []

    async def fake_images(url: str) -> tuple[bytes, str] | None:
        requested.append(url)
        buffer = io.BytesIO()
        Image.new("RGB", (18, 5), (40, 90, 160)).save(buffer, format="PNG")
        return buffer.getvalue(), "image/png"

    async with CardRenderer(fake_images) as renderer:
        head_to_head = processed(HEAD_TO_HEAD_ID)
        pngs = [
            await render_map_card(renderer, build_clutch_card(head_to_head, clutch))
            for clutch in head_to_head.clutches
        ]
        team_vs = processed(TEAM_VS_ID)
        team_vs_pngs = [
            await render_map_card(renderer, build_clutch_card(team_vs, team_vs.clutches[0]))
        ]

    assert len(pngs) == 4
    assert len(team_vs_pngs) == 1
    for png in pngs + team_vs_pngs:
        assert Image.open(io.BytesIO(png)).size == (2400, 1350)
    covers = [url for url in requested if "assets.ppy.sh" in url]
    avatars = [url for url in requested if "a.ppy.sh/" in url and url not in covers]
    assert len(covers) == 5
    assert all("cover@2x" in url for url in covers)
    assert avatars  # players' pictures were asked for too
    assert set(requested) == set(covers) | set(avatars)


def test_stomp_card() -> None:
    match = processed(HEAD_TO_HEAD_ID)
    assert match.stomp is not None

    card = build_stomp_card(match, match.stomp)

    assert (card.kind, card.label, card.map_number) == ("stomp", "Stomp", 7)
    assert (card.headline, card.note_name) == (f"4.7{TIMES}", "NINERIK")  # 389,527 against 82,636
    assert 'class="card stomp"' in map_card_html(card)


def test_stomp_against_nothing() -> None:
    match = processed(HEAD_TO_HEAD_ID)
    assert match.stomp is not None

    card = build_stomp_card(match, replace(match.stomp, ratio=math.inf))

    assert card.headline == INFINITY + TIMES


def test_huge_score_card() -> None:
    match = processed(TEAM_VS_ID)
    played = match.result.games[3]
    score = max(played.game.scores, key=lambda s: s.total_score)
    huge = HugeScore(4, played, score, Side(score.match.team), 2.345)

    card = build_huge_score_card(match, huge)

    assert (card.kind, card.label) == ("huge", "Huge score")
    assert card.headline == f"{score.total_score:,}"
    assert (card.note_before, card.note_after) == ("", f" · 2.3{TIMES} the next best")
    highlighted = [p for p in card.red.players + card.blue.players if p.is_highlighted]
    assert [p.name for p in highlighted] == [card.note_name]
    assert 'class="row highlighted"' in map_card_html(card)


async def test_a_huge_score_replaces_the_stomp_on_the_same_map() -> None:
    match = processed(TEAM_VS_ID)
    assert match.stomp is not None
    played = match.stomp.game
    score = max(played.game.scores, key=lambda s: s.total_score)
    huge = HugeScore(match.stomp.map_number, played, score, Side(score.match.team), 2.5)

    async with CardRenderer() as renderer:
        cards = await render_match_cards(renderer, replace(match, huge_score=huge))

    assert cards.stomp is None
    assert [c.file_name for c in cards.all] == [
        "result.png",
        "flow.png",
        "huge-1.png",
        "clutch-7.png",
    ]
