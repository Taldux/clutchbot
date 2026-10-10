from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from clutchbot.acronyms import AcronymsFile
from clutchbot.alerts import Alerter, Priority
from clutchbot.listener.poll import Listener
from clutchbot.osu.client import OsuClient
from clutchbot.processing.pipeline import ProcessedMatch
from clutchbot.render.cards import MatchCards, RenderedCard
from clutchbot.storage.database import Database, MatchStatus
from clutchbot.twitter.client import XClient
from tests.conftest import HEAD_TO_HEAD_ID, load_fixture, metric, write_acronyms
from tests.fakes import TOKEN_URL, X_POSTS_URL, fake_match_endpoint, fake_x, token_response

NOW = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)
ACRONYMS = {"DBHS": "Dio's Bizzare Holiday Singles"}
LISTING_PATH = "/api/v2/matches"


def listing_entry(match_id: int, name: str) -> dict[str, Any]:
    start = "2025-03-02T17:10:39+00:00"
    return {"id": match_id, "name": name, "start_time": start, "end_time": None}


# newest first, like the real listing: one tournament lobby between two casual ones
LISTING = [
    listing_entry(HEAD_TO_HEAD_ID + 2, "peppy's game"),
    listing_entry(HEAD_TO_HEAD_ID, "DBHS: (NINERIK) vs (Welter)"),
    listing_entry(HEAD_TO_HEAD_ID - 5, "XYZ: (A) vs (B)"),  # not in the acronym list
]


def serve_listing(respx_mock: respx.MockRouter, matches: list[dict[str, Any]]) -> respx.Route:
    return respx_mock.get(path=LISTING_PATH).mock(
        return_value=httpx.Response(200, json={"matches": matches, "cursor_string": None})
    )


def serve_match(respx_mock: respx.MockRouter, match_json: dict[str, Any]) -> respx.Route:
    return respx_mock.get(path=f"{LISTING_PATH}/{match_json['match']['id']}").mock(
        side_effect=fake_match_endpoint(match_json)
    )


def unfinished(match_json: dict[str, Any]) -> dict[str, Any]:
    return {**match_json, "match": {**match_json["match"], "end_time": None}}


async def fake_render(processed: ProcessedMatch) -> MatchCards:
    clutches = [RenderedCard(f"clutch-{c.map_number}.png", b"png") for c in processed.clutches]
    return MatchCards(RenderedCard("result.png", b"png"), clutches)


@pytest.fixture
def db() -> Iterator[Database]:
    with Database(":memory:") as database:
        yield database


@pytest.fixture
async def osu(respx_mock: respx.MockRouter) -> AsyncIterator[OsuClient]:
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    async with OsuClient(1, SecretStr("secret")) as client:
        yield client


@pytest.fixture
async def x() -> AsyncIterator[XClient]:
    async with XClient(*(SecretStr(value) for value in ("ck", "cs", "at", "as"))) as client:
        yield client


class RecordingAlerter(Alerter):
    """Keeps every alert instead of sending it"""

    def __init__(self) -> None:
        super().__init__(None)
        self.sent: list[tuple[str, str, Priority]] = []

    async def send(
        self,
        title: str,
        message: str,
        *,
        priority: Priority = Priority.DEFAULT,
        tags: Sequence[str] = (),
    ) -> None:
        self.sent.append((title, message, priority))


def make_listener(
    osu: OsuClient,
    db: Database,
    x: XClient | None,
    tmp_path: Path,
    alerter: Alerter | None = None,
) -> Listener:
    acronyms_path = tmp_path / "acronyms.json"
    if not acronyms_path.exists():
        write_acronyms(acronyms_path, ACRONYMS)
    return Listener(
        osu=osu,
        db=db,
        acronyms=AcronymsFile(acronyms_path),
        render_cards=fake_render,
        x=x,
        dry_run_dir=tmp_path / "dry-run",
        match_expiry=timedelta(hours=24),
        warmup_games_checked=2,
        clutch_threshold_percent=5.0,
        ez_multiplier=1.5,
        alerter=alerter or RecordingAlerter(),
    )


EVENTS = ("tracked", "finished", "posted", "dry_run", "skipped", "failed", "expired")


def match_events() -> dict[str, float]:
    return {event: metric("clutchbot_matches_total", event=event) for event in EVENTS}


def changes(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    return {
        event: after[event] - before[event] for event in EVENTS if after[event] != before[event]
    }


def without_games(match_json: dict[str, Any]) -> dict[str, Any]:
    return {**match_json, "events": [e for e in match_json["events"] if not e.get("game")]}


@pytest.mark.respx(assert_all_called=False)
async def test_a_finished_tournament_match_goes_from_listing_to_posted(
    respx_mock: respx.MockRouter, osu: OsuClient, db: Database, x: XClient, tmp_path: Path
) -> None:
    serve_listing(respx_mock, LISTING)
    serve_match(respx_mock, load_fixture(HEAD_TO_HEAD_ID))
    _, posts = fake_x(respx_mock)
    alerter = RecordingAlerter()
    listener = make_listener(osu, db, x, tmp_path, alerter)
    before = match_events()
    renders = metric("clutchbot_render_duration_seconds_count")

    report = await listener.poll_once(NOW)

    assert (report.new, report.finished, report.posted) == ([HEAD_TO_HEAD_ID],) * 3
    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    assert (stored.status, stored.post_ids) == (MatchStatus.POSTED, ["p1", "p2"])
    assert posts.call_count == 2
    assert alerter.sent == []
    # casual lobbies and unknown acronyms are ignored, but the listing is remembered
    assert db.get(HEAD_TO_HEAD_ID + 2) is None
    assert db.get(HEAD_TO_HEAD_ID - 5) is None
    assert db.newest_seen_id() == HEAD_TO_HEAD_ID + 2
    assert changes(before, match_events()) == {"tracked": 1, "finished": 1, "posted": 1}
    assert metric("clutchbot_render_duration_seconds_count") == renders + 1
    assert metric("clutchbot_matches_waiting") == 0

    # the next poll posts nothing again
    report = await listener.poll_once(NOW + timedelta(minutes=10))

    assert report.new == report.finished == report.posted == []
    assert posts.call_count == 2


@pytest.mark.respx(assert_all_called=False)
async def test_an_unfinished_match_keeps_waiting(
    respx_mock: respx.MockRouter, osu: OsuClient, db: Database, x: XClient, tmp_path: Path
) -> None:
    serve_listing(respx_mock, LISTING)
    serve_match(respx_mock, unfinished(load_fixture(HEAD_TO_HEAD_ID)))
    _, posts = fake_x(respx_mock)

    report = await make_listener(osu, db, x, tmp_path).poll_once(NOW)

    assert (report.new, report.finished) == ([HEAD_TO_HEAD_ID], [])
    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    assert (stored.status, stored.last_checked) == (MatchStatus.WAITING, NOW)
    assert posts.call_count == 0
    assert metric("clutchbot_matches_waiting") == 1


@pytest.mark.respx(assert_all_called=False)
async def test_matches_that_never_finish_expire(
    respx_mock: respx.MockRouter, osu: OsuClient, db: Database, x: XClient, tmp_path: Path
) -> None:
    serve_listing(respx_mock, [])
    serve_match(respx_mock, unfinished(load_fixture(HEAD_TO_HEAD_ID)))
    db.add_match(HEAD_TO_HEAD_ID, "DBHS: (NINERIK) vs (Welter)", "DBHS", NOW - timedelta(hours=25))

    report = await make_listener(osu, db, x, tmp_path).poll_once(NOW)

    assert report.expired == 1
    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    assert stored.status is MatchStatus.EXPIRED


@pytest.mark.respx(assert_all_called=False)
async def test_dry_run_saves_the_cards_and_posts_nothing(
    respx_mock: respx.MockRouter, osu: OsuClient, db: Database, tmp_path: Path
) -> None:
    serve_listing(respx_mock, LISTING)
    serve_match(respx_mock, load_fixture(HEAD_TO_HEAD_ID))
    _, posts = fake_x(respx_mock)
    before = match_events()

    report = await make_listener(osu, db, None, tmp_path).poll_once(NOW)

    assert report.posted == [HEAD_TO_HEAD_ID]
    assert posts.call_count == 0
    folder = tmp_path / "dry-run" / str(HEAD_TO_HEAD_ID)
    assert sorted(p.name for p in folder.iterdir())[0] == "clutch-10.png"
    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    assert (stored.status, stored.post_ids) == (MatchStatus.POSTED, [])
    assert changes(before, match_events()) == {"tracked": 1, "finished": 1, "dry_run": 1}


@pytest.mark.respx(assert_all_called=False)
async def test_a_match_without_games_is_skipped_at_once_with_a_low_alert(
    respx_mock: respx.MockRouter, osu: OsuClient, db: Database, x: XClient, tmp_path: Path
) -> None:
    serve_listing(respx_mock, LISTING)
    serve_match(respx_mock, without_games(load_fixture(HEAD_TO_HEAD_ID)))
    _, posts = fake_x(respx_mock)
    alerter = RecordingAlerter()
    before = match_events()

    report = await make_listener(osu, db, x, tmp_path, alerter).poll_once(NOW)

    assert (report.skipped, report.failed) == ([HEAD_TO_HEAD_ID], [])
    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    assert (stored.status, stored.attempts) == (MatchStatus.FAILED, 1)
    assert stored.error is not None
    assert "No games" in stored.error
    assert posts.call_count == 0
    ((title, message, priority),) = alerter.sent
    assert (title, priority) == ("Match can't be posted", Priority.LOW)
    assert "DBHS: (NINERIK) vs (Welter)" in message
    assert "https://osu.ppy.sh/mp/117358530" in message
    assert "No games" in message
    # "failed" feeds an alert, so a lobby closed without games mustn't count as one
    assert changes(before, match_events()) == {"tracked": 1, "finished": 1, "skipped": 1}


@pytest.mark.respx(assert_all_called=False)
async def test_a_posting_failure_is_retried_next_poll(
    respx_mock: respx.MockRouter, osu: OsuClient, db: Database, x: XClient, tmp_path: Path
) -> None:
    serve_listing(respx_mock, LISTING)
    serve_match(respx_mock, load_fixture(HEAD_TO_HEAD_ID))
    respx_mock.post(X_POSTS_URL).mock(return_value=httpx.Response(403, json={"detail": "Nope"}))
    respx_mock.post("https://api.x.com/2/media/upload").mock(
        return_value=httpx.Response(200, json={"data": {"id": "m"}})
    )
    listener = make_listener(osu, db, x, tmp_path)

    report = await listener.poll_once(NOW)

    assert report.failed == [HEAD_TO_HEAD_ID]
    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    assert (stored.status, stored.attempts) == (MatchStatus.FINISHED, 1)

    respx_mock.clear()
    serve_listing(respx_mock, [])
    serve_match(respx_mock, load_fixture(HEAD_TO_HEAD_ID))
    fake_x(respx_mock)

    report = await listener.poll_once(NOW + timedelta(minutes=10))

    assert report.posted == [HEAD_TO_HEAD_ID]


@pytest.mark.respx(assert_all_called=False)
async def test_one_broken_match_does_not_stop_the_others(
    respx_mock: respx.MockRouter, osu: OsuClient, db: Database, x: XClient, tmp_path: Path
) -> None:
    serve_listing(respx_mock, [])  # the listing works but has nothing new
    db.add_match(111, "DBHS: (A) vs (B)", "DBHS", NOW)  # osu! answers 404 for this one
    respx_mock.get(path=f"{LISTING_PATH}/111").mock(return_value=httpx.Response(404))
    db.add_match(HEAD_TO_HEAD_ID, "DBHS: (NINERIK) vs (Welter)", "DBHS", NOW)
    serve_match(respx_mock, load_fixture(HEAD_TO_HEAD_ID))
    fake_x(respx_mock)

    report = await make_listener(osu, db, x, tmp_path).poll_once(NOW)

    assert report.failed == []
    assert report.posted == [HEAD_TO_HEAD_ID]
    broken = db.get(111)
    assert broken is not None
    # a failed check isn't an attempt, the match just keeps waiting
    assert (broken.status, broken.attempts) == (MatchStatus.WAITING, 0)


@pytest.mark.respx(assert_all_called=False)
async def test_a_broken_listing_does_not_stop_known_matches(
    respx_mock: respx.MockRouter, osu: OsuClient, db: Database, x: XClient, tmp_path: Path
) -> None:
    respx_mock.get(path=LISTING_PATH).mock(return_value=httpx.Response(404))
    db.add_match(HEAD_TO_HEAD_ID, "DBHS: (NINERIK) vs (Welter)", "DBHS", NOW)
    serve_match(respx_mock, load_fixture(HEAD_TO_HEAD_ID))
    fake_x(respx_mock)

    report = await make_listener(osu, db, x, tmp_path).poll_once(NOW)

    assert report.posted == [HEAD_TO_HEAD_ID]
    assert db.newest_seen_id() is None


@pytest.mark.respx(assert_all_called=False)
async def test_giving_up_on_a_match_sends_a_high_alert_once(
    respx_mock: respx.MockRouter, osu: OsuClient, db: Database, x: XClient, tmp_path: Path
) -> None:
    serve_listing(respx_mock, [])
    # finished, but osu! answers 404 every time it's fetched
    db.add_match(111, "DBHS: (A) vs (B)", "DBHS", NOW, MatchStatus.FINISHED)
    respx_mock.get(path=f"{LISTING_PATH}/111").mock(return_value=httpx.Response(404))
    alerter = RecordingAlerter()
    listener = make_listener(osu, db, x, tmp_path, alerter)
    before = match_events()

    for poll in range(3):
        await listener.poll_once(NOW + timedelta(minutes=10 * poll))
        # no alert while attempts are left
        assert (len(alerter.sent) == 1) is (poll == 2)

    ((title, message, priority),) = alerter.sent
    assert (title, priority) == ("Match failed", Priority.HIGH)
    assert "DBHS: (A) vs (B)" in message
    assert "after 3 attempt(s)" in message
    assert changes(before, match_events()) == {"failed": 1}


@pytest.mark.respx(assert_all_called=False)
async def test_an_acronym_added_while_running_is_tracked_from_the_next_poll(
    respx_mock: respx.MockRouter, osu: OsuClient, db: Database, tmp_path: Path
) -> None:
    serve_listing(respx_mock, [])
    listener = make_listener(osu, db, None, tmp_path)
    await listener.poll_once(NOW)
    assert metric("clutchbot_acronyms") == 1

    new_id = HEAD_TO_HEAD_ID + 10
    write_acronyms(tmp_path / "acronyms.json", {**ACRONYMS, "XYZ": "XYZ Cup"})
    serve_listing(respx_mock, [listing_entry(new_id, "XYZ: (A) vs (B)")])
    match_json = unfinished(load_fixture(HEAD_TO_HEAD_ID))
    serve_match(respx_mock, {**match_json, "match": {**match_json["match"], "id": new_id}})
    report = await listener.poll_once(NOW + timedelta(minutes=10))

    assert report.new == [new_id]
    assert report.failed == []
    assert metric("clutchbot_acronyms") == 2


@pytest.mark.respx(assert_all_called=False)
async def test_a_broken_acronyms_file_keeps_the_old_list_and_alerts_once(
    respx_mock: respx.MockRouter, osu: OsuClient, db: Database, tmp_path: Path
) -> None:
    serve_listing(respx_mock, [])
    alerter = RecordingAlerter()
    listener = make_listener(osu, db, None, tmp_path, alerter)
    write_acronyms(tmp_path / "acronyms.json", '{"DBHS": "missing a quote}')

    for poll in range(2):
        await listener.poll_once(NOW + timedelta(minutes=10 * poll))

    ((title, message, priority),) = alerter.sent
    assert (title, priority) == ("Acronyms file not reloaded", Priority.HIGH)
    assert "not valid JSON" in message
    # the lobby is still recognised with the old list
    serve_listing(respx_mock, LISTING)
    serve_match(respx_mock, unfinished(load_fixture(HEAD_TO_HEAD_ID)))
    report = await listener.poll_once(NOW + timedelta(minutes=20))
    assert report.new == [HEAD_TO_HEAD_ID]
