"""Drop warmups, forfun TBs, aborts"""

from collections import Counter
from dataclasses import dataclass, replace
from enum import StrEnum

from clutchbot.osu.models import Game, MatchDetail
from clutchbot.processing.result import MatchResult, MatchType, Side

NO_FAIL = "NF"
HOST_CHANGED = "host-changed"


class SkipReason(StrEnum):
    NO_SCORES = "no scores"
    WARMUP = "warmup"
    FUN_TIEBREAKER = "tiebreaker for fun"


@dataclass(frozen=True)
class SkippedGame:
    game: Game
    reason: SkipReason


@dataclass(frozen=True)
class TrimmedGames:
    games: list[Game]
    skipped: list[SkippedGame]

    def without_last(self, reason: SkipReason) -> "TrimmedGames":
        return replace(
            self,
            games=self.games[:-1],
            skipped=[*self.skipped, SkippedGame(self.games[-1], reason)],
        )


def is_warmup(game: Game) -> bool:
    return not all(NO_FAIL in score.mod_acronyms for score in game.scores)


def trim_games(detail: MatchDetail, warmup_games_checked: int = 2) -> TrimmedGames:
    games: list[Game] = []
    skipped: list[SkippedGame] = []
    warmups_left = warmup_games_checked
    host_warmups_left = _games_before_last_host_change(detail, warmup_games_checked)

    for game in detail.games:
        if not game.scores:
            skipped.append(SkippedGame(game, SkipReason.NO_SCORES))
        elif warmups_left > 0 and (host_warmups_left > 0 or is_warmup(game)):
            skipped.append(SkippedGame(game, SkipReason.WARMUP))
            warmups_left -= 1
            host_warmups_left -= 1
        else:
            warmups_left = 0  # the first real map, no warmups after it
            games.append(game)

    trimmed = TrimmedGames(games, skipped)
    if len(games) >= 2 and (is_warmup(games[-1]) or _has_extra_players(games)):
        return trimmed.without_last(SkipReason.FUN_TIEBREAKER)
    return trimmed


def _games_before_last_host_change(detail: MatchDetail, warmup_games_checked: int) -> int:
    played = 0
    before_last_change = 0
    for event in detail.events:
        if event.game is not None and event.game.scores:
            played += 1
        elif event.detail.type == HOST_CHANGED:
            before_last_change = played
    return before_last_change if before_last_change <= warmup_games_checked else 0


def _has_extra_players(games: list[Game]) -> bool:
    *before, last = games
    return _largest_side(last) > max(_largest_side(game) for game in before)


def _largest_side(game: Game) -> int:
    return max(Counter(score.match.team for score in game.scores).values())


def ends_with_fun_tiebreaker(result: MatchResult) -> bool:
    if len(result.games) < 2:
        return False
    *before, last = result.games
    red_wins = sum(1 for game in before if game.winner is Side.RED)
    blue_wins = sum(1 for game in before if game.winner is Side.BLUE)
    return red_wins != blue_wins and _is_uneven(last.game, result.match_type)


def _is_uneven(game: Game, match_type: MatchType) -> bool:
    if match_type is MatchType.HEAD_TO_HEAD:
        return len({score.user_id for score in game.scores}) < 2
    teams = [score.match.team for score in game.scores]
    return teams.count("red") != teams.count("blue")
