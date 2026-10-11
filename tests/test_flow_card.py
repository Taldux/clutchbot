import io
from dataclasses import replace

from PIL import Image

from clutchbot.processing.highlights import HugeScore
from clutchbot.processing.pipeline import ProcessedMatch, process_match
from clutchbot.processing.result import Side
from clutchbot.processing.tournament import TournamentName
from clutchbot.render.browser import CardRenderer
from clutchbot.render.flow_card import build_flow_card, flow_card_html, render_flow_card
from tests.conftest import HEAD_TO_HEAD_ID, TEAM_VS_ID, load_detail

DBHS = TournamentName("DBHS", "DBHS Tournament", "NINERIK", "Welter")
CRTOA = TournamentName("CRTOA", "CRTOA Tournament", "Furball Pups", "Junior Sleuth Teds")


def processed(match_id: int = HEAD_TO_HEAD_ID) -> ProcessedMatch:
    return process_match(load_detail(match_id), DBHS if match_id == HEAD_TO_HEAD_ID else CRTOA)


def test_map_score_after_every_map() -> None:
    card = build_flow_card(processed())

    assert (card.red_name, card.red_wins, card.blue_wins, card.blue_name) == (
        "NINERIK",
        3,
        7,
        "Welter",
    )
    assert card.winner == "blue"
    scores = ["1-0", "1-1", "2-1", "2-2", "2-3", "2-4", "3-4", "3-5", "3-6", "3-7"]
    assert [p.score for p in card.points] == [s.replace("-", "\N{EN DASH}") for s in scores]
    assert [p.winner for p in card.points[:4]] == ["red", "blue", "red", "blue"]


def test_red_leads_above_the_middle_and_blue_below() -> None:
    card = build_flow_card(processed())
    one_nil, level, three_seven = card.points[0], card.points[3], card.points[-1]

    assert one_nil.y < card.zero_y
    assert level.y == card.zero_y
    assert three_seven.y > card.zero_y
    assert one_nil.label_y < one_nil.y  # scores sit away from the middle
    assert three_seven.label_y > three_seven.y


def test_a_one_sided_match_fills_the_chart() -> None:
    card = build_flow_card(processed(TEAM_VS_ID))

    # the red team never trailed, so the middle line is at the bottom
    assert all(p.y < card.zero_y for p in card.points)
    assert card.zero_y > card.height * 0.7


def test_clutch_and_stomp_maps_are_tagged() -> None:
    card = build_flow_card(processed())

    tagged = {p.map_number: [t.kind for t in p.tags] for p in card.points if p.tags}

    assert tagged == {5: ["clutch"], 6: ["clutch"], 7: ["stomp"], 8: ["clutch"], 10: ["clutch"]}
    assert [t.label for t in card.legend] == ["Clutch", "Stomp"]


def test_a_huge_score_on_the_stomp_map_keeps_both_tags() -> None:
    match = processed(TEAM_VS_ID)
    assert match.stomp is not None
    played = match.stomp.game
    score = max(played.game.scores, key=lambda s: s.total_score)
    huge = HugeScore(match.stomp.map_number, played, score, Side(score.match.team), 2.5)

    card = build_flow_card(replace(match, huge_score=huge))

    first = card.points[0]
    assert [t.kind for t in first.tags] == ["stomp", "huge"]
    assert first.tags[0].y < first.tags[1].y  # stacked
    assert [t.label for t in card.legend] == ["Clutch", "Stomp", "Huge score"]
    assert 'class="tag huge"' in flow_card_html(card)


def test_a_single_map_still_has_room() -> None:
    match = processed()
    one_map = replace(match, result=replace(match.result, games=match.result.games[:1]))

    card = build_flow_card(one_map)

    assert len(card.points) == 1
    assert card.points[0].y < card.zero_y


async def test_renders_the_flow_card() -> None:
    async with CardRenderer() as renderer:
        png = await render_flow_card(renderer, build_flow_card(processed()))

    assert Image.open(io.BytesIO(png)).size == (2400, 1350)
