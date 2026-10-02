from dataclasses import replace

from clutchbot.processing.pipeline import ProcessedMatch, process_match
from clutchbot.processing.tournament import TournamentName
from clutchbot.render.cards import MatchCards, RenderedCard
from clutchbot.twitter.client import MAX_POST_LENGTH
from clutchbot.twitter.posts import format_plan, plan_posts
from tests.conftest import HEAD_TO_HEAD_ID, TEAM_VS_ID, load_detail

DBHS = TournamentName("DBHS", "Dio's Bizzare Holiday Singles", "NINERIK", "Welter")
CRTOA = TournamentName("CRTOA", "CRTOA", "Furball Pups", "Junior Sleuth Teds")


def processed(match_id: int = HEAD_TO_HEAD_ID) -> ProcessedMatch:
    names = DBHS if match_id == HEAD_TO_HEAD_ID else CRTOA
    return process_match(load_detail(match_id), names)


def cards_for(match: ProcessedMatch) -> MatchCards:
    clutches = [RenderedCard(f"clutch-{c.map_number}.png", b"png") for c in match.clutches]
    return MatchCards(RenderedCard("result.png", b"png"), clutches)


def fake_cards(clutch_count: int) -> MatchCards:
    clutches = [RenderedCard(f"clutch-{n}.png", b"png") for n in range(1, clutch_count + 1)]
    return MatchCards(RenderedCard("result.png", b"png"), clutches)


def test_result_post_and_one_clutch_reply() -> None:
    match = processed()

    posts = plan_posts(match, cards_for(match))

    assert len(posts) == 2
    assert posts[0].text == (
        "\N{TROPHY} Dio's Bizzare Holiday Singles\nWelter defeats NINERIK 7\N{EN DASH}3\n#osugame"
    )
    assert [card.file_name for card in posts[0].cards] == ["result.png"]
    assert posts[1].text == "\N{HIGH VOLTAGE SIGN} 4 clutch maps in NINERIK vs Welter"
    assert [card.file_name for card in posts[1].cards] == [
        "clutch-5.png",
        "clutch-6.png",
        "clutch-8.png",
        "clutch-10.png",
    ]


def test_one_clutch_map_and_an_acronym_as_title() -> None:
    match = processed(TEAM_VS_ID)

    posts = plan_posts(match, cards_for(match))

    assert posts[0].text.splitlines() == [
        "\N{TROPHY} CRTOA",
        "Furball Pups defeats Junior Sleuth Teds 7\N{EN DASH}6",
        "#osugame",
    ]
    assert posts[1].text.endswith("1 clutch map in Furball Pups vs Junior Sleuth Teds")


def test_no_clutch_maps_still_posts_the_result() -> None:
    posts = plan_posts(processed(), fake_cards(0))

    assert len(posts) == 1
    assert [card.file_name for card in posts[0].cards] == ["result.png"]


def test_more_than_four_clutch_maps_continue_in_another_reply() -> None:
    posts = plan_posts(processed(), fake_cards(6))

    assert [len(post.cards) for post in posts] == [1, 4, 2]
    assert posts[1].text.endswith("6 clutch maps in NINERIK vs Welter (1/2)")
    assert posts[2].text.endswith("6 clutch maps in NINERIK vs Welter (2/2)")


def test_draw() -> None:
    match = processed()
    first_four = replace(match, result=replace(match.result, games=match.result.games[:4]))

    posts = plan_posts(first_four, fake_cards(0))

    assert posts[0].text.splitlines()[1] == "NINERIK and Welter draw 2\N{EN DASH}2"


def test_long_names_are_shortened_and_posts_fit() -> None:
    long_name = "An Extremely Long Tournament Name That Just Keeps Going And Going"
    match = replace(processed(), names=replace(DBHS, tournament=long_name))

    posts = plan_posts(match, fake_cards(9))

    title = posts[0].text.splitlines()[0]
    assert title == "\N{TROPHY} An Extremely Long Tournament Name That\N{HORIZONTAL ELLIPSIS}"
    for post in posts:
        assert len(post.text) <= MAX_POST_LENGTH
        assert "http" not in post.text  # a link would make every post cost $0.20


def test_format_plan() -> None:
    match = processed()

    text = format_plan(plan_posts(match, cards_for(match)))

    assert text == (
        "Post 1 [result.png]\n"
        "  | \N{TROPHY} Dio's Bizzare Holiday Singles\n"
        "  | Welter defeats NINERIK 7\N{EN DASH}3\n"
        "  | #osugame\n"
        "\n"
        "Post 2, reply to post 1 [clutch-5.png, clutch-6.png, clutch-8.png, clutch-10.png]\n"
        "  | \N{HIGH VOLTAGE SIGN} 4 clutch maps in NINERIK vs Welter"
    )
