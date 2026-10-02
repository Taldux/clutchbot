from pathlib import Path

import pytest

from clutchbot.acronyms import (
    AcronymsChange,
    AcronymsError,
    AcronymsFile,
    load_acronyms,
    save_acronyms,
)
from tests.conftest import write_acronyms


@pytest.fixture
def path(tmp_path: Path) -> Path:
    path = tmp_path / "acronyms.json"
    write_acronyms(path, {"RT": "Random Tournament", "OLD": "Old Cup"})
    return path


def test_loads_at_start(path: Path) -> None:
    assert AcronymsFile(path).current == {"RT": "Random Tournament", "OLD": "Old Cup"}


def test_an_empty_file_is_refused_at_start(tmp_path: Path) -> None:
    path = tmp_path / "acronyms.json"
    write_acronyms(path, {})

    with pytest.raises(AcronymsError, match="has no tournaments"):
        AcronymsFile(path)


def test_an_edit_is_picked_up(path: Path) -> None:
    acronyms = AcronymsFile(path)
    assert acronyms.reload_if_changed() is None  # nothing happens while the file is untouched
    write_acronyms(path, {"rt": "Random Tournament 2026", "NEW": "New Cup"})

    change = acronyms.reload_if_changed()

    assert change == AcronymsChange(added=["NEW"], removed=["OLD"], renamed=["RT"])
    assert change.summary() == "+NEW, -OLD, ~RT"
    assert acronyms.current == {"RT": "Random Tournament 2026", "NEW": "New Cup"}
    assert acronyms.reload_if_changed() is None


def test_saving_without_changes_is_not_a_change(path: Path) -> None:
    acronyms = AcronymsFile(path)
    write_acronyms(path, {"OLD": "Old Cup", "RT": "Random Tournament"})

    assert acronyms.reload_if_changed() is None


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"RT": "Random Tournament"', "not valid JSON"),
        ("{}", "has no tournaments"),
        ('{"R T": "Random"}', "can't appear before ':'"),
        (None, "not found"),  # the file was deleted
    ],
)
def test_a_broken_version_is_refused_once_and_the_old_list_kept(
    path: Path, content: str | None, message: str
) -> None:
    acronyms = AcronymsFile(path)
    if content is None:
        path.unlink()
    else:
        write_acronyms(path, content)

    with pytest.raises(AcronymsError, match=message):
        acronyms.reload_if_changed()
    assert acronyms.reload_if_changed() is None  # the same broken version isn't reported again
    assert acronyms.current == {"RT": "Random Tournament", "OLD": "Old Cup"}


def test_fixing_the_file_again_is_reported(path: Path) -> None:
    acronyms = AcronymsFile(path)
    write_acronyms(path, "{")
    with pytest.raises(AcronymsError):
        acronyms.reload_if_changed()

    # back to exactly the old content: still worth a log line that the file is fine again
    write_acronyms(path, {"RT": "Random Tournament", "OLD": "Old Cup"})
    change = acronyms.reload_if_changed()

    assert change is not None
    assert change.summary() == "no changes"


def test_save_acronyms_writes_a_file_that_loads_again(tmp_path: Path) -> None:
    path = tmp_path / "new-folder" / "acronyms.json"

    save_acronyms(path, {"RT": "Random Tournament", "JP": "東京カップ"})

    assert list(load_acronyms(path)) == ["JP", "RT"]
    assert "東京カップ" in path.read_text(encoding="utf-8")
    assert [file.name for file in path.parent.iterdir()] == ["acronyms.json"]
