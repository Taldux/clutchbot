import math

import pytest

from clutchbot.osu.models import Game
from clutchbot.processing.cost import _round_cost, is_tiebreaker, match_costs
from clutchbot.processing.result import MatchResult, Side, build_result
from clutchbot.processing.tournament import TournamentName
from clutchbot.processing.trim import trim_games
from tests.conftest import HEAD_TO_HEAD_ID, TEAM_VS_ID, WARMUP_MATCH_ID, load_detail

NAMES = TournamentName("X", "X Tournament", "Red", "Blue")

COSTS = {
    WARMUP_MATCH_ID: {
        "Kyube": 1.21,
        "Fleh": 2.93,
        "tplc": 1.77,
        "Haj": 1.83,
        "mort": 1.90,
        "Katharsis": 1.63,
        "Freakmaster": 2.34,
        "[ Nano ]": 1.64,
        "x3Lydiia": 1.76,
        "Ji2zie": 1.36,
        "Herazu": 2.34,
    },
    HEAD_TO_HEAD_ID: {"NINERIK": 2.52, "Welter": 2.16},
    TEAM_VS_ID: {
        "enri": 2.91,
        "Bernkastel": 1.48,
        "Taldux": 1.76,
        "ciee": 1.39,
        "OmegaOrigins": 1.25,
        "MALISZEWSKI": 2.82,
        "PSP": 0.54,
        "Juicy": 1.39,
    },
}


def result_from(games: list[Game]) -> MatchResult:
    return build_result(games, NAMES, load_detail(HEAD_TO_HEAD_ID).users_by_id)


def costs_by_name(match_id: int) -> dict[str, float]:
    detail = load_detail(match_id)
    result = build_result(trim_games(detail).games, NAMES, detail.users_by_id)
    users = detail.users_by_id
    return {users[c.user_id].username: c.cost for c in match_costs(result)}


@pytest.mark.parametrize("match_id", list(COSTS))
def test_known_costs(match_id: int) -> None:
    assert costs_by_name(match_id) == COSTS[match_id]


def test_order_and_maps_played() -> None:
    detail = load_detail(WARMUP_MATCH_ID)
    result = build_result(trim_games(detail).games, NAMES, detail.users_by_id)

    costs = match_costs(result)

    assert [c.user_id for c in costs] == [*result.red.player_ids, *result.blue.player_ids]
    assert [c.side for c in costs] == [Side.RED] * 5 + [Side.BLUE] * 6
    assert max(c.maps_played for c in costs) == 6
    assert min(c.maps_played for c in costs) == 1


def test_tiebreaker_bonus() -> None:
    games = trim_games(load_detail(HEAD_TO_HEAD_ID)).games
    # maps 1-4 end 2-2, so map 5 is a tiebreaker
    result = result_from(games[:5])

    assert is_tiebreaker(result)
    assert not is_tiebreaker(result_from(games))
    # without the bonus these would be 2.48 and 2.11
    assert [c.cost for c in match_costs(result)] == [2.73, 2.36]


def test_single_map_match() -> None:
    games = trim_games(load_detail(HEAD_TO_HEAD_ID)).games

    costs = match_costs(result_from(games[:1]))

    # participation is 1.5
    assert [c.cost for c in costs] == [2.93, 1.57]
    assert not is_tiebreaker(result_from(games[:1]))


def test_map_where_everyone_scored_zero() -> None:
    games = trim_games(load_detail(HEAD_TO_HEAD_ID)).games
    zeroed = [s.model_copy(update={"total_score": 0}) for s in games[0].scores]
    games[0] = games[0].model_copy(update={"scores": zeroed})

    costs = match_costs(result_from(games))

    assert all(math.isfinite(c.cost) for c in costs)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.125, 0.13),  # Python's round() would give 0.12
        (1.115, 1.12),  # 1.115 * 100 is exactly 111.5 as a float
        (2.344, 2.34),
        (2.3449999, 2.34),
        (1.9, 1.9),
    ],
)
def test_rounding_matches(value: float, expected: float) -> None:
    assert _round_cost(value) == expected
