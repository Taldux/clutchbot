import pytest

from clutchbot.osu.models import Game
from clutchbot.processing.trim import SkipReason, is_warmup, trim_games
from tests.conftest import (
    HEAD_TO_HEAD_ID,
    TEAM_VS_ID,
    WARMUP_MATCH_ID,
    load_detail,
    replace_games,
    without_nf,
)


@pytest.mark.parametrize(
    ("match_id", "counted", "warmups"),
    [
        (HEAD_TO_HEAD_ID, 10, []),
        (TEAM_VS_ID, 13, []),  # game 1 is 2 v 1 after a disconnect, but everyone has NF
        (WARMUP_MATCH_ID, 6, [0, 1]),
    ],
)
def test_fixtures(match_id: int, counted: int, warmups: list[int]) -> None:
    detail = load_detail(match_id)

    trimmed = trim_games(detail)

    assert len(trimmed.games) == counted
    assert [detail.games.index(s.game) for s in trimmed.skipped] == warmups
    assert all(s.reason is SkipReason.WARMUP for s in trimmed.skipped)
    assert trimmed.games == [g for g in detail.games if g not in [s.game for s in trimmed.skipped]]


@pytest.mark.parametrize(("checked", "counted"), [(2, 6), (1, 7), (0, 8)])
def test_warmup_games_checked(checked: int, counted: int) -> None:
    trimmed = trim_games(load_detail(WARMUP_MATCH_ID), warmup_games_checked=checked)

    assert len(trimmed.games) == counted


def test_no_warmups_after_the_first_real_map() -> None:
    detail = load_detail(HEAD_TO_HEAD_ID)
    # game 2 looks like a warmup, but game 1 was a real map
    changed = replace_games(detail, {1: without_nf(detail.games[1])})

    trimmed = trim_games(changed)

    assert len(trimmed.games) == 10
    assert trimmed.skipped == []


def test_single_score_without_nf_is_a_warmup() -> None:
    detail = load_detail(HEAD_TO_HEAD_ID)
    first = detail.games[0]
    solo = without_nf(first.model_copy(update={"scores": first.scores[:1]}))

    trimmed = trim_games(replace_games(detail, {0: solo}))

    assert [s.game for s in trimmed.skipped] == [solo]
    assert len(trimmed.games) == 9


def test_games_without_scores_are_dropped() -> None:
    detail = load_detail(HEAD_TO_HEAD_ID)
    aborted = detail.games[3].model_copy(update={"scores": []})

    trimmed = trim_games(replace_games(detail, {3: aborted}))

    assert [(s.game, s.reason) for s in trimmed.skipped] == [(aborted, SkipReason.NO_SCORES)]
    assert len(trimmed.games) == 9


def test_aborted_game_does_not_use_up_a_warmup_check() -> None:
    detail = load_detail(WARMUP_MATCH_ID)
    aborted = detail.games[0].model_copy(update={"scores": []})

    trimmed = trim_games(replace_games(detail, {0: aborted}))

    assert [s.reason for s in trimmed.skipped] == [SkipReason.NO_SCORES, SkipReason.WARMUP]
    assert len(trimmed.games) == 6


def test_is_warmup() -> None:
    game = load_detail(HEAD_TO_HEAD_ID).games[0]

    assert not is_warmup(game)
    assert is_warmup(without_nf(game, score_index=1))


def test_a_last_map_without_nf_is_a_tiebreaker_for_fun() -> None:
    detail = load_detail(HEAD_TO_HEAD_ID)
    last = without_nf(detail.games[-1])

    trimmed = trim_games(replace_games(detail, {9: last}))

    assert [(s.game, s.reason) for s in trimmed.skipped] == [(last, SkipReason.FUN_TIEBREAKER)]
    assert len(trimmed.games) == 9


def with_extra_players(game: Game, count: int) -> Game:
    """The same map with `count` more players on each side, like a 2v2 played as 4v4"""
    extra = [
        score.model_copy(update={"user_id": score.user_id + 1_000_000 * n})
        for n in range(1, count + 1)
        for score in game.scores
    ]
    return game.model_copy(update={"scores": [*game.scores, *extra]})


@pytest.mark.parametrize("match_id", [TEAM_VS_ID, HEAD_TO_HEAD_ID])
def test_a_bigger_last_map_is_a_tiebreaker_for_fun(match_id: int) -> None:
    detail = load_detail(match_id)
    last_index = len(detail.games) - 1
    bigger = with_extra_players(detail.games[last_index], 1)

    trimmed = trim_games(replace_games(detail, {last_index: bigger}))

    assert [(s.game, s.reason) for s in trimmed.skipped] == [(bigger, SkipReason.FUN_TIEBREAKER)]
    assert len(trimmed.games) == len(detail.games) - 1


def test_a_bigger_map_before_the_end_still_counts() -> None:
    detail = load_detail(TEAM_VS_ID)
    bigger = with_extra_players(detail.games[5], 1)

    trimmed = trim_games(replace_games(detail, {5: bigger}))

    assert trimmed.skipped == []
