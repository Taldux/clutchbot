import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from clutchbot.listener import loop as loop_module
from clutchbot.listener.loop import (
    heartbeat_age,
    max_heartbeat_age,
    run_listener,
    write_heartbeat,
)
from tests.conftest import metric


class FakePoller:
    """Counts polls, can fail on chosen ones, and asks to stop after `stop_after` polls"""

    def __init__(self, stop: asyncio.Event, stop_after: int, fail_on: tuple[int, ...] = ()) -> None:
        self.polls = 0
        self._stop = stop
        self._stop_after = stop_after
        self._fail_on = fail_on

    async def poll_once(self, now: datetime) -> None:
        self.polls += 1
        if self.polls >= self._stop_after:
            self._stop.set()
        if self.polls in self._fail_on:
            raise RuntimeError("osu! is down")


def run(poller: FakePoller, stop: asyncio.Event, tmp_path: Path, interval: float = 0.01) -> None:
    listener = run_listener(
        poller,
        stop,
        interval_seconds=interval,
        heartbeat_path=tmp_path / "heartbeat",
        image_cache_dir=tmp_path / "image-cache",
    )
    # a broken loop fails the test instead of hanging it
    asyncio.run(asyncio.wait_for(listener, timeout=5))


def test_polls_until_stopped_and_writes_the_heartbeat(tmp_path: Path) -> None:
    stop = asyncio.Event()
    poller = FakePoller(stop, stop_after=3)

    run(poller, stop, tmp_path)

    assert poller.polls == 3
    heartbeat = datetime.fromisoformat((tmp_path / "heartbeat").read_text(encoding="utf-8"))
    assert datetime.now(UTC) - heartbeat < timedelta(seconds=5)


def test_polls_are_counted_and_timed(tmp_path: Path) -> None:
    ok = metric("clutchbot_polls_total", result="ok")
    errors = metric("clutchbot_polls_total", result="error")
    timed = metric("clutchbot_poll_duration_seconds_count")
    stop = asyncio.Event()

    run(FakePoller(stop, stop_after=3, fail_on=(2,)), stop, tmp_path)

    assert metric("clutchbot_polls_total", result="ok") == ok + 2
    assert metric("clutchbot_polls_total", result="error") == errors + 1
    assert metric("clutchbot_poll_duration_seconds_count") == timed + 3
    last_poll = metric("clutchbot_last_poll_timestamp_seconds")
    assert abs(datetime.now(UTC).timestamp() - last_poll) < 5


def test_a_failed_poll_does_not_end_the_loop(tmp_path: Path) -> None:
    stop = asyncio.Event()
    poller = FakePoller(stop, stop_after=3, fail_on=(1, 2))

    run(poller, stop, tmp_path)

    assert poller.polls == 3
    assert (tmp_path / "heartbeat").exists()  # written after failed polls too


def test_disk_errors_do_not_end_the_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_prune(folder: Path, unused_since: datetime) -> int:
        raise PermissionError("image cache not writable")

    monkeypatch.setattr(loop_module, "prune_image_cache", broken_prune)
    (tmp_path / "blocker").write_text("a file where the data folder should be", encoding="utf-8")
    stop = asyncio.Event()
    poller = FakePoller(stop, stop_after=3)

    asyncio.run(
        asyncio.wait_for(
            run_listener(
                poller,
                stop,
                interval_seconds=0.01,
                heartbeat_path=tmp_path / "blocker" / "heartbeat",
                image_cache_dir=tmp_path / "image-cache",
            ),
            timeout=5,
        )
    )

    assert poller.polls == 3


def test_stop_ends_the_wait_between_polls_at_once(tmp_path: Path) -> None:
    stop = asyncio.Event()
    poller = FakePoller(stop, stop_after=99)

    async def main() -> None:
        async def stop_soon() -> None:
            await asyncio.sleep(0.05)
            stop.set()

        task = asyncio.create_task(stop_soon())
        await run_listener(
            poller,
            stop,
            interval_seconds=3600,  # without the early stop this would wait an hour
            heartbeat_path=tmp_path / "heartbeat",
            image_cache_dir=tmp_path / "image-cache",
        )
        await task

    asyncio.run(asyncio.wait_for(main(), timeout=5))

    assert poller.polls == 1


def test_old_cached_images_are_pruned(tmp_path: Path) -> None:
    cache = tmp_path / "image-cache"
    cache.mkdir()
    old, recent = cache / "old", cache / "recent"
    old.write_bytes(b"x")
    recent.write_bytes(b"x")
    forty_days_ago = (datetime.now(UTC) - timedelta(days=40)).timestamp()
    os.utime(old, (forty_days_ago, forty_days_ago))
    stop = asyncio.Event()

    run(FakePoller(stop, stop_after=1), stop, tmp_path)

    assert not old.exists()
    assert recent.exists()


def test_heartbeat_age(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    now = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)

    assert heartbeat_age(path, now) is None  # no file yet
    write_heartbeat(path, now - timedelta(minutes=3))
    assert heartbeat_age(path, now) == timedelta(minutes=3)
    path.write_text("not a time", encoding="utf-8")
    assert heartbeat_age(path, now) is None
    path.write_text("2026-09-25T18:00:00", encoding="utf-8")  # no time zone
    assert heartbeat_age(path, now) is None


def test_max_heartbeat_age() -> None:
    assert max_heartbeat_age(600) == timedelta(minutes=25)
    assert max_heartbeat_age(60) == timedelta(minutes=7)


def test_write_heartbeat(tmp_path: Path) -> None:
    moment = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)

    write_heartbeat(tmp_path / "data" / "heartbeat", moment)

    assert (tmp_path / "data" / "heartbeat").read_text(encoding="utf-8") == moment.isoformat()
