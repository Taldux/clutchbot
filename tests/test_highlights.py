import math

import pytest

from clutchbot.osu.models import Game, MatchDetail
from clutchbot.processing.clutch import clutch_maps
from clutchbot.processing.highlights import biggest_huge_score, biggest_stomp
from clutchbot.processing.result import GameResult, MatchResult, MatchType, Side, Team, build_result
from clutchbot.processing.tournament import TournamentName
from clutchbot.processing.trim import trim_games
from tests.conftest import (
    HEAD_TO_HEAD_ID,
    TEAM_VS_ID,
    WARMUP_MATCH_ID,
    load_detail,
    replace_games,
)

NAMES = TournamentName("X", "X Tournament", "Red", "Blue")


def result_of(detail: MatchDetail) -> MatchResult:
    return build_result(trim_games(detail).games, NAMES, detail.users_by_id)


def result_with_scores(*scores: tuple[int, int]) -> MatchResult:
    """A made-up match with the given (red, blue) scores, one map each"""
    game = load_detail(HEAD_TO_HEAD_ID).games[0]
    return MatchResult(
        MatchType.HEAD_TO_HEAD,
        Team("Red", (1,)),
        Team("Blue", (2,)),
        tuple(GameResult(game, red, blue) for red, blue in scores),
    )


def with_huge_score(game: Game, factor: int = 3) -> Game:
    """The first score becomes `factor` times the biggest of the others"""
    first, *rest = game.scores
    huge = first.model_copy(update={"total_score": max(s.total_score for s in rest) * factor})
    return game.model_copy(update={"scores": [huge, *rest]})


@pytest.mark.parametrize(
    ("match_id", "map_number", "winner"),
    [
        (HEAD_TO_HEAD_ID, 7, Side.RED),  # 389,527 against 82,636
        (TEAM_VS_ID, 1, Side.RED),
    ],
)
def test_fixture_stomps(match_id: int, map_number: int, winner: Side) -> None:
    stomp = biggest_stomp(result_of(load_detail(match_id)))

    assert stomp is not None
    assert (stomp.map_number, stomp.winner) == (map_number, winner)
    assert stomp.ratio >= 2


def test_no_stomp_without_a_map_won_by_twice_the_score() -> None:
    # BLB's biggest margin is 17%
    assert biggest_stomp(result_of(load_detail(WARMUP_MATCH_ID))) is None


def test_the_biggest_stomp_wins_and_exactly_twice_counts() -> None:
    stomp = biggest_stomp(result_with_scores((200, 100), (100, 450), (300, 299)))

    assert stomp is not None
    assert (stomp.map_number, stomp.winner, stomp.ratio) == (2, Side.BLUE, 4.5)
    assert biggest_stomp(result_with_scores((200, 101))) is None


def test_a_side_scoring_nothing_is_the_biggest_stomp() -> None:
    stomp = biggest_stomp(result_with_scores((900, 100), (50, 0)))

    assert stomp is not None
    assert (stomp.map_number, stomp.ratio) == (2, math.inf)


def test_no_huge_scores_in_the_fixtures() -> None:
    for match_id in (TEAM_VS_ID, WARMUP_MATCH_ID):
        result = result_of(load_detail(match_id))
        assert biggest_huge_score(result, clutch_maps(result)) is None


def test_a_score_twice_everyone_else_is_huge() -> None:
    detail = load_detail(TEAM_VS_ID)
    changed = replace_games(detail, {3: with_huge_score(detail.games[3])})
    result = result_of(changed)

    huge = biggest_huge_score(result, clutch_maps(result))

    assert huge is not None
    first = changed.games[3].scores[0]
    assert (huge.map_number, huge.score.user_id, huge.ratio) == (4, first.user_id, 3)
    assert huge.side.value == first.match.team


def test_the_biggest_huge_score_wins() -> None:
    detail = load_detail(TEAM_VS_ID)
    changed = replace_games(
        detail,
        {2: with_huge_score(detail.games[2], 3), 9: with_huge_score(detail.games[9], 5)},
    )
    result = result_of(changed)

    huge = biggest_huge_score(result, clutch_maps(result))

    assert huge is not None
    assert (huge.map_number, huge.ratio) == (10, 5)


def test_huge_scores_on_clutch_maps_are_skipped() -> None:
    detail = load_detail(TEAM_VS_ID)
    result = result_of(replace_games(detail, {3: with_huge_score(detail.games[3])}))
    # pretend map 4 was clutch, its card already shows the score
    clutches = [c for c in clutch_maps(result, threshold_percent=100) if c.map_number == 4]

    assert biggest_huge_score(result, clutches) is None
    assert biggest_huge_score(result, []) is not None


def test_no_huge_scores_in_head_to_head() -> None:
    detail = load_detail(HEAD_TO_HEAD_ID)
    result = result_of(detail)

    # DBHS map 7 is 389,527 against 82,636, which is the stomp instead
    assert biggest_huge_score(result, []) is None
