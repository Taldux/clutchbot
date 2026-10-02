import json
import time
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from pydantic import SecretStr

from clutchbot.twitter import client as client_module
from clutchbot.twitter.client import PostMaybePublishedError, XApiError, XClient
from tests.conftest import metric

UPLOAD_URL = "https://api.x.com/2/media/upload"
POSTS_URL = "https://api.x.com/2/tweets"


def media_response(media_id: str = "710511363345354753") -> httpx.Response:
    return httpx.Response(200, json={"data": {"id": media_id, "media_key": f"3_{media_id}"}})


def post_response(post_id: str = "1445880548472328192") -> httpx.Response:
    return httpx.Response(200, json={"data": {"id": post_id, "text": "hi"}})


@pytest.fixture
async def x() -> AsyncIterator[XClient]:
    keys = [SecretStr(value) for value in ("ck", "cs", "at", "as")]
    async with XClient(*keys) as client:
        yield client


@pytest.fixture
def sleeps(x: XClient, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Records retry waits instead of actually sleeping"""
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(x, "_sleep", fake_sleep)
    return recorded


def oauth_params(request: httpx.Request) -> dict[str, str]:
    header = request.headers["Authorization"]
    assert header.startswith("OAuth ")
    pairs = [part.strip().split("=", 1) for part in header.removeprefix("OAuth ").split(",")]
    return {key: value.strip('"') for key, value in pairs}


def spend() -> float:
    return metric("clutchbot_x_spend_dollars_total")


async def test_get_me(respx_mock: respx.MockRouter, x: XClient) -> None:
    route = respx_mock.get("https://api.x.com/2/users/me").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "42", "username": "ClutchBot", "name": "Clutch Bot"}}
        )
    )
    dollars = spend()

    account = await x.get_me()

    assert (account.id, account.username, account.name) == ("42", "ClutchBot", "Clutch Bot")
    assert oauth_params(route.calls[0].request)["oauth_token"] == "at"
    assert spend() == pytest.approx(dollars + 0.01)  # checking the keys costs a user read


async def test_upload_image(respx_mock: respx.MockRouter, x: XClient) -> None:
    route = respx_mock.post(UPLOAD_URL).mock(return_value=media_response("123"))

    assert await x.upload_image(b"\x89PNG fake") == "123"

    request = route.calls[0].request
    body = request.content
    assert b'name="media_category"' in body
    assert b"tweet_image" in body
    assert b'name="media"; filename="card.png"' in body
    assert b"\x89PNG fake" in body
    params = oauth_params(request)
    assert params["oauth_consumer_key"] == "ck"
    assert params["oauth_token"] == "at"
    assert params["oauth_signature_method"] == "HMAC-SHA1"
    assert params["oauth_signature"]


async def test_create_post_with_images_as_a_reply(respx_mock: respx.MockRouter, x: XClient) -> None:
    route = respx_mock.post(POSTS_URL).mock(return_value=post_response("999"))

    post_id = await x.create_post("Clutch moments", ["1", "2"], reply_to="555")

    assert post_id == "999"
    assert json.loads(route.calls[0].request.content) == {
        "text": "Clutch moments",
        "media": {"media_ids": ["1", "2"]},
        "reply": {"in_reply_to_tweet_id": "555"},
    }


async def test_every_request_gets_a_fresh_signature(
    respx_mock: respx.MockRouter, x: XClient
) -> None:
    route = respx_mock.post(POSTS_URL).mock(return_value=post_response())

    await x.create_post("one")
    await x.create_post("two")

    assert json.loads(route.calls[0].request.content) == {"text": "one"}
    first, second = (oauth_params(call.request) for call in route.calls)
    assert first["oauth_nonce"] != second["oauth_nonce"]
    assert first["oauth_signature"] != second["oauth_signature"]


async def test_too_long_text_is_refused_before_sending(x: XClient) -> None:
    with pytest.raises(ValueError, match="281 characters"):
        await x.create_post("x" * 281)


async def test_rate_limited_post_waits_for_the_reset(
    respx_mock: respx.MockRouter, x: XClient, sleeps: list[float]
) -> None:
    reset = str(int(time.time()) + 30)
    route = respx_mock.post(POSTS_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"x-rate-limit-reset": reset}),
            post_response("42"),
        ]
    )

    assert await x.create_post("hi") == "42"
    assert route.call_count == 2
    assert len(sleeps) == 1
    assert 25 <= sleeps[0] <= 31


async def test_rate_limit_wait_is_capped(
    respx_mock: respx.MockRouter, x: XClient, sleeps: list[float]
) -> None:
    reset = str(int(time.time()) + 10_000)
    respx_mock.post(POSTS_URL).mock(
        side_effect=[httpx.Response(429, headers={"x-rate-limit-reset": reset}), post_response()]
    )

    await x.create_post("hi")

    assert sleeps == [client_module.MAX_RATE_LIMIT_WAIT_SECONDS]


async def test_server_error_on_a_post_is_not_retried(
    respx_mock: respx.MockRouter, x: XClient, sleeps: list[float]
) -> None:
    # X may have created the post anyway, so a retry could post twice
    route = respx_mock.post(POSTS_URL).mock(return_value=httpx.Response(503))
    posts, dollars = metric("clutchbot_posts_total"), spend()
    failed = metric("clutchbot_api_requests_total", api="x", endpoint="create_post", status="503")

    with pytest.raises(PostMaybePublishedError, match="HTTP 503"):
        await x.create_post("hi")

    assert route.call_count == 1
    assert sleeps == []
    # a failed post costs nothing, but the request is still counted
    assert (metric("clutchbot_posts_total"), spend()) == (posts, dollars)
    after = metric("clutchbot_api_requests_total", api="x", endpoint="create_post", status="503")
    assert after == failed + 1


async def test_timeout_on_a_post_is_not_retried(
    respx_mock: respx.MockRouter, x: XClient, sleeps: list[float]
) -> None:
    route = respx_mock.post(POSTS_URL).mock(side_effect=httpx.ReadTimeout("no answer"))

    with pytest.raises(PostMaybePublishedError, match="ReadTimeout"):
        await x.create_post("hi")

    assert route.call_count == 1
    assert sleeps == []


async def test_connection_failure_on_a_post_is_retried(
    respx_mock: respx.MockRouter, x: XClient, sleeps: list[float]
) -> None:
    respx_mock.post(POSTS_URL).mock(side_effect=[httpx.ConnectError("refused"), post_response()])

    await x.create_post("hi")

    assert sleeps == [2.0]


async def test_server_errors_on_an_upload_are_retried(
    respx_mock: respx.MockRouter, x: XClient, sleeps: list[float]
) -> None:
    route = respx_mock.post(UPLOAD_URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(502), media_response("7")]
    )

    assert await x.upload_image(b"png") == "7"
    assert route.call_count == 3
    assert sleeps == [2.0, 4.0]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(403, json={"title": "Forbidden", "detail": "Not permitted"}),
            "Not permitted",
        ),
        (httpx.Response(400, json={"errors": [{"message": "Bad media id"}]}), "Bad media id"),
        (httpx.Response(401, text="nope"), "Unauthorized"),
    ],
)
async def test_errors_carry_the_explanation(
    respx_mock: respx.MockRouter, x: XClient, response: httpx.Response, expected: str
) -> None:
    respx_mock.post(POSTS_URL).mock(return_value=response)

    with pytest.raises(XApiError) as error:
        await x.create_post("hi")

    assert error.value.status == response.status_code
    assert error.value.detail == expected


async def test_a_post_is_counted_with_its_cost(respx_mock: respx.MockRouter, x: XClient) -> None:
    respx_mock.post(POSTS_URL).mock(return_value=post_response())
    respx_mock.post(UPLOAD_URL).mock(return_value=media_response())
    posts, dollars = metric("clutchbot_posts_total"), spend()
    created = metric("clutchbot_api_requests_total", api="x", endpoint="create_post", status="200")
    uploads = metric("clutchbot_api_requests_total", api="x", endpoint="media_upload", status="200")

    media_id = await x.upload_image(b"png")
    await x.create_post("hi", [media_id])

    assert metric("clutchbot_posts_total") == posts + 1
    assert spend() == pytest.approx(dollars + 0.015)  # uploads are free
    after_created = metric(
        "clutchbot_api_requests_total", api="x", endpoint="create_post", status="200"
    )
    after_uploads = metric(
        "clutchbot_api_requests_total", api="x", endpoint="media_upload", status="200"
    )
    assert (after_created, after_uploads) == (created + 1, uploads + 1)
