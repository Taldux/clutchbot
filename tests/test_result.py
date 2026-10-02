import pytest

from clutchbot.osu.models import Game, MatchDetail
from clutchbot.processing.result import (
    MatchResult,
    MatchType,
    ResultError,
    Side,
    build_result,
    detect_match_type,
)
from clutchbot.processing.tournament import TournamentName
from clutchbot.processing.trim import trim_games
from tests.conftest import HEAD_TO_HEAD_ID, TEAM_VS_ID, WARMUP_MATCH_ID, load_detail

NAMES = {
    HEAD_TO_HEAD_ID: TournamentName("DBHS", "DBHS Tournament", "NINERIK", "Welter"),
    TEAM_VS_ID: TournamentName("CRTOA", "CRTOA Tournament", "Furball Pups", "Junior Sleuth Teds"),
    WARMUP_MATCH_ID: TournamentName("BLB", "BLB Tournament", "Hessen", "Berlin"),
}
NINERIK = 10549880
WELTER = 11552867


def result_of(match_id: int) -> MatchResult:
    detail = load_detail(match_id)
    games = trim_games(detail).games
    return build_result(games, NAMES[match_id], detail.users_by_id)


def h2h_games() -> tuple[MatchDetail, list[Game]]:
    detail = load_detail(HEAD_TO_HEAD_ID)
    return detail, trim_games(detail).games


@pytest.mark.parametrize(
    ("match_id", "match_type", "red", "blue", "score", "winner", "players"),
    [
        (HEAD_TO_HEAD_ID, MatchType.HEAD_TO_HEAD, "NINERIK", "Welter", (3, 7), Side.BLUE, (1, 1)),
        (
            TEAM_VS_ID,
            MatchType.TEAM_VS,
            "Furball Pups",
            "Junior Sleuth Teds",
            (7, 6),
            Side.RED,
            (4, 4),
        ),
        (WARMUP_MATCH_ID, MatchType.TEAM_VS, "Hessen", "Berlin", (5, 1), Side.RED, (5, 6)),
    ],
)
def test_fixture_results(
    match_id: int,
    match_type: MatchType,
    red: str,
    blue: str,
    score: tuple[int, int],
    winner: Side,
    players: tuple[int, int],
) -> None:
    result = result_of(match_id)

    assert result.match_type is match_type
    assert (result.red.name, result.blue.name) == (red, blue)
    assert (result.red_wins, result.blue_wins) == score
    assert result.winner is winner
    assert (len(result.red.player_ids), len(result.blue.player_ids)) == players


def test_team_vs_sums_team_scores() -> None:
    first = result_of(TEAM_VS_ID).games[0]  # 2 v 1 after a disconnect

    assert (first.red_score, first.blue_score) == (717836, 267756)
    assert first.winner is Side.RED


def test_a_player_belongs_to_the_team_they_played_most_maps_for() -> None:
    detail = load_detail(TEAM_VS_ID)
    games = trim_games(detail).games
    last = games[-1]

    def maps_played(user_id: int) -> int:
        return sum(any(s.user_id == user_id for s in game.scores) for game in games)

    blue_scores = [s for s in last.scores if s.match.team == "blue"]
    switched = max(blue_scores, key=lambda s: maps_played(s.user_id))
    assert maps_played(switched.user_id) >= 3  # so blue is still their main team
    # the last map is played for red, like a tiebreaker for fun
    scores = [
        s.model_copy(update={"match": s.match.model_copy(update={"team": "red"})})
        if s is switched
        else s
        for s in last.scores
    ]
    games[-1] = last.model_copy(update={"scores": scores})

    result = build_result(games, NAMES[TEAM_VS_ID], detail.users_by_id)

    assert switched.user_id in result.blue.player_ids
    assert switched.user_id not in result.red.player_ids
    # the map itself still counts the score for the team it was played for
    red_total = sum(s.total_score for s in scores if s.match.team == "red")
    assert result.games[-1].red_score == red_total


def test_head_to_head_scores_and_players() -> None:
    result = result_of(HEAD_TO_HEAD_ID)

    assert result.red.player_ids == (NINERIK,)
    assert result.blue.player_ids == (WELTER,)
    assert (result.games[0].red_score, result.games[0].blue_score) == (368958, 139417)
    assert result.team(Side.BLUE) is result.blue


def test_tied_map_has_no_winner() -> None:
    detail, games = h2h_games()
    tied_scores = [s.model_copy(update={"total_score": 100000}) for s in games[0].scores]
    games[0] = games[0].model_copy(update={"scores": tied_scores})

    result = build_result(games, NAMES[HEAD_TO_HEAD_ID], detail.users_by_id)

    assert result.games[0].winner is None
    assert (result.red_wins, result.blue_wins) == (2, 7)


def test_equal_map_wins_is_a_draw() -> None:
    detail, games = h2h_games()

    result = build_result(games[:4], NAMES[HEAD_TO_HEAD_ID], detail.users_by_id)

    assert (result.red_wins, result.blue_wins) == (2, 2)
    assert result.winner is None


def test_head_to_head_disconnect_counts_as_zero() -> None:
    detail, games = h2h_games()
    # map 2 was Welter's, but Welter has no score now
    only_ninerik = [s for s in games[1].scores if s.user_id == NINERIK]
    games[1] = games[1].model_copy(update={"scores": only_ninerik})

    result = build_result(games, NAMES[HEAD_TO_HEAD_ID], detail.users_by_id)

    assert (result.games[1].red_score, result.games[1].blue_score) == (154368, 0)
    assert result.games[1].winner is Side.RED


@pytest.mark.parametrize(
    ("lobby_names", "red_first"),
    [
        (("NINERIK", "Welter"), True),
        (("welter", "ninerik"), False),  # lobby order wins, case doesn't matter
        (("Someone", "Else"), True),  # no match: slot order (NINERIK is slot 0)
    ],
)
def test_head_to_head_player_order(lobby_names: tuple[str, str], red_first: bool) -> None:
    detail, games = h2h_games()
    names = TournamentName("DBHS", "DBHS Tournament", *lobby_names)

    result = build_result(games, names, detail.users_by_id)

    expected = ("NINERIK", "Welter") if red_first else ("Welter", "NINERIK")
    assert (result.red.name, result.blue.name) == expected
    assert result.winner is (Side.BLUE if red_first else Side.RED)  # Welter wins 7-3


def test_head_to_head_with_three_players_fails() -> None:
    detail, games = h2h_games()
    stranger = games[2].scores[0].model_copy(update={"user_id": 999})
    games[2] = games[2].model_copy(update={"scores": [stranger, games[2].scores[1]]})

    with pytest.raises(ResultError, match="found 3"):
        build_result(games, NAMES[HEAD_TO_HEAD_ID], detail.users_by_id)


def test_head_to_head_with_one_player_fails() -> None:
    detail, games = h2h_games()
    only_ninerik = [g.model_copy(update={"scores": g.scores[:1]}) for g in games]

    with pytest.raises(ResultError, match="found 1"):
        build_result(only_ninerik, NAMES[HEAD_TO_HEAD_ID], detail.users_by_id)


def test_match_type_detection() -> None:
    _, games = h2h_games()

    assert detect_match_type(games) is MatchType.HEAD_TO_HEAD
    tag = [g.model_copy(update={"team_type": "tag-team-vs"}) for g in games]
    assert detect_match_type(tag) is MatchType.TEAM_VS
    with pytest.raises(ResultError, match="mix"):
        detect_match_type([*games[:3], tag[3]])
    with pytest.raises(ResultError, match="No games"):
        detect_match_type([])
