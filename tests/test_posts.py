from dataclasses import replace

from clutchbot.processing.pipeline import ProcessedMatch, process_match
from clutchbot.processing.tournament import TournamentName
from clutchbot.render.cards import MatchCards, RenderedCard
from clutchbot.twitter.client import MAX_POST_LENGTH
from clutchbot.twitter.posts import format_plan, plan_posts
from tests.conftest import HEAD_TO_HEAD_ID, TEAM_VS_ID, load_detail

DBHS = TournamentName("DBHS", "Dio's Bizzare Holiday Singles", "NINERIK", "Welter")
CRTOA = TournamentName("CRTOA", "CRTOA", "Furball Pups", "Junior Sleuth Teds")
DBHS_REPLY = "\N{HIGH VOLTAGE SIGN} 4 clutch maps \N{MIDDLE DOT} \N{COLLISION SYMBOL} 1 stomp"


def processed(match_id: int = HEAD_TO_HEAD_ID) -> ProcessedMatch:
    names = DBHS if match_id == HEAD_TO_HEAD_ID else CRTOA
    return process_match(load_detail(match_id), names)


def card(kind: str, map_number: int) -> RenderedCard:
    return RenderedCard(f"{kind}-{map_number}.png", b"png", map_number)


def cards_for(match: ProcessedMatch) -> MatchCards:
    stomp, huge = match.stomp, match.huge_score
    return MatchCards(
        RenderedCard("result.png", b"png"),
        [card("clutch", c.map_number) for c in match.clutches],
        stomp=card("stomp", stomp.map_number) if stomp else None,
        huge_score=card("huge", huge.map_number) if huge else None,
    )


def fake_cards(
    clutch_count: int, stomp: int | None = None, huge_score: int | None = None
) -> MatchCards:
    return MatchCards(
        RenderedCard("result.png", b"png"),
        [card("clutch", n) for n in range(1, clutch_count + 1)],
        stomp=card("stomp", stomp) if stomp else None,
        huge_score=card("huge", huge_score) if huge_score else None,
    )


def test_result_post_and_map_replies_in_map_order() -> None:
    match = processed()

    posts = plan_posts(match, cards_for(match))

    assert len(posts) == 3
    assert posts[0].text == (
        "\N{TROPHY} Dio's Bizzare Holiday Singles\nWelter defeats NINERIK 7\N{EN DASH}3\n#osugame"
    )
    assert [card.file_name for card in posts[0].cards] == ["result.png"]
    assert posts[1].text == f"{DBHS_REPLY} in NINERIK vs Welter (1/2)"
    assert posts[2].text == f"{DBHS_REPLY} in NINERIK vs Welter (2/2)"
    assert [[card.file_name for card in post.cards] for post in posts[1:]] == [
        ["clutch-5.png", "clutch-6.png", "stomp-7.png", "clutch-8.png"],
        ["clutch-10.png"],
    ]


def test_one_clutch_map_and_an_acronym_as_title() -> None:
    match = processed(TEAM_VS_ID)

    posts = plan_posts(match, cards_for(match))

    assert posts[0].text.splitlines() == [
        "\N{TROPHY} CRTOA",
        "Furball Pups defeats Junior Sleuth Teds 7\N{EN DASH}6",
        "#osugame",
    ]
    assert posts[1].text.endswith(
        "1 clutch map \N{MIDDLE DOT} \N{COLLISION SYMBOL} 1 stomp "
        "in Furball Pups vs Junior Sleuth Teds"
    )
    assert [card.file_name for card in posts[1].cards] == ["stomp-1.png", "clutch-7.png"]


def test_no_map_cards_still_posts_the_result() -> None:
    posts = plan_posts(processed(), fake_cards(0))

    assert len(posts) == 1
    assert [card.file_name for card in posts[0].cards] == ["result.png"]


def test_the_flow_chart_is_the_second_image_of_the_main_post() -> None:
    cards = replace(fake_cards(1), flow=RenderedCard("flow.png", b"png"))

    posts = plan_posts(processed(), cards)

    assert [card.file_name for card in posts[0].cards] == ["result.png", "flow.png"]
    assert [card.file_name for card in posts[1].cards] == ["clutch-1.png"]


def test_more_than_four_clutch_maps_continue_in_another_reply() -> None:
    posts = plan_posts(processed(), fake_cards(6))

    assert [len(post.cards) for post in posts] == [1, 4, 2]
    assert posts[1].text.endswith("6 clutch maps in NINERIK vs Welter (1/2)")
    assert posts[2].text.endswith("6 clutch maps in NINERIK vs Welter (2/2)")


def test_a_stomp_alone_gets_a_reply() -> None:
    posts = plan_posts(processed(), fake_cards(0, stomp=3))

    assert posts[1].text == "\N{COLLISION SYMBOL} 1 stomp in NINERIK vs Welter"
    assert [card.file_name for card in posts[1].cards] == ["stomp-3.png"]


def test_every_kind_of_map_card() -> None:
    posts = plan_posts(processed(), fake_cards(2, stomp=4, huge_score=3))

    assert posts[1].text == (
        "\N{HIGH VOLTAGE SIGN} 2 clutch maps \N{MIDDLE DOT} \N{COLLISION SYMBOL} 1 stomp "
        "\N{MIDDLE DOT} \N{FIRE} 1 huge score in NINERIK vs Welter"
    )
    names = [card.file_name for card in posts[1].cards]
    assert names == ["clutch-1.png", "clutch-2.png", "huge-3.png", "stomp-4.png"]


def test_draw() -> None:
    match = processed()
    first_four = replace(match, result=replace(match.result, games=match.result.games[:4]))

    posts = plan_posts(first_four, fake_cards(0))

    assert posts[0].text.splitlines()[1] == "NINERIK and Welter draw 2\N{EN DASH}2"


def test_long_names_are_shortened_and_posts_fit() -> None:
    long_name = "An Extremely Long Tournament Name That Just Keeps Going And Going"
    match = replace(processed(), names=replace(DBHS, tournament=long_name))

    posts = plan_posts(match, fake_cards(9, stomp=10, huge_score=11))

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
        "Post 2, reply to post 1 [clutch-5.png, clutch-6.png, stomp-7.png, clutch-8.png]\n"
        f"  | {DBHS_REPLY} in NINERIK vs Welter (1/2)\n"
        "\n"
        "Post 3, reply to post 2 [clutch-10.png]\n"
        f"  | {DBHS_REPLY} in NINERIK vs Welter (2/2)"
    )
