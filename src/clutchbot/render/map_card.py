import math
from dataclasses import dataclass
from enum import StrEnum

from clutchbot.osu.models import Score
from clutchbot.processing.clutch import ClutchMap
from clutchbot.processing.highlights import HugeScore, Stomp
from clutchbot.processing.pipeline import ProcessedMatch
from clutchbot.processing.result import GameResult, MatchType, Side
from clutchbot.render.browser import CardRenderer
from clutchbot.render.images import avatar_url
from clutchbot.render.templating import render_template

MAX_SCORE_ROWS = 4
LONG_TITLE = 24
NO_FAIL = "NF"


class MapKind(StrEnum):
    CLUTCH = "clutch"
    STOMP = "stomp"
    HUGE_SCORE = "huge"


LABELS = {
    MapKind.CLUTCH: "Clutch moment",
    MapKind.STOMP: "Stomp",
    MapKind.HUGE_SCORE: "Huge score",
}


@dataclass(frozen=True)
class ScoreRow:
    name: str
    avatar_url: str | None
    score: str
    accuracy: str
    combo: str
    mods: str  # without NF
    is_highlighted: bool  # the huge score


@dataclass(frozen=True)
class MapSide:
    side: str
    name: str
    total: str
    is_winner: bool
    players: list[ScoreRow]  # highest score first
    hidden_players: int


@dataclass(frozen=True)
class MapCard:
    kind: str
    label: str
    tournament: str
    match_link: str
    map_number: int
    maps_played: int
    title: str
    long_title: bool
    artist: str
    version: str
    stars: str
    mods: list[str]  # forced mod without NF
    cover_url: str | None
    headline: str  # the big number on the right
    # the line under it: "{before}<strong>{name}</strong>{after}"
    note_before: str
    note_name: str
    note_after: str
    red: MapSide
    blue: MapSide
    red_wins_after: int
    blue_wins_after: int
    is_head_to_head: bool


def build_clutch_card(
    processed: ProcessedMatch, clutch: ClutchMap, max_rows: int = MAX_SCORE_ROWS
) -> MapCard:
    gap = abs(clutch.game.red_score - clutch.game.blue_score)
    return _build_map_card(
        processed,
        MapKind.CLUTCH,
        clutch.map_number,
        clutch.game,
        headline=f"+{gap:,}",
        note=("won by ", processed.result.team(clutch.winner).name, ""),
        max_rows=max_rows,
    )


def build_stomp_card(
    processed: ProcessedMatch, stomp: Stomp, max_rows: int = MAX_SCORE_ROWS
) -> MapCard:
    return _build_map_card(
        processed,
        MapKind.STOMP,
        stomp.map_number,
        stomp.game,
        headline=_times(stomp.ratio),
        note=("won by ", processed.result.team(stomp.winner).name, ""),
        max_rows=max_rows,
    )


def build_huge_score_card(
    processed: ProcessedMatch, huge: HugeScore, max_rows: int = MAX_SCORE_ROWS
) -> MapCard:
    user = processed.detail.users_by_id.get(huge.score.user_id)
    name = user.username if user is not None else str(huge.score.user_id)
    return _build_map_card(
        processed,
        MapKind.HUGE_SCORE,
        huge.map_number,
        huge.game,
        headline=f"{huge.score.total_score:,}",
        note=("", name, f" · {_times(huge.ratio)} the next best"),
        max_rows=max_rows,
        highlight=huge.score,
    )


def _build_map_card(
    processed: ProcessedMatch,
    kind: MapKind,
    map_number: int,
    played: GameResult,
    *,
    headline: str,
    note: tuple[str, str, str],
    max_rows: int,
    highlight: Score | None = None,
) -> MapCard:
    result = processed.result
    users = processed.detail.users_by_id
    game = played.game

    def row(score: Score) -> ScoreRow:
        user = users.get(score.user_id)
        return ScoreRow(
            name=user.username if user is not None else str(score.user_id),
            avatar_url=avatar_url(user),
            score=f"{score.total_score:,}",
            accuracy=f"{score.accuracy * 100:.2f}%",
            combo=f"{score.max_combo:,}x",
            mods="".join(mod for mod in score.mod_acronyms if mod != NO_FAIL),
            is_highlighted=score is highlight,
        )

    def played_for(score: Score, side: Side) -> bool:
        if result.match_type is MatchType.TEAM_VS:
            return score.match.team == side.value
        return score.user_id in result.team(side).player_ids

    def side_panel(side: Side) -> MapSide:
        team = result.team(side)
        scores = sorted(
            (s for s in game.scores if played_for(s, side)),
            key=lambda s: s.total_score,
            reverse=True,
        )
        rows = [row(s) for s in scores]
        shown = rows if len(rows) <= max_rows else rows[: max_rows - 1]
        total = played.red_score if side is Side.RED else played.blue_score
        return MapSide(
            side=side.value,
            name=team.name,
            total=f"{total:,}",
            is_winner=played.winner is side,
            players=shown,
            hidden_players=len(rows) - len(shown),
        )

    games_so_far = result.games[:map_number]
    beatmap = game.beatmap
    title = beatmap.beatmapset.title if beatmap else f"Beatmap {game.beatmap_id}"
    note_before, note_name, note_after = note
    return MapCard(
        kind=kind.value,
        label=LABELS[kind],
        tournament=processed.names.tournament,
        match_link=f"osu.ppy.sh/mp/{processed.detail.match.id}",
        map_number=map_number,
        maps_played=len(result.games),
        title=title,
        long_title=len(title) > LONG_TITLE,
        artist=beatmap.beatmapset.artist if beatmap else "",
        version=beatmap.version if beatmap else "",
        stars=f"{beatmap.difficulty_rating:.2f}" if beatmap else "",
        mods=[mod for mod in game.mods if mod != NO_FAIL],
        cover_url=beatmap.beatmapset.covers.cover_2x if beatmap else None,
        headline=headline,
        note_before=note_before,
        note_name=note_name,
        note_after=note_after,
        red=side_panel(Side.RED),
        blue=side_panel(Side.BLUE),
        red_wins_after=sum(1 for g in games_so_far if g.winner is Side.RED),
        blue_wins_after=sum(1 for g in games_so_far if g.winner is Side.BLUE),
        is_head_to_head=result.match_type is MatchType.HEAD_TO_HEAD,
    )


def _times(ratio: float) -> str:
    if math.isinf(ratio):
        return "\N{INFINITY}" + "\N{MULTIPLICATION SIGN}"
    return f"{ratio:.1f}{'\N{MULTIPLICATION SIGN}'}"


def map_card_html(card: MapCard) -> str:
    return render_template("map.html", card=card)


async def render_map_card(renderer: CardRenderer, card: MapCard) -> bytes:
    return await renderer.render(map_card_html(card))
