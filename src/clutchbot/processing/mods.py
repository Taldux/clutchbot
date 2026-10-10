from clutchbot.osu.models import Game

DEFAULT_EZ_MULTIPLIER = 1.5


def apply_ez_multiplier(games: list[Game], multiplier: float) -> list[Game]:
    if multiplier == 1:
        return games
    return [_multiply_ez_scores(game, multiplier) for game in games]


def _multiply_ez_scores(game: Game, multiplier: float) -> Game:
    if "EZ" in game.mods:
        return game
    scores = [
        score.model_copy(update={"total_score": int(score.total_score * multiplier)})
        if "EZ" in score.mod_acronyms
        else score
        for score in game.scores
    ]
    return game.model_copy(update={"scores": scores})
