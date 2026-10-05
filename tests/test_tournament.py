import json
from pathlib import Path

import pytest

from clutchbot.acronyms import AcronymsError, load_acronyms
from clutchbot.processing.tournament import (
    TournamentName,
    describe_lobby,
    parse_tournament_name,
)

ACRONYMS = {
    "DBHS": "DBHS Tournament",
    "CRTOA": "CRTOA Tournament",
    "BLB": "BLB Tournament",
    "O!FT10": "osu! finnish tournament 10",
}
HESSEN_VS_BERLIN = TournamentName("BLB", "BLB Tournament", "Hessen", "Berlin")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # the three real fixture lobbies
        (
            "DBHS: (NINERIK) vs (Welter)",
            TournamentName("DBHS", "DBHS Tournament", "NINERIK", "Welter"),
        ),
        (
            "CRTOA: (Furball Pups) vs (Junior Sleuth Teds)",
            TournamentName("CRTOA", "CRTOA Tournament", "Furball Pups", "Junior Sleuth Teds"),
        ),
        ("BLB: (Hessen) vs (Berlin)", HESSEN_VS_BERLIN),
        # variations
        ("blb: (Hessen) VS (Berlin)", HESSEN_VS_BERLIN),
        ("BLB:(Hessen) vs. (Berlin)", HESSEN_VS_BERLIN),
        ("  BLB:  (Hessen)  vs  (Berlin)  ", HESSEN_VS_BERLIN),
        (
            "BLB: (Team (A)) vs (B)",
            TournamentName("BLB", "BLB Tournament", "Team (A)", "B"),
        ),
        # punctuation in the acronym
        (
            "o!ft10: (AllyrD) vs (savilju)",
            TournamentName("O!FT10", "osu! finnish tournament 10", "AllyrD", "savilju"),
        ),
        # known tournament, but no team names in the usual format
        ("BLB: Grand Finals", TournamentName("BLB", "BLB Tournament", "Red Team", "Blue Team")),
        ("BLB: (Hessen)", TournamentName("BLB", "BLB Tournament", "Red Team", "Blue Team")),
    ],
)
def test_tournament_lobbies(name: str, expected: TournamentName) -> None:
    assert parse_tournament_name(name, ACRONYMS) == expected


@pytest.mark.parametrize(
    "name",
    [
        "XYZ: (A) vs (B)",  # unknown acronym
        "peppy's game",  # no acronym
        "BLB (Hessen) vs (Berlin)",  # no colon
        "B L B: (Hessen) vs (Berlin)",  # spaces in the acronym
        "",
    ],
)
def test_other_lobbies(name: str) -> None:
    assert parse_tournament_name(name, ACRONYMS) is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("BLB: (Hessen) vs (Berlin)", HESSEN_VS_BERLIN),  # known: same as parse_tournament_name
        ("xyz: (A) vs (B)", TournamentName("XYZ", "XYZ", "A", "B")),
        ("XYZ: Finals", TournamentName("XYZ", "XYZ", "Red Team", "Blue Team")),
        (" peppy's game ", TournamentName("", "peppy's game", "Red Team", "Blue Team")),
    ],
)
def test_describe_lobby(name: str, expected: TournamentName) -> None:
    assert describe_lobby(name, ACRONYMS) == expected


def write_json(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "acronyms.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def test_load_acronyms_upper_cases_keys(tmp_path: Path) -> None:
    path = write_json(tmp_path, {"rt": "Random Tournament", "o!ft10": " osu! finnish tournament "})

    assert load_acronyms(path) == {"RT": "Random Tournament", "O!FT10": "osu! finnish tournament"}


def test_example_file_loads() -> None:
    example = Path(__file__).parent.parent / "acronyms.example.json"

    assert load_acronyms(example) == {"RT": "Random Tournament", "RT2": "Random Tournament 2"}


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AcronymsError, match="not found"):
        load_acronyms(tmp_path / "nope.json")


def test_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "acronyms.json"
    path.write_text('{"RT": "Random Tournament"\n"RT2": "Random Tournament 2"}', encoding="utf-8")

    with pytest.raises(AcronymsError, match="not valid JSON"):
        load_acronyms(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (["RT"], "must be a JSON object"),
        ({"RT": 5}, "must be a JSON object"),
        ({"R T": "Random"}, "can't appear before ':'"),
        ({"A:B": "Random"}, "can't appear before ':'"),
        ({"(RT)": "Random"}, "can't appear before ':'"),
        ({"RT": "Random", "rt": "Random again"}, "listed twice"),
        ({"RT": "  "}, "empty tournament name"),
    ],
)
def test_invalid_content(tmp_path: Path, content: object, message: str) -> None:
    with pytest.raises(AcronymsError, match=message):
        load_acronyms(write_json(tmp_path, content))
