import logging
from collections.abc import Awaitable
from importlib.metadata import PackageNotFoundError, version
from time import perf_counter

import httpx
from prometheus_client import Counter, Gauge, Histogram, Info, start_http_server

log = logging.getLogger(__name__)

# the Grafana dashboard and alerts use these names

# Seconds
POLL_BUCKETS = (1, 2, 5, 10, 20, 30, 60, 120, 300, 600)
RENDER_BUCKETS = (0.5, 1, 2, 3, 5, 8, 13, 20, 30, 60)

LAST_POLL = Gauge(
    "clutchbot_last_poll_timestamp_seconds", "When the last poll finished (Unix time)"
)
POLLS = Counter("clutchbot_polls", "Polls, by whether they finished or failed", ["result"])
POLL_DURATION = Histogram(
    "clutchbot_poll_duration_seconds", "How long one poll took", buckets=POLL_BUCKETS
)
MATCHES = Counter(
    "clutchbot_matches",
    "Match events: tracked, finished, posted, dry_run, skipped (can't be posted, e.g. no "
    "games), failed (given up) and expired",
    ["event"],
)
MATCHES_WAITING = Gauge("clutchbot_matches_waiting", "Tournament matches waiting to finish")
ACRONYMS = Gauge("clutchbot_acronyms", "Tournaments (acronyms) the listener tracks")
RENDER_DURATION = Histogram(
    "clutchbot_render_duration_seconds",
    "How long rendering all cards of one match took",
    buckets=RENDER_BUCKETS,
)
BOT_INFO = Info("clutchbot", "The running bot")

API_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30)
API_REQUESTS = Counter(
    "clutchbot_api_requests",
    "Requests to the osu! and X APIs; status is the HTTP code, or 'error' without a response",
    ["api", "endpoint", "status"],
)
API_DURATION = Histogram(
    "clutchbot_api_request_duration_seconds",
    "How long osu! and X API requests took",
    ["api", "endpoint"],
    buckets=API_BUCKETS,
)
POSTS = Counter("clutchbot_posts", "Posts published on X")
X_SPEND = Counter("clutchbot_x_spend_dollars", "Estimated X API cost in US dollars")

X_POST_COST = 0.015
X_USER_READ_COST = 0.01


def record_api_request(api: str, endpoint: str, status: int | str, seconds: float) -> None:
    API_REQUESTS.labels(api=api, endpoint=endpoint, status=str(status)).inc()
    API_DURATION.labels(api=api, endpoint=endpoint).observe(seconds)


async def timed_request(
    api: str, endpoint: str, request: Awaitable[httpx.Response]
) -> httpx.Response:
    started = perf_counter()
    try:
        response = await request
    except httpx.TransportError:
        record_api_request(api, endpoint, "error", perf_counter() - started)
        raise
    record_api_request(api, endpoint, response.status_code, perf_counter() - started)
    return response


def bot_version() -> str:
    try:
        return version("clutchbot")
    except PackageNotFoundError:
        return "unknown"


def start_metrics_server(port: int, *, dry_run: bool, addr: str = "0.0.0.0") -> None:
    BOT_INFO.info({"version": bot_version(), "dry_run": str(dry_run).lower()})
    start_http_server(port, addr=addr)
    log.info("Serving metrics on port %d", port)
