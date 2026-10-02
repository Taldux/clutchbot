import pytest

from clutchbot.processing.clutch import clutch_maps
from clutchbot.processing.result import GameResult, MatchResult, MatchType, Side, Team, build_result
from clutchbot.processing.tournament import TournamentName
from clutchbot.processing.trim import trim_games
from tests.conftest import HEAD_TO_HEAD_ID, TEAM_VS_ID, WARMUP_MATCH_ID, load_detail

NAMES = TournamentName("X", "X Tournament", "Red", "Blue")


def result_of(match_id: int) -> MatchResult:
    detail = load_detail(match_id)
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


@pytest.mark.parametrize(
    ("match_id", "threshold", "expected"),
    [
        (HEAD_TO_HEAD_ID, 5, [5, 6, 8, 10]),
        (TEAM_VS_ID, 5, [7]),
        (WARMUP_MATCH_ID, 5, [1, 4]),  # numbered from the first map after the warmups
        (TEAM_VS_ID, 7, [7, 11]),
        (WARMUP_MATCH_ID, 10, [1, 3, 4, 5, 6]),
        (HEAD_TO_HEAD_ID, 0.5, []),
    ],
)
def test_fixture_clutch_maps(match_id: int, threshold: float, expected: list[int]) -> None:
    clutches = clutch_maps(result_of(match_id), threshold)

    assert [c.map_number for c in clutches] == expected


def test_closest_map_details() -> None:
    last = clutch_maps(result_of(HEAD_TO_HEAD_ID))[-1]  # map 10: 342711 vs 340567

    assert last.winner is Side.BLUE
    assert (last.game.red_score, last.game.blue_score) == (340567, 342711)
    assert last.margin_percent == pytest.approx(0.6256, abs=1e-4)
    assert last.game.game.beatmap is not None
    assert last.game.game.beatmap.beatmapset.title == "Ragnarok"


def test_exactly_at_the_threshold_is_clutch() -> None:
    clutches = clutch_maps(result_with_scores((100000, 95000), (95000, 100000), (100000, 94999)))

    assert [(c.map_number, c.winner, c.margin_percent) for c in clutches] == [
        (1, Side.RED, 5.0),
        (2, Side.BLUE, 5.0),
    ]


def test_tied_and_one_sided_maps_are_not_clutch() -> None:
    assert clutch_maps(result_with_scores((100000, 100000), (100000, 0), (0, 0))) == []
