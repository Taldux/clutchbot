from dataclasses import dataclass, replace

from clutchbot.osu.models import MatchDetail
from clutchbot.processing.clutch import DEFAULT_THRESHOLD_PERCENT, ClutchMap, clutch_maps
from clutchbot.processing.cost import PlayerCost, match_costs
from clutchbot.processing.mods import DEFAULT_EZ_MULTIPLIER, apply_ez_multiplier
from clutchbot.processing.result import MatchResult, ResultError, build_result
from clutchbot.processing.tournament import TournamentName
from clutchbot.processing.trim import (
    SkipReason,
    TrimmedGames,
    ends_with_fun_tiebreaker,
    trim_games,
)


class ProcessingError(Exception):
    pass


@dataclass(frozen=True)
class ProcessedMatch:
    detail: MatchDetail
    names: TournamentName
    trimmed: TrimmedGames
    result: MatchResult
    costs: list[PlayerCost]
    clutches: list[ClutchMap]


def process_match(
    detail: MatchDetail,
    names: TournamentName,
    *,
    warmup_games_checked: int = 2,
    clutch_threshold_percent: float = DEFAULT_THRESHOLD_PERCENT,
    ez_multiplier: float = DEFAULT_EZ_MULTIPLIER,
) -> ProcessedMatch:
    if not detail.match.is_finished:
        raise ProcessingError(f"Match {detail.match.id} isn't finished yet")

    trimmed = trim_games(detail, warmup_games_checked)
    trimmed = replace(trimmed, games=apply_ez_multiplier(trimmed.games, ez_multiplier))
    result = _build_result(detail, trimmed, names)
    if ends_with_fun_tiebreaker(result):
        trimmed = trimmed.without_last(SkipReason.FUN_TIEBREAKER)
        result = _build_result(detail, trimmed, names)

    return ProcessedMatch(
        detail=detail,
        names=names,
        trimmed=trimmed,
        result=result,
        costs=match_costs(result),
        clutches=clutch_maps(result, clutch_threshold_percent),
    )


def _build_result(detail: MatchDetail, trimmed: TrimmedGames, names: TournamentName) -> MatchResult:
    try:
        return build_result(trimmed.games, names, detail.users_by_id)
    except ResultError as exc:
        raise ProcessingError(f"Match {detail.match.id}: {exc}") from exc
