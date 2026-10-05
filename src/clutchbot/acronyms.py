import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from clutchbot.processing.tournament import is_valid_acronym


class AcronymsError(Exception):
    pass


_ACRONYMS = TypeAdapter(dict[str, str])


def load_acronyms(path: Path) -> dict[str, str]:
    # keys are upper case
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AcronymsError(f"Acronyms file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise AcronymsError(f"{path} is not valid JSON: {exc}") from None

    try:
        entries = _ACRONYMS.validate_python(raw, strict=True)
    except ValidationError:
        raise AcronymsError(
            f'{path} must be a JSON object of "ACRONYM": "Tournament name" strings'
        ) from None

    acronyms: dict[str, str] = {}
    for key, tournament in entries.items():
        try:
            acronym, name = check_entry(key, tournament)
        except AcronymsError as exc:
            raise AcronymsError(f"{path}: {exc}") from None
        if acronym in acronyms:
            raise AcronymsError(f"{path}: {key!r} is listed twice (acronyms ignore case)")
        acronyms[acronym] = name
    return acronyms


def check_entry(key: str, tournament: str) -> tuple[str, str]:
    acronym = key.strip().upper()
    if not is_valid_acronym(acronym):
        raise AcronymsError(f"{key!r} can't appear before ':' in a lobby name")
    if not tournament.strip():
        raise AcronymsError(f"{key!r} has an empty tournament name")
    return acronym, tournament.strip()


def save_acronyms(path: Path, acronyms: Mapping[str, str]) -> None:
    text = json.dumps(dict(sorted(acronyms.items())), indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.part")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class AcronymsChange:
    added: list[str]
    removed: list[str]
    renamed: list[str]

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.renamed)

    def summary(self) -> str:
        parts = [
            *(f"+{acronym}" for acronym in self.added),
            *(f"-{acronym}" for acronym in self.removed),
            *(f"~{acronym}" for acronym in self.renamed),
        ]
        return ", ".join(parts) or "no changes"


def _compare(old: Mapping[str, str], new: Mapping[str, str]) -> AcronymsChange:
    return AcronymsChange(
        added=sorted(new.keys() - old.keys()),
        removed=sorted(old.keys() - new.keys()),
        renamed=sorted(key for key in old.keys() & new.keys() if old[key] != new[key]),
    )


def _load_non_empty(path: Path) -> dict[str, str]:
    acronyms = load_acronyms(path)
    if not acronyms:
        raise AcronymsError(f"{path} has no tournaments in it")
    return acronyms


def _file_version(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


class AcronymsFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._version = _file_version(path)
        self._acronyms = _load_non_empty(path)
        self._refused = False

    @property
    def current(self) -> Mapping[str, str]:
        return self._acronyms

    def reload_if_changed(self) -> AcronymsChange | None:
        version = _file_version(self.path)
        if version == self._version:
            return None
        self._version = version
        try:
            acronyms = _load_non_empty(self.path)
        except AcronymsError:
            self._refused = True
            raise
        change = _compare(self._acronyms, acronyms)
        if change.is_empty and not self._refused:
            return None  # saved without changes
        self._acronyms = acronyms
        self._refused = False
        return change
