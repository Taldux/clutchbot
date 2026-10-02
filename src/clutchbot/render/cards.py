from dataclasses import dataclass

from clutchbot.processing.pipeline import ProcessedMatch
from clutchbot.render.browser import CardRenderer
from clutchbot.render.clutch_card import render_clutch_cards
from clutchbot.render.result_card import render_result_card


@dataclass(frozen=True)
class RenderedCard:
    file_name: str  # e.g. "result.png" or "clutch-7.png"
    png: bytes


@dataclass(frozen=True)
class MatchCards:
    result: RenderedCard
    clutches: list[RenderedCard]  # in play order

    @property
    def all(self) -> list[RenderedCard]:
        return [self.result, *self.clutches]


async def render_match_cards(renderer: CardRenderer, processed: ProcessedMatch) -> MatchCards:
    result = RenderedCard("result.png", await render_result_card(renderer, processed))
    clutch_pngs = await render_clutch_cards(renderer, processed)
    clutches = [
        RenderedCard(f"clutch-{clutch.map_number}.png", png)
        for clutch, png in zip(processed.clutches, clutch_pngs, strict=True)
    ]
    return MatchCards(result, clutches)
