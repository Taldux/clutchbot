"""Async osu! API v2 client"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import monotonic
from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import BaseModel, SecretStr
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt

from clutchbot.metrics import timed_request
from clutchbot.osu.models import Match, MatchDetail, MatchListing

log = logging.getLogger(__name__)

BASE_URL = "https://osu.ppy.sh"
API_VERSION = "20250515"  # maybe change
USER_AGENT = "clutchbot (+https://github.com/Taldux/clutchbot)"
TOKEN_REFRESH_MARGIN_SECONDS = 300
LISTING_PAGE_SIZE = 50  # API max
MAX_ATTEMPTS = 5
RETRY_BASE_DELAY_SECONDS = 1.0
MAX_RETRY_WAIT_SECONDS = 60.0

Params = dict[str, str | int]


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == httpx.codes.TOO_MANY_REQUESTS or status >= 500
    return isinstance(exc, httpx.TransportError)


def _retry_wait(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = exc.response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            return min(float(retry_after), MAX_RETRY_WAIT_SECONDS)
    delay = RETRY_BASE_DELAY_SECONDS * 2.0 ** (retry_state.attempt_number - 1)
    return min(delay, MAX_RETRY_WAIT_SECONDS)


def _log_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError):
        reason = f"HTTP {exc.response.status_code}"
    else:
        reason = type(exc).__name__
    wait = retry_state.next_action.sleep if retry_state.next_action else 0.0
    log.warning(
        "osu! request failed (%s), attempt %d of %d, retrying in %.0f s",
        reason,
        retry_state.attempt_number,
        MAX_ATTEMPTS,
        wait,
    )


class _TokenResponse(BaseModel):
    access_token: str
    expires_in: int


class OsuClient:
    def __init__(self, client_id: int, client_secret: SecretStr, *, timeout: float = 30.0) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"x-api-version": API_VERSION, "User-Agent": USER_AGENT},
        )
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_match(self, match_id: int) -> MatchDetail:
        detail = MatchDetail.model_validate(await self._get_json(f"/matches/{match_id}"))
        events = detail.events
        # collect users from every single map
        users = {user.id: user for user in detail.users}

        while events and events[0].id != detail.first_event_id:
            page = MatchDetail.model_validate(
                await self._get_json(f"/matches/{match_id}", {"before": events[0].id})
            )
            if not page.events:
                log.warning("Match %d: no events before %d, stopping early", match_id, events[0].id)
                break
            events = page.events + events
            users.update({user.id: user for user in page.users})

        return detail.model_copy(
            update={"events": events, "users": sorted(users.values(), key=lambda user: user.id)}
        )

    async def get_match_info(self, match_id: int) -> Match:
        detail = MatchDetail.model_validate(
            await self._get_json(f"/matches/{match_id}", {"limit": 1})
        )
        return detail.match

    async def list_matches(
        self, cursor: str | None = None, *, active: bool | None = None
    ) -> MatchListing:
        params: Params = {"limit": LISTING_PAGE_SIZE, "sort": "id_desc"}
        if cursor is not None:
            params["cursor_string"] = cursor
        if active is not None:
            params["active"] = "true" if active else "false"
        return MatchListing.model_validate(await self._get_json("/matches", params))

    async def list_new_matches(
        self, newest_seen_id: int | None, max_pages: int = 40
    ) -> list[Match]:
        # after a certain id

        new_matches: list[Match] = []
        cursor: str | None = None
        for _ in range(max_pages):
            page = await self.list_matches(cursor)
            for match in page.matches:
                if newest_seen_id is not None and match.id <= newest_seen_id:
                    return new_matches
                new_matches.append(match)
            if newest_seen_id is None or page.cursor_string is None:
                return new_matches
            cursor = page.cursor_string

        log.warning(
            "Stopped after %d listing pages without reaching match %d; older matches are skipped",
            max_pages,
            newest_seen_id,
        )
        return new_matches

    async def _access_token(self) -> str:
        # stops several concurrent requests from all fetching a new token
        async with self._token_lock:
            if self._token is None or monotonic() >= self._token_expires_at:
                self._token = await self._fetch_token()
            return self._token

    async def _fetch_token(self) -> str:
        request = self._http.post(
            "/oauth/token",
            data={
                "client_id": str(self._client_id),
                "client_secret": self._client_secret.get_secret_value(),
                "grant_type": "client_credentials",
                "scope": "public",
            },
        )
        response = await timed_request("osu", "token", request)
        response.raise_for_status()
        token = _TokenResponse.model_validate(response.json())
        self._token_expires_at = monotonic() + token.expires_in - TOKEN_REFRESH_MARGIN_SECONDS
        log.info("Fetched osu! access token, valid for %d s", token.expires_in)
        return token.access_token

    async def _get_json(self, path: str, params: Params | None = None) -> Any:
        retrying = AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=_retry_wait,
            sleep=self._sleep,
            before_sleep=_log_retry,
            reraise=True,
        )
        return await retrying(self._get_json_once, path, params)

    async def _get_json_once(self, path: str, params: Params | None) -> Any:
        response = await self._send_get(path, params)
        # revoked or expired token, drop it and try once more with a fresh one
        if response.status_code == httpx.codes.UNAUTHORIZED:
            log.warning("osu! API returned 401, fetching a new token")
            self._token = None
            response = await self._send_get(path, params)
        response.raise_for_status()
        return response.json()

    async def _send_get(self, path: str, params: Params | None) -> httpx.Response:
        token = await self._access_token()
        request = self._http.get(
            f"/api/v2{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        # a fixed name per endpoint
        endpoint = "matches" if path == "/matches" else "match"
        return await timed_request("osu", endpoint, request)
