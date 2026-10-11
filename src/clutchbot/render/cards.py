from dataclasses import dataclass

from clutchbot.processing.pipeline import ProcessedMatch
from clutchbot.render.browser import CardRenderer
from clutchbot.render.map_card import (
    MapCard,
    build_clutch_card,
    build_huge_score_card,
    build_stomp_card,
    render_map_card,
)
from clutchbot.render.result_card import render_result_card


@dataclass(frozen=True)
class RenderedCard:
    file_name: str  # e.g. "result.png" or "clutch-7.png"
    png: bytes
    map_number: int | None = None


@dataclass(frozen=True)
class MatchCards:
    result: RenderedCard
    clutches: list[RenderedCard]  # in play order
    stomp: RenderedCard | None = None
    huge_score: RenderedCard | None = None

    @property
    def maps(self) -> list[RenderedCard]:
        cards = [*self.clutches, self.stomp, self.huge_score]
        return sorted((c for c in cards if c is not None), key=lambda c: c.map_number or 0)

    @property
    def all(self) -> list[RenderedCard]:
        return [self.result, *self.maps]


async def render_match_cards(renderer: CardRenderer, processed: ProcessedMatch) -> MatchCards:
    async def render(card: MapCard) -> RenderedCard:
        png = await render_map_card(renderer, card)
        return RenderedCard(f"{card.kind}-{card.map_number}.png", png, card.map_number)

    result = RenderedCard("result.png", await render_result_card(renderer, processed))
    clutches = [await render(build_clutch_card(processed, c)) for c in processed.clutches]
    huge = processed.huge_score
    stomp = processed.stomp
    if stomp is not None and huge is not None and stomp.map_number == huge.map_number:
        stomp = None
    return MatchCards(
        result,
        clutches,
        stomp=await render(build_stomp_card(processed, stomp)) if stomp else None,
        huge_score=await render(build_huge_score_card(processed, huge)) if huge else None,
    )
