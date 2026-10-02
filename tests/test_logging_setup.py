import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from clutchbot.config import Settings
from clutchbot.log_context import current_match_id, match_context
from clutchbot.logging_setup import setup_logging

log = logging.getLogger("clutchbot.test")


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    # setup_logging replaces the root handlers; later tests shouldn't log into a closed capture
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def configure(log_format: str) -> None:
    settings = Settings(
        osu_client_id=1,
        osu_client_secret=SecretStr("osu-secret-value"),
        log_format=log_format,  # type: ignore[arg-type]
        _env_file=None,  # type: ignore[call-arg]
    )
    setup_logging(settings)


def json_lines(err: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in err.splitlines() if line.strip()]


def test_json_lines(capsys: pytest.CaptureFixture[str]) -> None:
    configure("json")

    log.info("Poll done: %d new", 2)

    (entry,) = json_lines(capsys.readouterr().err)
    assert entry["level"] == "INFO"
    assert entry["logger"] == "clutchbot.test"
    assert entry["message"] == "Poll done: 2 new"
    assert entry["time"].endswith("+00:00")
    assert "match_id" not in entry


def test_match_id_is_added_inside_a_match_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure("json")

    with match_context(117358530):
        log.info("Posting")
        logging.getLogger("clutchbot.twitter.client").info("Published post p1")
    log.info("Poll done")

    first, second, outside = json_lines(capsys.readouterr().err)
    assert first["match_id"] == second["match_id"] == 117358530
    assert "match_id" not in outside
    assert current_match_id() is None


def test_secrets_and_tracebacks_in_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure("json")

    try:
        raise RuntimeError("request with osu-secret-value failed")
    except RuntimeError:
        log.exception("Oops, the secret is osu-secret-value")

    (entry,) = json_lines(capsys.readouterr().err)
    assert entry["message"] == "Oops, the secret is ***"
    assert "RuntimeError: request with *** failed" in entry["exception"]
    assert "osu-secret-value" not in json.dumps(entry)


def test_text_is_still_the_default(capsys: pytest.CaptureFixture[str]) -> None:
    configure("text")

    with match_context(1):
        log.warning("Plain line with osu-secret-value")

    err = capsys.readouterr().err
    assert "WARNING  clutchbot.test: Plain line with ***" in err
    assert not err.startswith("{")
