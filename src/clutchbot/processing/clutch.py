from dataclasses import dataclass

from clutchbot.processing.result import GameResult, MatchResult, Side

DEFAULT_THRESHOLD_PERCENT = 5.0


@dataclass(frozen=True)
class ClutchMap:
    map_number: int
    game: GameResult
    winner: Side
    margin_percent: float  # (winner - loser) / winner * 100


def clutch_maps(
    result: MatchResult, threshold_percent: float = DEFAULT_THRESHOLD_PERCENT
) -> list[ClutchMap]:
    """Maps where the loser finished within `threshold_percent` of the winner's score"""
    clutches = []
    for map_number, game in enumerate(result.games, start=1):
        winner = game.winner
        if winner is None:  # tied map (lol)
            continue
        if winner is Side.RED:
            winning, losing = game.red_score, game.blue_score
        else:
            winning, losing = game.blue_score, game.red_score
        # multiply first for no losses to float rounding
        margin = (winning - losing) * 100 / winning
        if margin <= threshold_percent:
            clutches.append(ClutchMap(map_number, game, winner, margin))
    return clutches
