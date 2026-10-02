import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from clutchbot.metrics import LAST_POLL, POLL_DURATION, POLLS
from clutchbot.render.images import prune_image_cache

log = logging.getLogger(__name__)

PRUNE_IMAGES_EVERY = timedelta(days=1)
KEEP_UNUSED_IMAGES_FOR = timedelta(days=30)


class Poller(Protocol):
    async def poll_once(self, now: datetime) -> object: ...


async def run_listener(
    listener: Poller,
    stop: asyncio.Event,
    *,
    interval_seconds: float,
    heartbeat_path: Path,
    image_cache_dir: Path,
) -> None:
    """Poll every `interval_seconds until `stop`"""
    last_prune: datetime | None = None
    while not stop.is_set():
        now = datetime.now(UTC)
        with POLL_DURATION.time():
            try:
                await listener.poll_once(now)
            except Exception:
                log.exception("Poll failed, trying again next time")
                POLLS.labels(result="error").inc()
            else:
                POLLS.labels(result="ok").inc()
        finished = datetime.now(UTC)
        LAST_POLL.set(finished.timestamp())
        # disk problem doesn't stop loop
        try:
            write_heartbeat(heartbeat_path, finished)
        except OSError:
            log.exception("Couldn't write the heartbeat")

        if last_prune is None or now - last_prune >= PRUNE_IMAGES_EVERY:
            last_prune = now  # also after a failure
            try:
                deleted = await asyncio.to_thread(
                    prune_image_cache, image_cache_dir, now - KEEP_UNUSED_IMAGES_FOR
                )
            except OSError:
                log.exception("Couldn't prune the image cache")
            else:
                log.info("Pruned %d unused cached image(s)", deleted)

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
    log.info("Listener stopped")


def heartbeat_age(path: Path, now: datetime) -> timedelta | None:
    try:
        last_poll = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if last_poll.tzinfo is None:
        return None
    return now - last_poll


def max_heartbeat_age(poll_interval_seconds: int) -> timedelta:
    return timedelta(seconds=2 * poll_interval_seconds) + timedelta(minutes=5)


def write_heartbeat(path: Path, now: datetime) -> None:
    """For container health check"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    temporary.write_text(now.isoformat(), encoding="utf-8")
    temporary.replace(path)
