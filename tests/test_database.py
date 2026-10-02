import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from clutchbot.storage.database import SCHEMA_VERSION, Database, MatchStatus

NOW = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Database]:
    with Database(":memory:") as database:
        yield database


def test_newest_seen_id(db: Database) -> None:
    assert db.newest_seen_id() is None

    db.set_newest_seen_id(117358530)
    db.set_newest_seen_id(117416879)

    assert db.newest_seen_id() == 117416879


def test_add_match_only_once(db: Database) -> None:
    assert db.add_match(1, "DBHS: (A) vs (B)", "DBHS", NOW)
    assert not db.add_match(1, "renamed", "XYZ", NOW)

    match = db.get(1)
    assert match is not None
    assert (match.name, match.acronym, match.status) == ("DBHS: (A) vs (B)", "DBHS", "waiting")
    assert (match.attempts, match.first_seen, match.post_ids) == (0, NOW, [])
    assert match.last_checked is match.posted_at is match.error is None
    assert db.get(2) is None


def test_with_status(db: Database) -> None:
    db.add_match(3, "c", "C", NOW)
    db.add_match(1, "a", "A", NOW)
    db.add_match(2, "b", "B", NOW, status=MatchStatus.FINISHED)

    assert [m.id for m in db.with_status(MatchStatus.WAITING)] == [1, 3]
    both = db.with_status(MatchStatus.WAITING, MatchStatus.FINISHED)
    assert [m.id for m in both] == [1, 2, 3]
    assert db.with_status(MatchStatus.POSTED) == []


def test_from_waiting_to_posted(db: Database) -> None:
    db.add_match(1, "a", "A", NOW)
    db.mark_checked(1, NOW + timedelta(minutes=10))
    db.mark_finished(1)
    db.add_post_id(1, "p1")
    db.add_post_id(1, "p2")
    db.mark_posted(1, NOW + timedelta(minutes=20))

    match = db.get(1)
    assert match is not None
    assert match.status is MatchStatus.POSTED
    assert match.last_checked == NOW + timedelta(minutes=10)
    assert match.posted_at == NOW + timedelta(minutes=20)
    assert match.post_ids == ["p1", "p2"]


def test_failures_count_up_until_failed(db: Database) -> None:
    db.add_match(1, "a", "A", NOW, status=MatchStatus.FINISHED)

    first = db.record_failure(1, "X API error 503", max_attempts=3)
    second = db.record_failure(1, "timeout", max_attempts=3)
    third = db.record_failure(1, "X API error 503", max_attempts=3)

    assert (first.attempts, first.status) == (1, MatchStatus.FINISHED)
    assert (second.attempts, second.status, second.error) == (2, MatchStatus.FINISHED, "timeout")
    assert (third.attempts, third.status) == (3, MatchStatus.FAILED)


def test_posting_clears_an_old_error(db: Database) -> None:
    db.add_match(1, "a", "A", NOW, status=MatchStatus.FINISHED)
    db.record_failure(1, "timeout", max_attempts=3)

    db.mark_posted(1, NOW)

    match = db.get(1)
    assert match is not None
    assert (match.error, match.attempts) == (None, 1)


def test_expire_waiting(db: Database) -> None:
    db.add_match(1, "old waiting", "A", NOW - timedelta(hours=30))
    db.add_match(2, "new waiting", "A", NOW - timedelta(hours=2))
    db.add_match(3, "old finished", "A", NOW - timedelta(hours=30), status=MatchStatus.FINISHED)

    assert db.expire_waiting(NOW - timedelta(hours=24)) == 1

    assert [m.id for m in db.with_status(MatchStatus.EXPIRED)] == [1]
    assert [m.id for m in db.with_status(MatchStatus.WAITING)] == [2]


def test_delete_done(db: Database) -> None:
    old = NOW - timedelta(days=40)
    for match_id, status in enumerate(MatchStatus, start=1):
        db.add_match(match_id, status, "A", old, status=status)
    db.add_match(99, "recent", "A", NOW, status=MatchStatus.POSTED)

    assert db.delete_done(NOW - timedelta(days=30)) == 3  # posted, failed and expired

    remaining = db.with_status(*MatchStatus)
    assert sorted(m.name for m in remaining) == ["finished", "recent", "waiting"]


def test_unknown_match(db: Database) -> None:
    with pytest.raises(KeyError, match="isn't in the database"):
        db.mark_finished(42)
    with pytest.raises(KeyError):
        db.add_post_id(42, "p1")


def test_times_need_a_time_zone_and_are_stored_as_utc(db: Database) -> None:
    with pytest.raises(ValueError, match="time zone"):
        db.add_match(1, "a", "A", datetime(2026, 9, 25, 18, 0))

    two_hours_ahead = timezone(timedelta(hours=2))
    db.add_match(2, "b", "B", datetime(2026, 9, 25, 20, 0, tzinfo=two_hours_ahead))

    match = db.get(2)
    assert match is not None
    assert match.first_seen == NOW
    assert match.first_seen.tzinfo is not None
    assert match.first_seen.utcoffset() == timedelta(0)


def test_data_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "data" / "clutchbot.db"  # the folder doesn't exist yet
    with Database(path) as db:
        db.add_match(1, "a", "A", NOW)
        db.set_newest_seen_id(1)

    with Database(path) as db:
        assert db.get(1) is not None
        assert db.newest_seen_id() == 1


def test_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "clutchbot.db"
    Database(path).close()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()

    with pytest.raises(RuntimeError, match="newer than this bot"):
        Database(path)
