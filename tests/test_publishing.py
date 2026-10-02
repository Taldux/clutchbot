import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import httpx
import pytest
import respx
from pydantic import SecretStr

from clutchbot.listener.publishing import (
    MAX_POST_ATTEMPTS,
    AlreadyPostedError,
    posted_so_far,
    publish_match,
)
from clutchbot.processing.pipeline import ProcessedMatch, process_match
from clutchbot.processing.tournament import TournamentName
from clutchbot.render.cards import MatchCards, RenderedCard
from clutchbot.storage.database import Database, MatchStatus
from clutchbot.twitter.client import XClient
from clutchbot.twitter.poster import PublishError
from clutchbot.twitter.posts import PlannedPost, plan_posts
from tests.conftest import HEAD_TO_HEAD_ID, load_detail
from tests.fakes import X_POSTS_URL, X_UPLOAD_URL, fake_x

NOW = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)
DBHS = TournamentName("DBHS", "Dio's Bizzare Holiday Singles", "NINERIK", "Welter")


@pytest.fixture
def db() -> Iterator[Database]:
    with Database(":memory:") as database:
        yield database


@pytest.fixture
async def x() -> AsyncIterator[XClient]:
    async with XClient(*(SecretStr(value) for value in ("ck", "cs", "at", "as"))) as client:
        yield client


@pytest.fixture
def match() -> ProcessedMatch:
    return process_match(load_detail(HEAD_TO_HEAD_ID), DBHS)


def plan_for(match: ProcessedMatch) -> list[PlannedPost]:
    clutches = [RenderedCard(f"clutch-{c.map_number}.png", b"png") for c in match.clutches]
    return plan_posts(match, MatchCards(RenderedCard("result.png", b"png"), clutches))


def failing_second_post(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(X_UPLOAD_URL).mock(return_value=httpx.Response(200, json={"data": {"id": "m"}}))
    respx_mock.post(X_POSTS_URL).mock(
        side_effect=[
            httpx.Response(200, json={"data": {"id": "p1"}}),
            httpx.Response(403, json={"detail": "Not permitted"}),
        ]
    )


async def test_a_new_match_is_posted_and_remembered(
    respx_mock: respx.MockRouter, db: Database, x: XClient, match: ProcessedMatch
) -> None:
    fake_x(respx_mock)

    post_ids = await publish_match(db, x, match, plan_for(match), now=NOW)

    assert post_ids == ["p1", "p2"]
    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    assert stored.status is MatchStatus.POSTED
    assert (stored.post_ids, stored.posted_at) == (["p1", "p2"], NOW)
    assert (stored.acronym, stored.name) == ("DBHS", "DBHS: (NINERIK) vs (Welter)")


async def test_a_posted_match_is_never_posted_again(
    respx_mock: respx.MockRouter, db: Database, x: XClient, match: ProcessedMatch
) -> None:
    _, posts = fake_x(respx_mock)
    await publish_match(db, x, match, plan_for(match), now=NOW)

    with pytest.raises(AlreadyPostedError):
        await publish_match(db, x, match, plan_for(match), now=NOW)

    assert posts.call_count == 2  # only the first time
    with pytest.raises(AlreadyPostedError):
        posted_so_far(db, HEAD_TO_HEAD_ID)


async def test_force_posts_a_new_thread(
    respx_mock: respx.MockRouter, db: Database, x: XClient, match: ProcessedMatch
) -> None:
    fake_x(respx_mock)
    await publish_match(db, x, match, plan_for(match), now=NOW)

    post_ids = await publish_match(db, x, match, plan_for(match), force=True, now=NOW)

    assert post_ids == ["p3", "p4"]
    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    assert stored.post_ids == ["p3", "p4"]


async def test_a_failure_is_recorded_and_the_next_attempt_resumes(
    respx_mock: respx.MockRouter, db: Database, x: XClient, match: ProcessedMatch
) -> None:
    failing_second_post(respx_mock)

    with pytest.raises(PublishError):
        await publish_match(db, x, match, plan_for(match), now=NOW)

    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    assert (stored.status, stored.attempts, stored.post_ids) == (MatchStatus.FINISHED, 1, ["p1"])
    assert stored.error is not None
    assert "Not permitted" in stored.error

    respx_mock.clear()  # X works again
    upload = respx_mock.post(X_UPLOAD_URL).mock(
        return_value=httpx.Response(200, json={"data": {"id": "m"}})
    )
    posts = respx_mock.post(X_POSTS_URL).mock(
        return_value=httpx.Response(200, json={"data": {"id": "p2"}})
    )
    post_ids = await publish_match(db, x, match, plan_for(match), now=NOW)

    assert post_ids == ["p1", "p2"]  # the first post from before, then the new reply
    assert posts.call_count == 1
    body = json.loads(posts.calls[0].request.content)
    assert body["reply"] == {"in_reply_to_tweet_id": "p1"}
    assert upload.call_count == 4  # only the reply's clutch cards


async def test_too_many_failures_mark_the_match_failed(
    respx_mock: respx.MockRouter, db: Database, x: XClient, match: ProcessedMatch
) -> None:
    respx_mock.post(X_UPLOAD_URL).mock(return_value=httpx.Response(401))

    for _ in range(MAX_POST_ATTEMPTS):
        with pytest.raises(PublishError):
            await publish_match(db, x, match, plan_for(match), now=NOW)

    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    assert (stored.status, stored.attempts) == (MatchStatus.FAILED, MAX_POST_ATTEMPTS)


async def test_a_post_that_may_be_on_x_is_not_retried(
    respx_mock: respx.MockRouter, db: Database, x: XClient, match: ProcessedMatch
) -> None:
    respx_mock.post(X_UPLOAD_URL).mock(return_value=httpx.Response(200, json={"data": {"id": "m"}}))
    respx_mock.post(X_POSTS_URL).mock(
        side_effect=[httpx.Response(200, json={"data": {"id": "p1"}}), httpx.ReadTimeout("slow")]
    )

    with pytest.raises(PublishError) as error:
        await publish_match(db, x, match, plan_for(match), now=NOW)

    assert error.value.maybe_published
    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    # failed straight away: posting again could put the reply on X twice
    assert (stored.status, stored.attempts, stored.post_ids) == (MatchStatus.FAILED, 1, ["p1"])
    assert stored.error is not None
    assert "check the account" in stored.error


async def test_a_failed_match_can_be_posted_by_hand(
    respx_mock: respx.MockRouter, db: Database, x: XClient, match: ProcessedMatch
) -> None:
    db.add_match(HEAD_TO_HEAD_ID, "old", "DBHS", NOW, MatchStatus.FAILED)
    fake_x(respx_mock)

    await publish_match(db, x, match, plan_for(match), now=NOW)

    stored = db.get(HEAD_TO_HEAD_ID)
    assert stored is not None
    assert (stored.status, stored.attempts, stored.error) == (MatchStatus.POSTED, 0, None)


def test_posted_so_far(db: Database) -> None:
    assert posted_so_far(db, 1) == []
    db.add_match(1, "a", "A", NOW, MatchStatus.FINISHED)
    db.add_post_id(1, "p1")

    assert posted_so_far(db, 1) == ["p1"]
    db.mark_posted(1, NOW)
    assert posted_so_far(db, 1, force=True) == []
