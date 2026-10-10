from clutchbot.osu.models import Game, Mod
from clutchbot.processing.mods import apply_ez_multiplier
from clutchbot.processing.pipeline import process_match
from clutchbot.processing.result import Side
from clutchbot.processing.tournament import TournamentName
from tests.conftest import HEAD_TO_HEAD_ID, load_detail, replace_games

DBHS = TournamentName("DBHS", "DBHS Tournament", "NINERIK", "Welter")
# map 4 of DBHS: the loser has 191,839 against 223,308, and 1.5x that is enough to win
MAP_4 = 3
LOSER = 10549880


def with_ez(game: Game, user_id: int) -> Game:
    scores = [
        score.model_copy(update={"mods": [*score.mods, Mod(acronym="EZ")]})
        if score.user_id == user_id
        else score
        for score in game.scores
    ]
    return game.model_copy(update={"scores": scores})


def scores_by_user(game: Game) -> dict[int, int]:
    return {score.user_id: score.total_score for score in game.scores}


def test_only_ez_scores_are_multiplied() -> None:
    game = with_ez(load_detail(HEAD_TO_HEAD_ID).games[MAP_4], LOSER)

    (multiplied,) = apply_ez_multiplier([game], 1.5)

    before, after = scores_by_user(game), scores_by_user(multiplied)
    assert after[LOSER] == int(191_839 * 1.5)
    assert {u: s for u, s in after.items() if u != LOSER} == {
        u: s for u, s in before.items() if u != LOSER
    }


def test_nothing_changes_when_ez_is_forced_or_the_multiplier_is_off() -> None:
    game = with_ez(load_detail(HEAD_TO_HEAD_ID).games[MAP_4], LOSER)
    forced = game.model_copy(update={"mods": [*game.mods, "EZ"]})

    assert apply_ez_multiplier([forced], 1.5) == [forced]
    assert apply_ez_multiplier([game], 1) == [game]


def test_an_ez_score_can_win_the_map() -> None:
    detail = load_detail(HEAD_TO_HEAD_ID)
    changed = replace_games(detail, {MAP_4: with_ez(detail.games[MAP_4], LOSER)})

    plain = process_match(changed, DBHS, ez_multiplier=1).result
    multiplied = process_match(changed, DBHS).result

    ez_side = Side.RED if LOSER in plain.red.player_ids else Side.BLUE
    assert plain.games[MAP_4].winner is not ez_side
    assert multiplied.games[MAP_4].winner is ez_side
