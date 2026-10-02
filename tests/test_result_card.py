import io
from dataclasses import replace

from PIL import Image

from clutchbot.processing.pipeline import ProcessedMatch, process_match
from clutchbot.processing.result import Team
from clutchbot.processing.tournament import TournamentName
from clutchbot.render.browser import CardRenderer
from clutchbot.render.result_card import (
    build_head_to_head_card,
    build_result_card,
    render_result_card,
    result_card_html,
)
from tests.conftest import HEAD_TO_HEAD_ID, TEAM_VS_ID, WARMUP_MATCH_ID, load_detail

CRTOA = TournamentName("CRTOA", "CRTOA Tournament", "Furball Pups", "Junior Sleuth Teds")
BLB = TournamentName("BLB", "BLB Tournament", "Hessen", "Berlin")
DBHS = TournamentName("DBHS", "DBHS Tournament", "NINERIK", "Welter")


def processed(match_id: int = TEAM_VS_ID) -> ProcessedMatch:
    names = CRTOA if match_id == TEAM_VS_ID else BLB
    return process_match(load_detail(match_id), names)


def renamed(match: ProcessedMatch, red: str, blue: str) -> ProcessedMatch:
    """The same match with other team names"""
    result = match.result
    return replace(
        match,
        result=replace(
            result,
            red=Team(red, result.red.player_ids),
            blue=Team(blue, result.blue.player_ids),
        ),
    )


def test_card_data() -> None:
    card = build_result_card(processed())

    assert card.tournament == "CRTOA Tournament"
    assert card.date == "8 March 2025"
    assert card.match_link == "osu.ppy.sh/mp/117416879"
    assert (card.red.name, card.red.wins, card.red.is_winner) == ("Furball Pups", 7, True)
    assert (card.blue.name, card.blue.wins, card.blue.is_winner) == ("Junior Sleuth Teds", 6, False)
    assert not card.is_draw
    assert (card.maps_played, card.clutch_maps) == (13, 1)


def test_players_are_sorted_by_cost_and_the_best_is_mvp() -> None:
    card = build_result_card(processed())

    assert [(p.name, p.cost) for p in card.red.players] == [
        ("enri", "2.91"),
        ("Bernkastel", "1.48"),
        ("ciee", "1.39"),  # equal costs keep their order of first appearance
        ("Juicy", "1.39"),
    ]
    assert [(p.name, p.cost) for p in card.blue.players] == [
        ("MALISZEWSKI", "2.82"),
        ("Taldux", "1.76"),
        ("OmegaOrigins", "1.25"),
        ("PSP", "0.54"),
    ]
    mvps = [p.name for p in card.red.players + card.blue.players if p.is_mvp]
    assert mvps == ["enri"]


def test_player_avatars() -> None:
    card = build_result_card(processed())
    avatars = {p.name: p.avatar_url for p in card.red.players + card.blue.players}

    assert avatars["enri"] == "https://a.ppy.sh/8640970?1789222097.jpeg"
    # ciee has no avatar: osu! sends its guest picture, which cards skip for the placeholder
    assert avatars["ciee"] is None
    assert "a.ppy.sh/8640970" in result_card_html(card)


def test_long_rosters_end_in_more() -> None:
    card = build_result_card(processed(WARMUP_MATCH_ID), max_rows=4)  # 5 v 6 players

    assert (len(card.red.players), card.red.hidden_players) == (3, 2)
    assert (len(card.blue.players), card.blue.hidden_players) == (3, 3)
    assert build_result_card(processed(WARMUP_MATCH_ID), max_rows=5).red.hidden_players == 0


def test_long_team_names_are_marked() -> None:
    card = build_result_card(renamed(processed(), "Short", "A Rather Long Team Name"))

    assert not card.red.long_name
    assert card.blue.long_name


def test_names_are_escaped_in_the_html() -> None:
    html = result_card_html(build_result_card(renamed(processed(), "<b>Red</b>", "A & B")))

    assert "&lt;b&gt;Red&lt;/b&gt;" in html
    assert "A &amp; B" in html
    assert "<b>Red</b>" not in html


def test_head_to_head_card_data() -> None:
    card = build_head_to_head_card(process_match(load_detail(HEAD_TO_HEAD_ID), DBHS))

    assert (card.red.name, card.red.wins, card.red.is_winner) == ("NINERIK", 3, False)
    assert (card.blue.name, card.blue.wins, card.blue.is_winner) == ("Welter", 7, True)
    # the loser has the better cost, score and accuracy: his wins were blowouts
    assert (card.red.cost, card.red.has_best_cost) == ("2.52", True)
    assert (card.blue.cost, card.blue.has_best_cost) == ("2.16", False)
    assert (card.red.average_score, card.red.average_accuracy) == ("268,060", "92.29%")
    assert (card.blue.average_score, card.blue.average_accuracy) == ("227,901", "88.41%")
    assert card.cost_ratio == "1.17"
    assert card.red.avatar_url is not None
    assert card.red.avatar_url.startswith("https://a.ppy.sh/10549880?")
    assert "Performance difference 1.17" in result_card_html(card)
    assert (card.maps_played, card.clutch_maps) == (10, 4)


def test_head_to_head_without_a_cost_ratio() -> None:
    match = process_match(load_detail(HEAD_TO_HEAD_ID), DBHS)
    zero_cost = replace(match, costs=[replace(match.costs[0], cost=0.0), match.costs[1]])

    card = build_head_to_head_card(zero_cost)

    assert card.cost_ratio is None
    assert "Performance difference" not in result_card_html(card)


async def test_renders_the_card() -> None:
    async with CardRenderer() as renderer:
        png = await render_result_card(renderer, processed())
        long_names = await render_result_card(
            renderer,
            renamed(
                processed(WARMUP_MATCH_ID),
                "The Extremely Long Team Name That Goes On And On",
                "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW",
            ),
        )

        head_to_head = await render_result_card(
            renderer, process_match(load_detail(HEAD_TO_HEAD_ID), DBHS)
        )

    for card in (png, long_names, head_to_head):
        assert Image.open(io.BytesIO(card)).size == (2400, 1350)
