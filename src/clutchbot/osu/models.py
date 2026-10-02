"""Typed models instead of API jsons"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TeamType = Literal["head-to-head", "tag-coop", "team-vs", "tag-team-vs"]
ScoringType = Literal["score", "accuracy", "combo", "scorev2"]
Team = Literal["red", "blue", "none"]


class OsuModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, validate_by_name=True)


class Mod(OsuModel):
    acronym: str


class ScoreMatchInfo(OsuModel):
    slot: int
    team: Team
    passed: bool = Field(alias="pass")


class Score(OsuModel):
    user_id: int
    total_score: int
    accuracy: float  # 0-1
    max_combo: int
    rank: str
    passed: bool
    mods: list[Mod]
    match: ScoreMatchInfo

    @property
    def mod_acronyms(self) -> list[str]:
        return [mod.acronym for mod in self.mods]


class Covers(OsuModel):
    cover: str
    cover_2x: str = Field(alias="cover@2x")
    slimcover: str


class Beatmapset(OsuModel):
    id: int
    title: str
    artist: str
    creator: str
    covers: Covers


class Beatmap(OsuModel):
    id: int
    version: str
    difficulty_rating: float
    total_length: int  # seconds
    beatmapset: Beatmapset


class Game(OsuModel):
    id: int
    beatmap_id: int
    start_time: datetime
    end_time: datetime | None
    mode: str
    scoring_type: ScoringType
    team_type: TeamType
    mods: list[str]  # acronyms
    beatmap: Beatmap | None = None  # missing for deleted beatmaps
    scores: list[Score]


class EventDetail(OsuModel):
    type: str  # "match-created", "player-joined", "other" (a game), etc
    text: str | None = None  # lobby name at the time, on "other" events


class Event(OsuModel):
    id: int
    detail: EventDetail
    timestamp: datetime
    user_id: int | None
    game: Game | None = None


class User(OsuModel):
    id: int
    username: str
    avatar_url: str
    country_code: str


class Match(OsuModel):
    id: int
    name: str
    start_time: datetime
    end_time: datetime | None

    @property
    def is_finished(self) -> bool:
        return self.end_time is not None


class MatchDetail(OsuModel):
    match: Match
    events: list[Event]
    users: list[User]
    first_event_id: int
    latest_event_id: int
    current_game_id: int | None

    @property
    def games(self) -> list[Game]:
        return [event.game for event in self.events if event.game is not None]

    @property
    def users_by_id(self) -> dict[int, User]:
        return {user.id: user for user in self.users}


class MatchListing(OsuModel):
    matches: list[Match]
    cursor_string: str | None
