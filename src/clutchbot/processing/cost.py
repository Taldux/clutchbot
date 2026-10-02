"""Match costs"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from clutchbot.osu.models import Game
from clutchbot.processing.result import MatchResult, Side


@dataclass(frozen=True)
class PlayerCost:
    user_id: int
    side: Side
    cost: float
    maps_played: int


def match_costs(result: MatchResult) -> list[PlayerCost]:
    games = [game_result.game for game_result in result.games]
    tiebreaker = is_tiebreaker(result)
    costs = []
    for side in Side:
        for user_id in result.team(side).player_ids:
            maps_played = sum(1 for game in games if _score_of(game, user_id) is not None)
            cost = _player_cost(user_id, games, maps_played, tiebreaker)
            costs.append(PlayerCost(user_id, side, cost, maps_played))
    return costs


def is_tiebreaker(result: MatchResult) -> bool:
    if len(result.games) < 2:
        return False
    before_last = result.games[:-1]
    red_wins = sum(1 for game in before_last if game.winner is Side.RED)
    blue_wins = sum(1 for game in before_last if game.winner is Side.BLUE)
    return red_wins == blue_wins


def _player_cost(user_id: int, games: Sequence[Game], maps_played: int, tiebreaker: bool) -> float:
    if maps_played == 0:
        return 0.0

    # maps the player missed add 0 but still count in the division by maps_played
    performance = sum(_relative_score(game, user_id) for game in games) / maps_played + 0.5

    total = len(games)
    exponent = (maps_played - 1) / (total - 1) if total > 1 else 1.0
    participation = math.pow(1.5, math.pow(exponent, 0.6))

    combos = {
        "+".join(sorted(score.mod_acronyms))
        for game in games
        for score in game.scores
        if score.user_id == user_id
    }
    mod_bonus = 1 + 0.02 * max(len(combos) - 2, 0)

    tiebreaker_bonus = min(0.5, 0.25 * _relative_score(games[-1], user_id)) if tiebreaker else 0.0

    return _round_cost(performance * participation * mod_bonus + tiebreaker_bonus)


def _score_of(game: Game, user_id: int) -> int | None:
    return next((s.total_score for s in game.scores if s.user_id == user_id), None)


def _relative_score(game: Game, user_id: int) -> float:
    score = _score_of(game, user_id)
    if score is None or not game.scores:
        return 0.0
    average = sum(s.total_score for s in game.scores) / len(game.scores)
    if average == 0:
        return 0.0
    return score / average


def _round_cost(cost: float) -> float:
    hundredths = Decimal(cost * 100).to_integral_value(rounding=ROUND_HALF_UP)
    return float(hundredths) / 100
