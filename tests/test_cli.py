import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from clutchbot import cli as cli_module
from clutchbot.cli import app
from clutchbot.listener.loop import write_heartbeat
from clutchbot.storage.database import Database
from tests.conftest import HEAD_TO_HEAD_ID, TEAM_VS_ID, WARMUP_MATCH_ID, load_fixture
from tests.fakes import TOKEN_URL, fake_match_endpoint, fake_x, token_response

runner = CliRunner()

CONFIG_VARS = [
    "OSU_CLIENT_ID",
    "OSU_CLIENT_SECRET",
    "TWITTER_CONSUMER_KEY",
    "TWITTER_CONSUMER_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_SECRET",
    "DRY_RUN",
    "POLL_INTERVAL_SECONDS",
    "MATCH_EXPIRY_HOURS",
    "CLUTCH_THRESHOLD_PERCENT",
    "WARMUP_GAMES_CHECKED",
    "DATA_DIR",
    "ACRONYMS_PATH",
    "LOG_LEVEL",
    "NTFY_URL",
]


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An empty folder and only the settings the tests need"""
    monkeypatch.chdir(tmp_path)
    for var in CONFIG_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OSU_CLIENT_ID", "1")
    monkeypatch.setenv("OSU_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


def serve_match(respx_mock: respx.MockRouter, match_id: int) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    respx_mock.get(path=f"/api/v2/matches/{match_id}").mock(
        side_effect=fake_match_endpoint(load_fixture(match_id))
    )


def serve_missing_images(respx_mock: respx.MockRouter) -> None:
    """Covers and avatars answer 404, so cards use their placeholders"""
    for host in ("assets.ppy.sh", "a.ppy.sh"):
        respx_mock.get(host=host).mock(return_value=httpx.Response(404))


@pytest.mark.respx(assert_all_called=False)
def test_process_dry_run(respx_mock: respx.MockRouter, isolated_config: Path) -> None:
    serve_match(respx_mock, HEAD_TO_HEAD_ID)
    serve_missing_images(respx_mock)

    result = runner.invoke(app, ["process", "https://osu.ppy.sh/mp/117358530"])

    assert result.exit_code == 0, result.output
    assert "DBHS | DBHS: (NINERIK) vs (Welter)" in result.stdout  # no acronyms file
    assert "NINERIK 3 - 7 Welter  (Winner: Welter)" in result.stdout
    assert "Dry run, nothing posted" in result.stdout
    assert "Post 1 [result.png]" in result.stdout
    assert "  | Welter defeats NINERIK 7\N{EN DASH}3" in result.stdout
    assert "Post 2, reply to post 1 [clutch-5.png, clutch-6.png" in result.stdout
    folder = isolated_config / "output" / str(HEAD_TO_HEAD_ID)
    assert (folder / "clutch-10.png").read_bytes().startswith(b"\x89PNG")


@pytest.mark.respx(assert_all_called=False)
def test_process_uses_the_acronyms_file(
    respx_mock: respx.MockRouter, isolated_config: Path
) -> None:
    acronyms = {"BLB": "Bundeslaender Battle"}
    (isolated_config / "acronyms.json").write_text(json.dumps(acronyms))
    serve_match(respx_mock, WARMUP_MATCH_ID)
    serve_missing_images(respx_mock)

    result = runner.invoke(app, ["process", str(WARMUP_MATCH_ID)])

    assert result.exit_code == 0, result.output
    # the summary goes to stdout, logs (which also mention the warmups) go to stderr
    assert "Bundeslaender Battle | BLB: (Hessen) vs (Berlin)" in result.stdout
    assert result.stdout.count("(warmup)") == 2
    assert "  | \N{TROPHY} Bundeslaender Battle" in result.stdout


@pytest.mark.respx(assert_all_called=False)
def test_process_posts_after_confirming(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_x_keys(monkeypatch)
    serve_match(respx_mock, HEAD_TO_HEAD_ID)
    serve_missing_images(respx_mock)
    upload, posts = fake_x(respx_mock)

    result = runner.invoke(app, ["process", "117358530", "--no-dry-run"], input="y\n")

    assert result.exit_code == 0, result.output
    assert "Publish 2 post(s) on X?" in result.stdout
    assert "Post 1: https://x.com/i/status/p1" in result.stdout
    assert "Post 2: https://x.com/i/status/p2" in result.stdout
    assert upload.call_count == 5  # the result card and 4 clutch cards
    assert posts.call_count == 2


@pytest.mark.respx(assert_all_called=False)
def test_process_never_posts_a_match_twice(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_x_keys(monkeypatch)
    serve_match(respx_mock, HEAD_TO_HEAD_ID)
    serve_missing_images(respx_mock)
    _, posts = fake_x(respx_mock)
    first = runner.invoke(app, ["process", "117358530", "--no-dry-run", "--yes"])

    assert first.exit_code == 0, first.output
    assert "post(s) on X?" not in first.stdout  # --yes skips the question

    again = runner.invoke(app, ["process", "117358530", "--no-dry-run", "--yes"])

    assert again.exit_code == 1
    assert "Match 117358530 was already posted, use --force" in again.output
    assert "Post 1: https://x.com/i/status/p1" in again.stdout  # where to find it
    assert posts.call_count == 2  # nothing new

    forced = runner.invoke(app, ["process", "117358530", "--no-dry-run", "--yes", "--force"])

    assert forced.exit_code == 0, forced.output
    assert "Post 1: https://x.com/i/status/p3" in forced.stdout
    assert posts.call_count == 4


@pytest.mark.respx(assert_all_called=False)
def test_process_answering_no_posts_nothing(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
) -> None:
    set_x_keys(monkeypatch)
    serve_match(respx_mock, HEAD_TO_HEAD_ID)
    serve_missing_images(respx_mock)
    upload, posts = fake_x(respx_mock)

    result = runner.invoke(app, ["process", "117358530", "--no-dry-run"], input="n\n")

    assert result.exit_code == 1
    assert "Nothing posted" in result.output
    assert upload.call_count == posts.call_count == 0
    with Database(isolated_config / "clutchbot.db") as db:
        assert db.get(HEAD_TO_HEAD_ID) is None


def test_posting_without_x_keys_fails_before_fetching() -> None:
    # no osu! routes are faked, so this also shows nothing was fetched
    result = runner.invoke(app, ["process", "117358530", "--no-dry-run"])

    assert result.exit_code == 2
    assert "TWITTER_CONSUMER_KEY" in result.output


def test_process_bad_link() -> None:
    result = runner.invoke(app, ["process", "https://example.com/mp/1"])

    assert result.exit_code == 2
    assert "Not a match id or osu! match link" in result.output


def test_process_missing_match(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    respx_mock.get(path="/api/v2/matches/5").mock(return_value=httpx.Response(404))

    result = runner.invoke(app, ["process", "5"])

    assert result.exit_code == 1
    assert "Match 5 not found" in result.output


@pytest.mark.respx(assert_all_called=False)
def test_render_saves_the_cards(respx_mock: respx.MockRouter, isolated_config: Path) -> None:
    serve_match(respx_mock, TEAM_VS_ID)
    serve_missing_images(respx_mock)

    result = runner.invoke(app, ["render", str(TEAM_VS_ID), "--output", "cards"])

    assert result.exit_code == 0, result.output
    folder = isolated_config / "cards" / str(TEAM_VS_ID)
    assert sorted(p.name for p in folder.iterdir()) == ["clutch-7.png", "result.png"]
    assert (folder / "result.png").read_bytes().startswith(b"\x89PNG")
    assert f"Saved {Path('cards') / str(TEAM_VS_ID) / 'result.png'}" in result.stdout


def test_listen_starts_the_loop(monkeypatch: pytest.MonkeyPatch, isolated_config: Path) -> None:
    (isolated_config / "acronyms.json").write_text(json.dumps({"DBHS": "Dio's Bizzare"}))
    received: dict[str, Any] = {}

    async def fake_run_listener(listener: object, stop: object, **kwargs: Any) -> None:
        received.update(kwargs, listener=listener)

    monkeypatch.setattr(cli_module, "run_listener", fake_run_listener)
    monkeypatch.setattr(cli_module, "_stop_on_signals", lambda stop: None)

    result = runner.invoke(app, ["listen"])

    assert result.exit_code == 0, result.output
    assert received["interval_seconds"] == 600
    assert received["heartbeat_path"] == isolated_config / "heartbeat"
    assert received["image_cache_dir"] == isolated_config / "image-cache"
    # logs go to stderr, which the runner captures into the output
    assert "Listening for 1 tournament(s) every 600 s, dry run" in result.output


@pytest.mark.parametrize(
    ("minutes_ago", "exit_code", "message"),
    [
        (3, 0, "OK: last poll finished 180 s ago"),
        (30, 1, "Unhealthy: last poll finished 1800 s ago"),  # the limit is 25 minutes
    ],
)
def test_healthcheck(isolated_config: Path, minutes_ago: int, exit_code: int, message: str) -> None:
    last_poll = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    write_heartbeat(isolated_config / "heartbeat", last_poll)

    result = runner.invoke(app, ["healthcheck"])

    assert result.exit_code == exit_code
    assert message in result.output


def test_healthcheck_without_a_heartbeat() -> None:
    result = runner.invoke(app, ["healthcheck"])

    assert result.exit_code == 1
    assert "Unhealthy: no readable heartbeat" in result.output


def test_check_alerts_sends_a_test_notification(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NTFY_URL", "https://ntfy.sh/clutchbot-test-topic")
    route = respx_mock.post("https://ntfy.sh/").mock(return_value=httpx.Response(200))

    result = runner.invoke(app, ["check-alerts"])

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content)["topic"] == "clutchbot-test-topic"
    assert "Test notification sent" in result.stdout
    assert "clutchbot-test-topic" not in result.output  # the topic is a secret in the logs


@pytest.mark.parametrize(
    ("ntfy_url", "message"),
    [(None, "Set NTFY_URL in .env first"), ("ntfy.sh", "must look like https://ntfy.sh/<topic>")],
)
def test_check_alerts_needs_a_valid_url(
    monkeypatch: pytest.MonkeyPatch, ntfy_url: str | None, message: str
) -> None:
    if ntfy_url is not None:
        monkeypatch.setenv("NTFY_URL", ntfy_url)

    result = runner.invoke(app, ["check-alerts"])

    assert result.exit_code == 2
    assert message in result.output


def test_check_fonts(isolated_config: Path) -> None:
    result = runner.invoke(app, ["check-fonts", "--output", "sample/fonts.png"])

    assert result.exit_code == 0, result.output
    for script in ("Japanese", "Chinese", "Korean", "Emoji", "Latin extended"):
        assert f"{script:<15} OK" in result.stdout
    assert (isolated_config / "sample" / "fonts.png").read_bytes().startswith(b"\x89PNG")


def test_listen_needs_the_acronyms_file() -> None:
    result = runner.invoke(app, ["listen"])

    assert result.exit_code == 2
    assert "Acronyms file not found" in result.output


def set_x_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in CONFIG_VARS[2:6]:
        monkeypatch.setenv(var, "x")


def test_check_x(respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    set_x_keys(monkeypatch)
    respx_mock.get("https://api.x.com/2/users/me").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "42", "username": "ClutchBot", "name": "Clutch Bot"}}
        )
    )

    result = runner.invoke(app, ["check-x"])

    assert result.exit_code == 0, result.output
    assert "X keys OK: posts will come from @ClutchBot (Clutch Bot)" in result.stdout


def test_check_x_with_wrong_keys(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_x_keys(monkeypatch)
    respx_mock.get("https://api.x.com/2/users/me").mock(
        return_value=httpx.Response(401, json={"title": "Unauthorized"})
    )

    result = runner.invoke(app, ["check-x"])

    assert result.exit_code == 1
    assert "X rejected the keys: X API error 401: Unauthorized" in result.output


def test_check_x_without_keys() -> None:
    result = runner.invoke(app, ["check-x"])

    assert result.exit_code == 2
    assert "Set all four TWITTER_* keys" in result.output


def test_process_invalid_acronyms_file(isolated_config: Path) -> None:
    (isolated_config / "acronyms.json").write_text("{not json")

    result = runner.invoke(app, ["process", "5"])

    assert result.exit_code == 2
    assert "not valid JSON" in result.output


def read_acronyms_json(folder: Path) -> Any:
    return json.loads((folder / "acronyms.json").read_text(encoding="utf-8"))


def test_acronyms_add_creates_the_file(isolated_config: Path) -> None:
    result = runner.invoke(app, ["acronyms", "add", "rt", " Random Tournament "])

    assert result.exit_code == 0
    assert "Added RT: Random Tournament" in result.stdout
    assert "Lobbies opened before that aren't picked up" in result.stdout
    assert read_acronyms_json(isolated_config) == {"RT": "Random Tournament"}
    assert not (isolated_config / "acronyms.json.part").exists()


def test_acronyms_add_wont_rename_without_replace(isolated_config: Path) -> None:
    (isolated_config / "acronyms.json").write_text('{"RT": "Random Tournament"}')

    refused = runner.invoke(app, ["acronyms", "add", "rt", "Other Name"])
    unchanged = runner.invoke(app, ["acronyms", "add", "RT", "Random Tournament"])
    renamed = runner.invoke(app, ["acronyms", "add", "RT", "Other Name", "--replace"])

    assert refused.exit_code == 1
    assert "add --replace to change it" in refused.output
    assert unchanged.exit_code == 0
    assert "nothing changed" in unchanged.stdout
    assert renamed.exit_code == 0
    assert "Renamed RT: Other Name" in renamed.stdout
    assert "Lobbies opened before" not in renamed.stdout
    assert read_acronyms_json(isolated_config) == {"RT": "Other Name"}


def test_acronyms_add_never_overwrites_a_broken_file(isolated_config: Path) -> None:
    (isolated_config / "acronyms.json").write_text('{"RT": "Random"')

    result = runner.invoke(app, ["acronyms", "add", "NEW", "New Cup"])

    assert result.exit_code == 2
    assert "not valid JSON" in result.output
    assert (isolated_config / "acronyms.json").read_text() == '{"RT": "Random"'


def test_acronyms_remove(isolated_config: Path) -> None:
    (isolated_config / "acronyms.json").write_text('{"RT": "Random", "OLD": "Old Cup"}')

    result = runner.invoke(app, ["acronyms", "remove", "old"])

    assert result.exit_code == 0
    assert "Removed OLD (Old Cup)" in result.stdout
    assert read_acronyms_json(isolated_config) == {"RT": "Random"}


@pytest.mark.parametrize(
    ("acronym", "message"),
    [("XYZ", "XYZ isn't in"), ("RT", "the only tournament left")],
)
def test_acronyms_remove_refuses(isolated_config: Path, acronym: str, message: str) -> None:
    (isolated_config / "acronyms.json").write_text('{"RT": "Random"}')

    result = runner.invoke(app, ["acronyms", "remove", acronym])

    assert result.exit_code == 1
    assert message in result.output
    assert read_acronyms_json(isolated_config) == {"RT": "Random"}


def test_acronyms_list(isolated_config: Path) -> None:
    (isolated_config / "acronyms.json").write_text('{"RT": "Random", "LONGER": "Longer Cup"}')

    result = runner.invoke(app, ["acronyms", "list"])

    assert result.exit_code == 0
    assert result.stdout.splitlines()[:2] == ["LONGER  Longer Cup", "RT      Random"]
    assert "2 tournament(s) in" in result.stdout
