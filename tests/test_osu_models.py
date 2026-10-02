from typing import Any

import pytest
from pydantic import ValidationError

from clutchbot.osu.models import MatchDetail, MatchListing, Score
from tests.conftest import HEAD_TO_HEAD_ID, TEAM_VS_ID, load_fixture


def test_every_fixture_parses(match_json: dict[str, Any]) -> None:
    detail = MatchDetail.model_validate(match_json)

    assert detail.match.is_finished
    assert detail.events[0].id == detail.first_event_id
    assert detail.events[-1].id == detail.latest_event_id
    assert len(detail.games) == sum(1 for e in match_json["events"] if e.get("game"))


def test_head_to_head_values() -> None:
    detail = MatchDetail.model_validate(load_fixture(HEAD_TO_HEAD_ID))
    game = detail.games[0]
    score = game.scores[0]

    assert detail.match.name == "DBHS: (NINERIK) vs (Welter)"
    assert game.team_type == "head-to-head"
    assert game.scoring_type == "scorev2"
    assert score.user_id == 10549880
    assert score.total_score == 368958
    assert score.mod_acronyms == ["NF", "DT"]
    assert score.match.team == "none"
    assert score.match.passed is True


def test_team_vs_values() -> None:
    detail = MatchDetail.model_validate(load_fixture(TEAM_VS_ID))
    game = detail.games[0]

    assert game.team_type == "team-vs"
    assert {score.match.team for score in game.scores} == {"red", "blue"}
    assert game.beatmap is not None
    assert game.beatmap.beatmapset.title == "Toosenbo feat. Hatsune Miku"


def test_every_scoring_user_is_in_users(match_json: dict[str, Any]) -> None:
    detail = MatchDetail.model_validate(match_json)
    scoring_users = {score.user_id for game in detail.games for score in game.scores}

    assert scoring_users <= detail.users_by_id.keys()


def test_listing_parses() -> None:
    listing = MatchListing.model_validate(
        {
            "matches": [
                {
                    "id": 114428685,
                    "start_time": "2024-06-25T00:55:30+00:00",
                    "end_time": None,
                    "name": "peppy's game",
                }
            ],
            "params": {"limit": 50, "sort": "id_desc", "active": None},
            "cursor": {"match_id": 114428685},
            "cursor_string": "eyJtYXRjaF9pZCI6MTE0NDI4Njg1fQ",
        }
    )

    assert listing.matches[0].is_finished is False
    assert listing.cursor_string == "eyJtYXRjaF9pZCI6MTE0NDI4Njg1fQ"


def test_legacy_score_format_is_rejected() -> None:
    legacy = {
        "user_id": 1,
        "score": 368958,
        "accuracy": 0.95,
        "max_combo": 258,
        "rank": "A",
        "passed": True,
        "mods": ["NF", "DT"],
        "match": {"slot": 0, "team": "none", "pass": True},
    }
    with pytest.raises(ValidationError):
        Score.model_validate(legacy)
