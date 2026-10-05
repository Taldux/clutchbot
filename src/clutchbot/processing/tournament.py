import re
from collections.abc import Mapping
from dataclasses import dataclass

# anything but spaces, colon and brackets
ACRONYM = r"[^\s:()]+"
ACRONYM_PATTERN = re.compile(rf"^(?P<acronym>{ACRONYM}):\s*(?P<rest>.*)$")
TEAMS_PATTERN = re.compile(r"^\((?P<red>.+)\)\s+vs\.?\s+\((?P<blue>.+)\)$", re.IGNORECASE)

DEFAULT_RED_TEAM = "Red Team"
DEFAULT_BLUE_TEAM = "Blue Team"


@dataclass(frozen=True)
class TournamentName:
    acronym: str
    tournament: str
    # in 1v1 these are the player names
    red_team: str
    blue_team: str


def is_valid_acronym(text: str) -> bool:
    return re.fullmatch(ACRONYM, text) is not None


def parse_tournament_name(match_name: str, acronyms: Mapping[str, str]) -> TournamentName | None:
    acronym_match = ACRONYM_PATTERN.match(match_name.strip())
    if acronym_match is None:
        return None
    acronym = acronym_match["acronym"].upper()
    tournament = acronyms.get(acronym)
    if tournament is None:
        return None
    return TournamentName(acronym, tournament, *_team_names(acronym_match["rest"]))


def describe_lobby(match_name: str, acronyms: Mapping[str, str]) -> TournamentName:
    # for processing by hand
    known = parse_tournament_name(match_name, acronyms)
    if known is not None:
        return known
    acronym_match = ACRONYM_PATTERN.match(match_name.strip())
    if acronym_match is None:
        return TournamentName("", match_name.strip(), DEFAULT_RED_TEAM, DEFAULT_BLUE_TEAM)
    acronym = acronym_match["acronym"].upper()
    return TournamentName(acronym, acronym, *_team_names(acronym_match["rest"]))


def _team_names(rest: str) -> tuple[str, str]:
    teams_match = TEAMS_PATTERN.match(rest.strip())
    if teams_match is None:
        return DEFAULT_RED_TEAM, DEFAULT_BLUE_TEAM
    return teams_match["red"].strip(), teams_match["blue"].strip()
