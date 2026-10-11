from dataclasses import dataclass

from clutchbot.processing.pipeline import ProcessedMatch
from clutchbot.processing.result import Side
from clutchbot.render.browser import CardRenderer
from clutchbot.render.templating import render_template

EN_DASH = "\N{EN DASH}"

CHART_WIDTH = 1104
CHART_HEIGHT = 476
PLOT_LEFT = 40
PLOT_RIGHT = CHART_WIDTH - 40
PLOT_TOP = 70
PLOT_BOTTOM = 436
DOT_MARGIN = 34
MIN_SPAN = 2
TAG_HEIGHT = 24
TAG_GAP = 4

KINDS = {"clutch": "Clutch", "stomp": "Stomp", "huge": "Huge score"}
TAG_LABELS = {"clutch": "Clutch", "stomp": "Stomp", "huge": "Huge"}


@dataclass(frozen=True)
class Tag:
    kind: str
    label: str
    y: float


@dataclass(frozen=True)
class FlowPoint:
    map_number: int
    x: float
    y: float
    winner: str
    score: str
    label_y: float
    tags: list[Tag]
    guide_top: float
    guide_bottom: float


@dataclass(frozen=True)
class FlowCard:
    tournament: str
    match_link: str
    maps_played: int
    red_name: str
    blue_name: str
    red_wins: int
    blue_wins: int
    winner: str | None
    width: int
    height: int
    zero_y: float
    axis_y: int
    line: str
    area: str
    points: list[FlowPoint]
    legend: list[Tag]


def build_flow_card(processed: ProcessedMatch) -> FlowCard:
    result = processed.result
    kinds_by_map: dict[int, list[str]] = {}
    for clutch in processed.clutches:
        kinds_by_map.setdefault(clutch.map_number, []).append("clutch")
    if processed.stomp is not None:
        kinds_by_map.setdefault(processed.stomp.map_number, []).append("stomp")
    if processed.huge_score is not None:
        kinds_by_map.setdefault(processed.huge_score.map_number, []).append("huge")

    leads = [0]
    red_wins = blue_wins = 0
    scores = []
    for played in result.games:
        red_wins += played.winner is Side.RED
        blue_wins += played.winner is Side.BLUE
        leads.append(red_wins - blue_wins)
        scores.append(f"{red_wins}{EN_DASH}{blue_wins}")

    padding = max(MIN_SPAN - (max(leads) - min(leads)), 0) / 2
    top, bottom = max(leads) + padding, min(leads) - padding
    scale = (PLOT_BOTTOM - PLOT_TOP - 2 * DOT_MARGIN) / (top - bottom)
    step = (PLOT_RIGHT - PLOT_LEFT) / len(result.games)

    def x_at(index: int) -> float:
        return PLOT_LEFT + index * step

    def y_at(lead: float) -> float:
        return PLOT_TOP + DOT_MARGIN + (top - lead) * scale

    zero_y = y_at(0)

    points = []
    for map_number, played in enumerate(result.games, start=1):
        lead = leads[map_number]
        y = y_at(lead)
        kinds = kinds_by_map.get(map_number, [])
        label_y = y - 16 if lead >= 0 else y + 30
        points.append(
            FlowPoint(
                map_number=map_number,
                x=x_at(map_number),
                y=y,
                winner=played.winner.value if played.winner is not None else "tie",
                score=scores[map_number - 1],
                label_y=label_y,
                tags=[
                    Tag(kind, TAG_LABELS[kind], 4 + i * (TAG_HEIGHT + TAG_GAP))
                    for i, kind in enumerate(kinds)
                ],
                guide_top=4 + len(kinds) * (TAG_HEIGHT + TAG_GAP) - TAG_GAP,
                guide_bottom=label_y - 20 if lead >= 0 else y - 12,
            )
        )

    coords = [(x_at(i), y_at(lead)) for i, lead in enumerate(leads)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{line} {coords[-1][0]:.1f},{zero_y:.1f} {coords[0][0]:.1f},{zero_y:.1f}"
    used = {kind for kinds in kinds_by_map.values() for kind in kinds}
    winner = result.winner
    return FlowCard(
        tournament=processed.names.tournament,
        match_link=f"osu.ppy.sh/mp/{processed.detail.match.id}",
        maps_played=len(result.games),
        red_name=result.red.name,
        blue_name=result.blue.name,
        red_wins=result.red_wins,
        blue_wins=result.blue_wins,
        winner=winner.value if winner is not None else None,
        width=CHART_WIDTH,
        height=CHART_HEIGHT,
        zero_y=zero_y,
        axis_y=PLOT_BOTTOM + 30,
        line=line,
        area=area,
        points=points,
        legend=[Tag(kind, label, 0) for kind, label in KINDS.items() if kind in used],
    )


def flow_card_html(card: FlowCard) -> str:
    return render_template("flow.html", card=card)


async def render_flow_card(renderer: CardRenderer, card: FlowCard) -> bytes:
    return await renderer.render(flow_card_html(card))
