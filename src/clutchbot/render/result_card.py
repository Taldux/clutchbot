from dataclasses import dataclass

from clutchbot.processing.pipeline import ProcessedMatch
from clutchbot.processing.result import MatchType, Side
from clutchbot.render.browser import CardRenderer
from clutchbot.render.images import avatar_url
from clutchbot.render.templating import render_template

MAX_PLAYER_ROWS = 8
LONG_TEAM_NAME = 16


@dataclass(frozen=True)
class PlayerRow:
    name: str
    avatar_url: str | None
    cost: str
    maps_played: int
    is_mvp: bool


@dataclass(frozen=True)
class TeamPanel:
    side: str
    name: str
    long_name: bool
    wins: int
    is_winner: bool
    players: list[PlayerRow]  # best match cost first
    hidden_players: int


@dataclass(frozen=True)
class ResultCard:
    tournament: str
    date: str
    match_link: str
    red: TeamPanel
    blue: TeamPanel
    is_draw: bool
    maps_played: int
    clutch_maps: int


def build_result_card(processed: ProcessedMatch, max_rows: int = MAX_PLAYER_ROWS) -> ResultCard:
    result = processed.result
    users = processed.detail.users_by_id
    best_cost = max((c.cost for c in processed.costs), default=None)

    def panel(side: Side) -> TeamPanel:
        costs = sorted(
            (c for c in processed.costs if c.side is side), key=lambda c: c.cost, reverse=True
        )
        rows = [
            PlayerRow(
                name=users[c.user_id].username if c.user_id in users else str(c.user_id),
                avatar_url=avatar_url(users.get(c.user_id)),
                cost=f"{c.cost:.2f}",
                maps_played=c.maps_played,
                is_mvp=c.cost == best_cost,
            )
            for c in costs
        ]
        shown = rows if len(rows) <= max_rows else rows[: max_rows - 1]
        team = result.team(side)
        return TeamPanel(
            side=side.value,
            name=team.name,
            long_name=len(team.name) > LONG_TEAM_NAME,
            wins=result.red_wins if side is Side.RED else result.blue_wins,
            is_winner=result.winner is side,
            players=shown,
            hidden_players=len(rows) - len(shown),
        )

    return ResultCard(
        tournament=processed.names.tournament,
        date=_match_date(processed),
        match_link=_match_link(processed),
        red=panel(Side.RED),
        blue=panel(Side.BLUE),
        is_draw=result.winner is None,
        maps_played=len(result.games),
        clutch_maps=len(processed.clutches),
    )


@dataclass(frozen=True)
class PlayerPanel:
    side: str
    name: str
    avatar_url: str | None
    long_name: bool
    wins: int
    is_winner: bool
    cost: str
    has_best_cost: bool
    average_score: str
    average_accuracy: str


@dataclass(frozen=True)
class HeadToHeadCard:
    tournament: str
    date: str
    match_link: str
    red: PlayerPanel
    blue: PlayerPanel
    is_draw: bool
    maps_played: int
    clutch_maps: int
    cost_ratio: str | None


def build_head_to_head_card(processed: ProcessedMatch) -> HeadToHeadCard:
    result = processed.result
    users = processed.detail.users_by_id
    costs = {c.side: c for c in processed.costs}
    best_cost = max(c.cost for c in costs.values())

    def panel(side: Side) -> PlayerPanel:
        player = costs[side]
        user = users.get(player.user_id)
        name = user.username if user is not None else result.team(side).name
        scores = [
            score
            for game in result.games
            for score in game.game.scores
            if score.user_id == player.user_id
        ]
        average_score = sum(s.total_score for s in scores) / len(scores) if scores else 0
        average_accuracy = sum(s.accuracy for s in scores) / len(scores) if scores else 0
        return PlayerPanel(
            side=side.value,
            name=name,
            avatar_url=avatar_url(user),
            long_name=len(name) > LONG_TEAM_NAME,
            wins=result.red_wins if side is Side.RED else result.blue_wins,
            is_winner=result.winner is side,
            cost=f"{player.cost:.2f}",
            has_best_cost=player.cost == best_cost,
            average_score=f"{average_score:,.0f}",
            average_accuracy=f"{average_accuracy * 100:.2f}%",
        )

    low, high = sorted(c.cost for c in costs.values())
    return HeadToHeadCard(
        tournament=processed.names.tournament,
        date=_match_date(processed),
        match_link=_match_link(processed),
        red=panel(Side.RED),
        blue=panel(Side.BLUE),
        is_draw=result.winner is None,
        maps_played=len(result.games),
        clutch_maps=len(processed.clutches),
        cost_ratio=f"{high / low:.2f}" if low > 0 else None,
    )


def result_card_html(card: ResultCard | HeadToHeadCard) -> str:
    template = "result.html" if isinstance(card, ResultCard) else "head_to_head.html"
    return render_template(template, card=card)


async def render_result_card(renderer: CardRenderer, processed: ProcessedMatch) -> bytes:
    card: ResultCard | HeadToHeadCard
    if processed.result.match_type is MatchType.HEAD_TO_HEAD:
        card = build_head_to_head_card(processed)
    else:
        card = build_result_card(processed)
    return await renderer.render(result_card_html(card))


def _match_date(processed: ProcessedMatch) -> str:
    start = processed.detail.match.start_time
    return f"{start.day} {start:%B %Y}"


def _match_link(processed: ProcessedMatch) -> str:
    return f"osu.ppy.sh/mp/{processed.detail.match.id}"
