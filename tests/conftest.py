"""Shared test helpers"""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from prometheus_client import REGISTRY

from clutchbot.osu.models import Game, MatchDetail

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MATCH_IDS = [117358530, 117416879, 117500384]
HEAD_TO_HEAD_ID = 117358530
TEAM_VS_ID = 117416879
WARMUP_MATCH_ID = 117500384  # BLB: games 1 and 2 are warmups


def load_fixture(match_id: int) -> dict[str, Any]:
    path = FIXTURES_DIR / f"match_{match_id}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def metric(name: str, **labels: str) -> float:
    """A metric's current value (0 if it hasn't been recorded yet); counters only grow
    across tests, so tests compare the value before and after"""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def load_detail(match_id: int) -> MatchDetail:
    return MatchDetail.model_validate(load_fixture(match_id))


@pytest.fixture(params=MATCH_IDS, ids=str)
def match_json(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Raw JSON of each saved match in turn"""
    return load_fixture(request.param)


def write_acronyms(path: Path, acronyms: dict[str, str] | str) -> None:
    """Write the file and move its modification time on, so every write counts as an edit"""
    before = path.stat().st_mtime_ns if path.exists() else 0
    content = acronyms if isinstance(acronyms, str) else json.dumps(acronyms)
    path.write_text(content, encoding="utf-8")
    later = max(path.stat().st_mtime_ns, before + 1_000_000_000)
    os.utime(path, ns=(later, later))


def replace_games(detail: MatchDetail, changes: dict[int, Game]) -> MatchDetail:
    """A copy of the match with the games at the given positions replaced"""
    events = []
    game_index = 0
    for event in detail.events:
        if event.game is not None:
            if game_index in changes:
                event = event.model_copy(update={"game": changes[game_index]})
            game_index += 1
        events.append(event)
    return detail.model_copy(update={"events": events})


def without_nf(game: Game, score_index: int = 0) -> Game:
    scores = list(game.scores)
    score = scores[score_index]
    mods = [mod for mod in score.mods if mod.acronym != "NF"]
    scores[score_index] = score.model_copy(update={"mods": mods})
    return game.model_copy(update={"scores": scores})
