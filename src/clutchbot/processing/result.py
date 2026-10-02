from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from clutchbot.osu.models import Game, User
from clutchbot.processing.tournament import TournamentName


class MatchType(StrEnum):
    TEAM_VS = "team-vs"
    HEAD_TO_HEAD = "head-to-head"


class Side(StrEnum):
    RED = "red"
    BLUE = "blue"


_MATCH_TYPES = {
    "team-vs": MatchType.TEAM_VS,
    "tag-team-vs": MatchType.TEAM_VS,
    "head-to-head": MatchType.HEAD_TO_HEAD,
    "tag-coop": MatchType.HEAD_TO_HEAD,
}


class ResultError(Exception):
    pass


@dataclass(frozen=True)
class Team:
    name: str
    player_ids: tuple[int, ...]  # one player in 1v1


@dataclass(frozen=True)
class GameResult:
    game: Game
    red_score: int
    blue_score: int

    @property
    def winner(self) -> Side | None:
        if self.red_score > self.blue_score:
            return Side.RED
        if self.blue_score > self.red_score:
            return Side.BLUE
        return None


@dataclass(frozen=True)
class MatchResult:
    match_type: MatchType
    red: Team
    blue: Team
    games: tuple[GameResult, ...]

    @property
    def red_wins(self) -> int:
        return sum(1 for game in self.games if game.winner is Side.RED)

    @property
    def blue_wins(self) -> int:
        return sum(1 for game in self.games if game.winner is Side.BLUE)

    @property
    def winner(self) -> Side | None:
        if self.red_wins > self.blue_wins:
            return Side.RED
        if self.blue_wins > self.red_wins:
            return Side.BLUE
        return None

    def team(self, side: Side) -> Team:
        return self.red if side is Side.RED else self.blue


def detect_match_type(games: Sequence[Game]) -> MatchType:
    if not games:
        raise ResultError("No games to score")
    types = {_MATCH_TYPES[game.team_type] for game in games}
    if len(types) > 1:
        raise ResultError(f"Games mix match types: {sorted(types)}")
    return types.pop()


def build_result(
    games: Sequence[Game], names: TournamentName, users: Mapping[int, User]
) -> MatchResult:
    match_type = detect_match_type(games)
    if match_type is MatchType.TEAM_VS:
        return _team_vs_result(games, names)
    return _head_to_head_result(games, names, users)


def _team_vs_result(games: Sequence[Game], names: TournamentName) -> MatchResult:
    sides_played: dict[int, Counter[Side]] = {}
    results = []
    for game in games:
        red_score = blue_score = 0
        for score in game.scores:
            if score.match.team == "red":
                red_score += score.total_score
            elif score.match.team == "blue":
                blue_score += score.total_score
            else:
                continue
            sides_played.setdefault(score.user_id, Counter())[Side(score.match.team)] += 1
        results.append(GameResult(game, red_score, blue_score))

    # Someone can play a map for the other team (a warmup that wasn't dropped, a tiebreaker
    # for fun), so each player belongs to the side they played most maps for. On a tie,
    # most_common keeps the side they played for first
    main_side = {player: counts.most_common(1)[0][0] for player, counts in sides_played.items()}
    return MatchResult(
        MatchType.TEAM_VS,
        Team(names.red_team, tuple(p for p, side in main_side.items() if side is Side.RED)),
        Team(names.blue_team, tuple(p for p, side in main_side.items() if side is Side.BLUE)),
        tuple(results),
    )


def _head_to_head_result(
    games: Sequence[Game], names: TournamentName, users: Mapping[int, User]
) -> MatchResult:
    red_id, blue_id = _order_players(games, names, users)
    results = []
    for game in games:
        scores = {score.user_id: score.total_score for score in game.scores}
        # disconnect counts as 0
        results.append(GameResult(game, scores.get(red_id, 0), scores.get(blue_id, 0)))

    return MatchResult(
        MatchType.HEAD_TO_HEAD,
        Team(_username(red_id, users, names.red_team), (red_id,)),
        Team(_username(blue_id, users, names.blue_team), (blue_id,)),
        tuple(results),
    )


def _order_players(
    games: Sequence[Game], names: TournamentName, users: Mapping[int, User]
) -> tuple[int, int]:
    seen: dict[int, None] = {}  # first appearance
    for game in games:
        for score in sorted(game.scores, key=lambda s: s.match.slot):
            seen[score.user_id] = None
    players = list(seen)
    if len(players) != 2:
        raise ResultError(f"Head-to-head needs exactly 2 players, found {len(players)}")
    first, second = players

    lobby_order = (names.red_team.casefold(), names.blue_team.casefold())
    usernames = tuple(
        users[player].username.casefold() if player in users else "" for player in players
    )
    if usernames == lobby_order[::-1]:
        return second, first
    return first, second


def _username(user_id: int, users: Mapping[int, User], fallback: str) -> str:
    user = users.get(user_id)
    return user.username if user is not None else fallback
