import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Generator, Sequence
from types import TracebackType
from typing import Any, Literal, Self, cast, get_args

import httpx
from oauthlib.oauth1 import SIGNATURE_HMAC_SHA1
from oauthlib.oauth1 import Client as OAuth1Signer
from pydantic import BaseModel, SecretStr
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt

from clutchbot.metrics import POSTS, X_POST_COST, X_SPEND, X_USER_READ_COST, timed_request
from clutchbot.osu.client import USER_AGENT

log = logging.getLogger(__name__)

API_URL = "https://api.x.com"
MAX_POST_LENGTH = 280
MAX_ATTEMPTS = 5
RETRY_BASE_DELAY_SECONDS = 2.0
MAX_RATE_LIMIT_WAIT_SECONDS = 900.0

# for metrics
_ENDPOINTS = {
    "/2/media/upload": "media_upload",
    "/2/tweets": "create_post",
    "/2/users/me": "users_me",
}


class XApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"X API error {status}: {detail}")
        self.status = status
        self.detail = detail


class PostMaybePublishedError(Exception):
    pass


class OAuth1Auth(httpx.Auth):
    # sign every request

    def __init__(
        self, consumer_key: str, consumer_secret: str, access_token: str, access_secret: str
    ) -> None:
        self._signer = OAuth1Signer(
            client_key=consumer_key,
            client_secret=consumer_secret,
            resource_owner_key=access_token,
            resource_owner_secret=access_secret,
            signature_method=SIGNATURE_HMAC_SHA1,
        )

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        method = _http_method(request.method)
        _, headers, _ = self._signer.sign(str(request.url), http_method=method)
        request.headers["Authorization"] = headers["Authorization"]
        yield request


_HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


def _http_method(method: str) -> _HttpMethod:
    if method not in get_args(_HttpMethod):
        raise ValueError(f"Unsupported HTTP method for X: {method}")
    return cast(_HttpMethod, method)


class _MediaData(BaseModel):
    id: str


class _MediaUploadResponse(BaseModel):
    data: _MediaData


class XAccount(BaseModel):
    id: str
    username: str
    name: str


class _MeResponse(BaseModel):
    data: XAccount


class _PostData(BaseModel):
    id: str


class _CreatePostResponse(BaseModel):
    data: _PostData


class XClient:
    def __init__(
        self,
        consumer_key: SecretStr,
        consumer_secret: SecretStr,
        access_token: SecretStr,
        access_secret: SecretStr,
        *,
        timeout: float = 60.0,
    ) -> None:
        auth = OAuth1Auth(
            consumer_key.get_secret_value(),
            consumer_secret.get_secret_value(),
            access_token.get_secret_value(),
            access_secret.get_secret_value(),
        )
        self._http = httpx.AsyncClient(
            base_url=API_URL, auth=auth, timeout=timeout, headers={"User-Agent": USER_AGENT}
        )
        # replaced in tests
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    async def get_me(self) -> XAccount:
        response = await self._send("GET", "/2/users/me", retry_on=_safe_to_retry)
        X_SPEND.inc(X_USER_READ_COST)
        return _MeResponse.model_validate(response.json()).data

    async def upload_image(self, png: bytes) -> str:
        response = await self._send(
            "POST",
            "/2/media/upload",
            retry_on=_safe_to_retry,
            files={"media": ("card.png", png, "image/png")},
            data={"media_category": "tweet_image"},
        )
        media_id = _MediaUploadResponse.model_validate(response.json()).data.id
        log.info("Uploaded image (%d bytes) as media %s", len(png), media_id)
        return media_id

    async def create_post(
        self, text: str, media_ids: Sequence[str] = (), reply_to: str | None = None
    ) -> str:
        if len(text) > MAX_POST_LENGTH:
            raise ValueError(f"Post text is {len(text)} characters, the limit is {MAX_POST_LENGTH}")
        body: dict[str, Any] = {"text": text}
        if media_ids:
            body["media"] = {"media_ids": list(media_ids)}
        if reply_to is not None:
            body["reply"] = {"in_reply_to_tweet_id": reply_to}
        try:
            response = await self._send("POST", "/2/tweets", retry_on=_post_is_retryable, json=body)
        except (XApiError, httpx.TransportError) as exc:
            if _maybe_published(exc):
                reason = f"HTTP {exc.status}" if isinstance(exc, XApiError) else type(exc).__name__
                raise PostMaybePublishedError(
                    f"X may have published the post anyway ({reason}), "
                    "check the account before posting it again"
                ) from exc
            raise
        post_id = _CreatePostResponse.model_validate(response.json()).data.id
        POSTS.inc()
        X_SPEND.inc(X_POST_COST)
        log.info("Published post %s%s", post_id, f" (reply to {reply_to})" if reply_to else "")
        return post_id

    async def _send(
        self, method: str, path: str, *, retry_on: Callable[[BaseException], bool], **kwargs: Any
    ) -> httpx.Response:
        retrying = AsyncRetrying(
            retry=retry_if_exception(retry_on),
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=_retry_wait,
            sleep=self._sleep,
            before_sleep=_log_retry,
            reraise=True,
        )
        return await retrying(self._send_once, method, path, **kwargs)

    async def _send_once(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        request = self._http.request(method, path, **kwargs)
        response = await timed_request("x", _ENDPOINTS.get(path, "other"), request)
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise _RateLimitedError(response)
        if response.is_error:
            raise _api_error(response)
        return response


class _RateLimitedError(XApiError):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(response.status_code, "rate limited")
        self.response = response


def _api_error(response: httpx.Response) -> XApiError:
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = None
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("title")
        if not detail and isinstance(body.get("errors"), list):
            detail = "; ".join(
                str(error.get("message", error)) if isinstance(error, dict) else str(error)
                for error in body["errors"]
            )
    return XApiError(response.status_code, str(detail or response.reason_phrase))


_NEVER_SENT = httpx.ConnectError | httpx.ConnectTimeout | httpx.PoolTimeout


def _post_is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, _RateLimitedError | _NEVER_SENT)


def _maybe_published(exc: BaseException) -> bool:
    if isinstance(exc, XApiError):
        return exc.status >= 500
    return isinstance(exc, httpx.TransportError) and not isinstance(exc, _NEVER_SENT)


def _safe_to_retry(exc: BaseException) -> bool:
    if isinstance(exc, XApiError):
        return exc.status == 429 or exc.status >= 500
    return isinstance(exc, httpx.TransportError)


def _retry_wait(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, _RateLimitedError):
        reset = exc.response.headers.get("x-rate-limit-reset", "")
        if reset.isdigit():
            return min(max(float(reset) - time.time(), 1.0), MAX_RATE_LIMIT_WAIT_SECONDS)
    delay = RETRY_BASE_DELAY_SECONDS * 2.0 ** (retry_state.attempt_number - 1)
    return min(delay, MAX_RATE_LIMIT_WAIT_SECONDS)


def _log_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    reason = f"HTTP {exc.status}" if isinstance(exc, XApiError) else type(exc).__name__
    wait = retry_state.next_action.sleep if retry_state.next_action else 0.0
    log.warning(
        "X request failed (%s), attempt %d of %d, retrying in %.0f s",
        reason,
        retry_state.attempt_number,
        MAX_ATTEMPTS,
        wait,
    )
