from clutchbot.render.browser import CardRenderer

SAMPLES = {
    "Japanese": "トーナメント決勝",
    "Chinese": "锦标赛决赛",
    "Korean": "토너먼트 결승",
    "Emoji": "🔥🏆⚡",
    "Latin extended": "ąęłőşñ",
}
# U+0378 is unassigned in Unicode, so no font has it and it's always drawn as a box
_NO_FONT_HAS_THIS = "͸"


def _page(text: str) -> str:
    return (
        '<link rel="stylesheet" href="/static/card.css">'
        f"<p style='margin: 40px; font-size: 96px'>{text}</p>"
    )


def sample_sheet() -> str:
    lines = "".join(
        f"<p style='font-size: 44px; margin: 18px 40px'>{name}: {text}</p>"
        for name, text in SAMPLES.items()
    )
    return f'<link rel="stylesheet" href="/static/card.css">{lines}'


async def check_fonts(renderer: CardRenderer) -> dict[str, bool]:
    blank = await renderer.render(_page(""))
    if await renderer.render(_page(_NO_FONT_HAS_THIS * 3)) == blank:
        raise RuntimeError("Missing characters render as nothing here, so boxes can't be detected")

    results = {}
    for name, text in SAMPLES.items():
        rendered = await renderer.render(_page(text))
        boxes = await renderer.render(_page(_NO_FONT_HAS_THIS * len(text)))
        results[name] = rendered != boxes
    return results
