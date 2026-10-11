import math
from dataclasses import dataclass

from clutchbot.osu.models import Score
from clutchbot.processing.clutch import ClutchMap
from clutchbot.processing.result import GameResult, MatchResult, MatchType, Side

STOMP_RATIO = 2
HUGE_SCORE_RATIO = 2


@dataclass(frozen=True)
class Stomp:
    map_number: int
    game: GameResult
    winner: Side
    ratio: float


@dataclass(frozen=True)
class HugeScore:
    map_number: int
    game: GameResult
    score: Score
    side: Side
    ratio: float


def biggest_stomp(result: MatchResult) -> Stomp | None:
    # The map won by the biggest ratio, if the winners scored at least twice the losers
    stomps = []
    for map_number, game in enumerate(result.games, start=1):
        winner = game.winner
        if winner is None:
            continue
        if winner is Side.RED:
            winning, losing = game.red_score, game.blue_score
        else:
            winning, losing = game.blue_score, game.red_score
        if winning >= STOMP_RATIO * losing:
            stomps.append(Stomp(map_number, game, winner, _ratio(winning, losing)))
    return max(stomps, key=lambda s: s.ratio, default=None)


def biggest_huge_score(result: MatchResult, clutches: list[ClutchMap]) -> HugeScore | None:
    # The score furthest ahead of everyone else in its lobby (at least 2x as big as everyone else)
    # skipped in 1v1
    if result.match_type is not MatchType.TEAM_VS:
        return None
    clutch_numbers = {clutch.map_number for clutch in clutches}
    huge = []
    for map_number, game in enumerate(result.games, start=1):
        if map_number in clutch_numbers or len(game.game.scores) < 2:
            continue
        top, *rest = sorted(game.game.scores, key=lambda s: s.total_score, reverse=True)
        next_biggest = rest[0].total_score
        if top.total_score > 0 and top.total_score >= HUGE_SCORE_RATIO * next_biggest:
            side = Side.RED if top.match.team == Side.RED.value else Side.BLUE
            ratio = _ratio(top.total_score, next_biggest)
            huge.append(HugeScore(map_number, game, top, side, ratio))
    return max(huge, key=lambda h: h.ratio, default=None)


def _ratio(bigger: int, smaller: int) -> float:
    return bigger / smaller if smaller else math.inf
