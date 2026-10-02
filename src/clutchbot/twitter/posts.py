from dataclasses import dataclass

from clutchbot.processing.pipeline import ProcessedMatch
from clutchbot.processing.result import Side
from clutchbot.render.cards import MatchCards, RenderedCard

MAX_IMAGES_PER_POST = 4
# So that it doesn't go past twitter character limit
MAX_NAME_LENGTH = 40
HASHTAG = "#osugame"

TROPHY = "\N{TROPHY}"
LIGHTNING = "\N{HIGH VOLTAGE SIGN}"
EN_DASH = "\N{EN DASH}"
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"


@dataclass(frozen=True)
class PlannedPost:
    text: str
    cards: list[RenderedCard]


def plan_posts(processed: ProcessedMatch, cards: MatchCards) -> list[PlannedPost]:
    posts = [PlannedPost(_result_text(processed), [cards.result])]

    chunks = [
        cards.clutches[start : start + MAX_IMAGES_PER_POST]
        for start in range(0, len(cards.clutches), MAX_IMAGES_PER_POST)
    ]
    for number, chunk in enumerate(chunks, start=1):
        part = f" ({number}/{len(chunks)})" if len(chunks) > 1 else ""
        posts.append(PlannedPost(_clutch_text(processed, len(cards.clutches)) + part, chunk))
    return posts


def _result_text(processed: ProcessedMatch) -> str:
    result = processed.result
    red, blue = _short(result.red.name), _short(result.blue.name)
    winner = result.winner
    if winner is None:
        line = f"{red} and {blue} draw {result.red_wins}{EN_DASH}{result.blue_wins}"
    else:
        loser = Side.BLUE if winner is Side.RED else Side.RED
        winner_wins = result.red_wins if winner is Side.RED else result.blue_wins
        loser_wins = result.blue_wins if winner is Side.RED else result.red_wins
        line = (
            f"{_short(result.team(winner).name)} defeats {_short(result.team(loser).name)} "
            f"{winner_wins}{EN_DASH}{loser_wins}"
        )
    return f"{TROPHY} {_short(processed.names.tournament)}\n{line}\n{HASHTAG}"


def _clutch_text(processed: ProcessedMatch, count: int) -> str:
    result = processed.result
    maps = "clutch map" if count == 1 else "clutch maps"
    return f"{LIGHTNING} {count} {maps} in {_short(result.red.name)} vs {_short(result.blue.name)}"


def _short(name: str) -> str:
    if len(name) <= MAX_NAME_LENGTH:
        return name
    return name[: MAX_NAME_LENGTH - 1].rstrip() + ELLIPSIS


def format_plan(posts: list[PlannedPost]) -> str:
    blocks = []
    for number, post in enumerate(posts, start=1):
        reply = f", reply to post {number - 1}" if number > 1 else ""
        images = ", ".join(card.file_name for card in post.cards)
        body = "\n".join(f"  | {line}" for line in post.text.splitlines())
        blocks.append(f"Post {number}{reply} [{images}]\n{body}")
    return "\n\n".join(blocks)
