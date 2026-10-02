import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from pydantic import SecretStr

from clutchbot.render.cards import RenderedCard
from clutchbot.twitter.client import XClient
from clutchbot.twitter.poster import PublishError, post_url, publish
from clutchbot.twitter.posts import PlannedPost
from tests.fakes import X_POSTS_URL, X_UPLOAD_URL, fake_x


def card(name: str) -> RenderedCard:
    return RenderedCard(name, f"png of {name}".encode())


PLAN = [
    PlannedPost("result", [card("result.png")]),
    PlannedPost("clutches", [card("clutch-5.png"), card("clutch-6.png")]),
]


@pytest.fixture
async def x() -> AsyncIterator[XClient]:
    async with XClient(*(SecretStr(value) for value in ("ck", "cs", "at", "as"))) as client:
        yield client


async def test_publishes_a_thread(respx_mock: respx.MockRouter, x: XClient) -> None:
    upload, posts = fake_x(respx_mock)

    post_ids = await publish(x, PLAN)

    assert post_ids == ["p1", "p2"]
    assert upload.call_count == 3
    assert b"png of clutch-6.png" in upload.calls[2].request.content
    bodies = [json.loads(call.request.content) for call in posts.calls]
    assert bodies[0] == {"text": "result", "media": {"media_ids": ["m1"]}}
    assert bodies[1] == {
        "text": "clutches",
        "media": {"media_ids": ["m2", "m3"]},
        "reply": {"in_reply_to_tweet_id": "p1"},
    }


async def test_a_failure_says_what_was_already_posted(
    respx_mock: respx.MockRouter, x: XClient
) -> None:
    respx_mock.post(X_UPLOAD_URL).mock(return_value=httpx.Response(200, json={"data": {"id": "m"}}))
    respx_mock.post(X_POSTS_URL).mock(
        side_effect=[
            httpx.Response(200, json={"data": {"id": "p1"}}),
            httpx.Response(403, json={"detail": "Not permitted"}),
        ]
    )

    with pytest.raises(PublishError) as error:
        await publish(x, PLAN)

    assert error.value.posted_ids == ["p1"]
    assert "Not permitted" in str(error.value)


async def test_resumes_after_already_published_posts(
    respx_mock: respx.MockRouter, x: XClient
) -> None:
    upload, posts = fake_x(respx_mock)
    saved: list[str] = []

    post_ids = await publish(x, PLAN, already_posted=["p0"], on_posted=saved.append)

    assert post_ids == ["p0", "p1"]
    assert saved == ["p1"]  # only the new post
    assert upload.call_count == 2  # only the reply's two cards
    body = json.loads(posts.calls[0].request.content)
    assert body["reply"] == {"in_reply_to_tweet_id": "p0"}


async def test_on_posted_is_called_for_every_new_post(
    respx_mock: respx.MockRouter, x: XClient
) -> None:
    fake_x(respx_mock)
    saved: list[str] = []

    await publish(x, PLAN, on_posted=saved.append)

    assert saved == ["p1", "p2"]


async def test_more_already_posted_than_planned_is_an_error(x: XClient) -> None:
    with pytest.raises(ValueError, match="3 posts already published, but only 2 planned"):
        await publish(x, PLAN, already_posted=["a", "b", "c"])


def test_post_url() -> None:
    assert post_url("1445880548472328192") == "https://x.com/i/status/1445880548472328192"
