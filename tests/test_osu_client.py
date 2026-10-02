from collections.abc import AsyncIterator
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from pydantic import SecretStr

from clutchbot.osu import client as client_module
from clutchbot.osu.client import API_VERSION, OsuClient
from clutchbot.osu.models import MatchDetail
from tests.conftest import MATCH_IDS, load_fixture, metric
from tests.fakes import TOKEN_URL, fake_match_endpoint, token_response

MATCH_URL = "https://osu.ppy.sh/api/v2/matches/1"


@pytest.fixture
async def osu() -> AsyncIterator[OsuClient]:
    async with OsuClient(123, SecretStr("s3cret")) as client:
        yield client


@pytest.fixture
def sleeps(osu: OsuClient, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Records retry waits instead of actually sleeping"""
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(osu, "_sleep", fake_sleep)
    return recorded


async def test_token_is_fetched_once_and_sent(respx_mock: respx.MockRouter, osu: OsuClient) -> None:
    token_route = respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    api_route = respx_mock.get(MATCH_URL).mock(return_value=httpx.Response(200, json={"ok": 1}))

    assert await osu._get_json("/matches/1") == {"ok": 1}
    await osu._get_json("/matches/1")

    assert token_route.call_count == 1
    form = parse_qs(token_route.calls[0].request.content.decode())
    assert form == {
        "client_id": ["123"],
        "client_secret": ["s3cret"],
        "grant_type": ["client_credentials"],
        "scope": ["public"],
    }
    request = api_route.calls[-1].request
    assert request.headers["Authorization"] == "Bearer tok"
    assert request.headers["x-api-version"] == API_VERSION


async def test_token_is_refreshed_before_it_expires(
    respx_mock: respx.MockRouter, osu: OsuClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 1000.0
    monkeypatch.setattr(client_module, "monotonic", lambda: now)
    token_route = respx_mock.post(TOKEN_URL).mock(
        side_effect=[token_response("first", 3600), token_response("second", 3600)]
    )
    api_route = respx_mock.get(MATCH_URL).mock(return_value=httpx.Response(200, json={}))

    await osu._get_json("/matches/1")
    now += 3600 - client_module.TOKEN_REFRESH_MARGIN_SECONDS - 1  # just before the margin
    await osu._get_json("/matches/1")
    assert token_route.call_count == 1

    now += 1  # inside the margin
    await osu._get_json("/matches/1")
    assert token_route.call_count == 2
    assert api_route.calls[-1].request.headers["Authorization"] == "Bearer second"


async def test_401_fetches_a_new_token_and_retries_once(
    respx_mock: respx.MockRouter, osu: OsuClient
) -> None:
    token_route = respx_mock.post(TOKEN_URL).mock(
        side_effect=[token_response("old"), token_response("new")]
    )
    api_route = respx_mock.get(MATCH_URL).mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"ok": 1})]
    )

    assert await osu._get_json("/matches/1") == {"ok": 1}
    assert token_route.call_count == 2
    assert api_route.calls[-1].request.headers["Authorization"] == "Bearer new"


async def test_second_401_raises(respx_mock: respx.MockRouter, osu: OsuClient) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    respx_mock.get(MATCH_URL).mock(return_value=httpx.Response(401))

    with pytest.raises(httpx.HTTPStatusError):
        await osu._get_json("/matches/1")


async def test_server_errors_are_retried_with_backoff(
    respx_mock: respx.MockRouter, osu: OsuClient, sleeps: list[float]
) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    route = respx_mock.get(MATCH_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(502),
            httpx.Response(200, json={"ok": 1}),
        ]
    )

    assert await osu._get_json("/matches/1") == {"ok": 1}
    assert route.call_count == 3
    assert sleeps == [1, 2]


async def test_network_errors_are_retried(
    respx_mock: respx.MockRouter, osu: OsuClient, sleeps: list[float]
) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    respx_mock.get(MATCH_URL).mock(
        side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json={"ok": 1})]
    )

    assert await osu._get_json("/matches/1") == {"ok": 1}
    assert sleeps == [1]


async def test_429_waits_for_retry_after(
    respx_mock: respx.MockRouter, osu: OsuClient, sleeps: list[float]
) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    respx_mock.get(MATCH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(429, headers={"Retry-After": "9999"}),
            httpx.Response(200, json={"ok": 1}),
        ]
    )

    assert await osu._get_json("/matches/1") == {"ok": 1}
    assert sleeps == [7, client_module.MAX_RETRY_WAIT_SECONDS]


async def test_gives_up_after_max_attempts(
    respx_mock: respx.MockRouter, osu: OsuClient, sleeps: list[float]
) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    route = respx_mock.get(MATCH_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await osu._get_json("/matches/1")
    assert route.call_count == client_module.MAX_ATTEMPTS
    assert sleeps == [1, 2, 4, 8]


async def test_client_errors_are_not_retried(
    respx_mock: respx.MockRouter, osu: OsuClient, sleeps: list[float]
) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    route = respx_mock.get(MATCH_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(httpx.HTTPStatusError):
        await osu._get_json("/matches/1")
    assert route.call_count == 1
    assert sleeps == []


@pytest.mark.parametrize("page_size", [100, 10, 1])
@pytest.mark.parametrize("match_id", MATCH_IDS)
async def test_get_match_pages_back_to_the_first_event(
    respx_mock: respx.MockRouter, osu: OsuClient, match_id: int, page_size: int
) -> None:
    match_json = load_fixture(match_id)
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    route = respx_mock.get(path=f"/api/v2/matches/{match_id}").mock(
        side_effect=fake_match_endpoint(match_json, page_size)
    )

    detail = await osu.get_match(match_id)

    assert detail == MatchDetail.model_validate(match_json)
    expected_requests = -(-len(match_json["events"]) // page_size)  # ceiling division
    assert route.call_count == expected_requests


async def test_get_match_stops_on_an_empty_page(
    respx_mock: respx.MockRouter, osu: OsuClient
) -> None:
    match_json = load_fixture(MATCH_IDS[0])
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    last_page = {**match_json, "events": match_json["events"][-5:]}
    respx_mock.get(path=f"/api/v2/matches/{MATCH_IDS[0]}").mock(
        side_effect=[
            httpx.Response(200, json=last_page),
            httpx.Response(200, json={**match_json, "events": [], "users": []}),
        ]
    )

    detail = await osu.get_match(MATCH_IDS[0])

    assert [e.id for e in detail.events] == [e["id"] for e in match_json["events"][-5:]]


async def test_get_match_info_requests_a_single_event(
    respx_mock: respx.MockRouter, osu: OsuClient
) -> None:
    match_json = load_fixture(MATCH_IDS[0])
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    route = respx_mock.get(path=f"/api/v2/matches/{MATCH_IDS[0]}").mock(
        side_effect=fake_match_endpoint(match_json, page_size=100)
    )

    match = await osu.get_match_info(MATCH_IDS[0])

    assert route.calls[0].request.url.params["limit"] == "1"
    assert match.name == "DBHS: (NINERIK) vs (Welter)"
    assert match.is_finished


LISTING_IDS = list(range(1130, 1000, -1))  # 130 matches, newest first


def fake_listing_endpoint(request: httpx.Request) -> httpx.Response:
    """Serves LISTING_IDS in pages of `limit`; the cursor is the last id of the previous page"""
    limit = int(request.url.params["limit"])
    cursor = request.url.params.get("cursor_string")
    remaining = [i for i in LISTING_IDS if cursor is None or i < int(cursor)]
    page = remaining[:limit]
    next_cursor = str(page[-1]) if len(remaining) > limit else None
    matches = [
        {"id": i, "name": f"match {i}", "start_time": "2026-09-23T12:00:00+00:00", "end_time": None}
        for i in page
    ]
    return httpx.Response(200, json={"matches": matches, "cursor_string": next_cursor})


def requests(endpoint: str, status: str) -> float:
    return metric("clutchbot_api_requests_total", api="osu", endpoint=endpoint, status=status)


@pytest.fixture
def listing_route(respx_mock: respx.MockRouter) -> respx.Route:
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    return respx_mock.get(path="/api/v2/matches").mock(side_effect=fake_listing_endpoint)


async def test_list_matches_sends_paging_and_filter_params(
    listing_route: respx.Route, osu: OsuClient
) -> None:
    counted = requests("matches", "200")

    await osu.list_matches()
    await osu.list_matches("1081", active=False)

    first, second = (call.request.url.params for call in listing_route.calls)
    assert dict(first) == {"limit": "50", "sort": "id_desc"}
    assert dict(second) == {
        "limit": "50",
        "sort": "id_desc",
        "cursor_string": "1081",
        "active": "false",
    }
    assert requests("matches", "200") == counted + 2  # the listing has its own endpoint name


@pytest.mark.parametrize(
    ("newest_seen_id", "max_pages", "expected_ids", "pages"),
    [
        (None, 40, LISTING_IDS[:50], 1),  # first start: only the newest page
        (1060, 40, list(range(1130, 1060, -1)), 2),  # stops at the newest seen id
        (1130, 40, [], 1),  # nothing new
        (1, 40, LISTING_IDS, 3),  # ends on the last page
        (1, 2, LISTING_IDS[:100], 2),  # never more than max_pages
    ],
)
async def test_list_new_matches(
    listing_route: respx.Route,
    osu: OsuClient,
    newest_seen_id: int | None,
    max_pages: int,
    expected_ids: list[int],
    pages: int,
) -> None:
    matches = await osu.list_new_matches(newest_seen_id, max_pages)

    assert [m.id for m in matches] == expected_ids
    assert listing_route.call_count == pages


async def test_requests_are_counted_per_endpoint_and_status(
    respx_mock: respx.MockRouter, osu: OsuClient, sleeps: list[float]
) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=token_response())
    respx_mock.get(MATCH_URL).mock(
        side_effect=[
            httpx.ConnectError("refused"),  # no response at all
            httpx.Response(503),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    before = {
        "token": requests("token", "200"),
        "error": requests("match", "error"),
        "503": requests("match", "503"),
        "200": requests("match", "200"),
    }
    timed = metric("clutchbot_api_request_duration_seconds_count", api="osu", endpoint="match")

    await osu._get_json("/matches/1")

    # every retry attempt is counted, so retried failures still show up
    assert requests("token", "200") == before["token"] + 1
    assert requests("match", "error") == before["error"] + 1
    assert requests("match", "503") == before["503"] + 1
    assert requests("match", "200") == before["200"] + 1
    timings = metric("clutchbot_api_request_duration_seconds_count", api="osu", endpoint="match")
    assert timings == timed + 3
