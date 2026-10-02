from dataclasses import dataclass

from clutchbot.osu.models import Score
from clutchbot.processing.clutch import ClutchMap
from clutchbot.processing.pipeline import ProcessedMatch
from clutchbot.processing.result import MatchType, Side
from clutchbot.render.browser import CardRenderer
from clutchbot.render.images import avatar_url
from clutchbot.render.templating import render_template

MAX_SCORE_ROWS = 4
LONG_TITLE = 24
NO_FAIL = "NF"


@dataclass(frozen=True)
class ScoreRow:
    name: str
    avatar_url: str | None
    score: str
    accuracy: str
    combo: str
    mods: str  # without NF


@dataclass(frozen=True)
class ClutchSide:
    side: str
    name: str
    total: str
    is_winner: bool
    players: list[ScoreRow]  # highest score first
    hidden_players: int


@dataclass(frozen=True)
class ClutchCard:
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
    margin: str
    winner_name: str
    red: ClutchSide
    blue: ClutchSide
    red_wins_after: int
    blue_wins_after: int
    is_head_to_head: bool


def build_clutch_card(
    processed: ProcessedMatch, clutch: ClutchMap, max_rows: int = MAX_SCORE_ROWS
) -> ClutchCard:
    result = processed.result
    users = processed.detail.users_by_id
    game = clutch.game.game

    def row(score: Score) -> ScoreRow:
        user = users.get(score.user_id)
        return ScoreRow(
            name=user.username if user is not None else str(score.user_id),
            avatar_url=avatar_url(user),
            score=f"{score.total_score:,}",
            accuracy=f"{score.accuracy * 100:.2f}%",
            combo=f"{score.max_combo:,}x",
            mods="".join(mod for mod in score.mod_acronyms if mod != NO_FAIL),
        )

    def played_for(score: Score, side: Side) -> bool:
        if result.match_type is MatchType.TEAM_VS:
            return score.match.team == side.value
        return score.user_id in result.team(side).player_ids

    def side_panel(side: Side) -> ClutchSide:
        team = result.team(side)
        scores = sorted(
            (s for s in game.scores if played_for(s, side)),
            key=lambda s: s.total_score,
            reverse=True,
        )
        rows = [row(s) for s in scores]
        shown = rows if len(rows) <= max_rows else rows[: max_rows - 1]
        total = clutch.game.red_score if side is Side.RED else clutch.game.blue_score
        return ClutchSide(
            side=side.value,
            name=team.name,
            total=f"{total:,}",
            is_winner=clutch.winner is side,
            players=shown,
            hidden_players=len(rows) - len(shown),
        )

    games_so_far = result.games[: clutch.map_number]
    beatmap = game.beatmap
    title = beatmap.beatmapset.title if beatmap else f"Beatmap {game.beatmap_id}"
    return ClutchCard(
        tournament=processed.names.tournament,
        match_link=f"osu.ppy.sh/mp/{processed.detail.match.id}",
        map_number=clutch.map_number,
        maps_played=len(result.games),
        title=title,
        long_title=len(title) > LONG_TITLE,
        artist=beatmap.beatmapset.artist if beatmap else "",
        version=beatmap.version if beatmap else "",
        stars=f"{beatmap.difficulty_rating:.2f}" if beatmap else "",
        mods=[mod for mod in game.mods if mod != NO_FAIL],
        cover_url=beatmap.beatmapset.covers.cover_2x if beatmap else None,
        margin=f"{clutch.margin_percent:.2f}%",
        winner_name=result.team(clutch.winner).name,
        red=side_panel(Side.RED),
        blue=side_panel(Side.BLUE),
        red_wins_after=sum(1 for g in games_so_far if g.winner is Side.RED),
        blue_wins_after=sum(1 for g in games_so_far if g.winner is Side.BLUE),
        is_head_to_head=result.match_type is MatchType.HEAD_TO_HEAD,
    )


def clutch_card_html(card: ClutchCard) -> str:
    return render_template("clutch.html", card=card)


async def render_clutch_cards(renderer: CardRenderer, processed: ProcessedMatch) -> list[bytes]:
    return [
        await renderer.render(clutch_card_html(build_clutch_card(processed, clutch)))
        for clutch in processed.clutches
    ]
