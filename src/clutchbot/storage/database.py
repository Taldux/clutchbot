"""SQLite for match state"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Self

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id           INTEGER PRIMARY KEY,  -- the osu! match id
    name         TEXT NOT NULL,
    acronym      TEXT NOT NULL,
    status       TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    first_seen   TEXT NOT NULL,        -- ISO 8601 UTC, like every time below
    last_checked TEXT,
    posted_at    TEXT,
    post_ids     TEXT NOT NULL DEFAULT '[]',  -- JSON list in thread order
    error        TEXT
);
CREATE INDEX IF NOT EXISTS matches_by_status ON matches (status);
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_NEWEST_SEEN_ID = "newest_seen_match_id"


class MatchStatus(StrEnum):
    WAITING = "waiting"  # waiting to finish
    FINISHED = "finished"  # ready to post
    POSTED = "posted"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass(frozen=True)
class StoredMatch:
    id: int
    name: str
    acronym: str
    status: MatchStatus
    attempts: int
    first_seen: datetime
    last_checked: datetime | None
    posted_at: datetime | None
    post_ids: list[str]
    error: str | None


class Database:
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        try:
            self._migrate()
        except Exception:
            self._conn.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {version} is newer than this bot ({SCHEMA_VERSION})"
            )
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def newest_seen_id(self) -> int | None:
        row = self._conn.execute(
            "SELECT value FROM state WHERE key = ?", (_NEWEST_SEEN_ID,)
        ).fetchone()
        return int(row["value"]) if row else None

    def set_newest_seen_id(self, match_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO state (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (_NEWEST_SEEN_ID, str(match_id)),
            )

    def add_match(
        self,
        match_id: int,
        name: str,
        acronym: str,
        now: datetime,
        status: MatchStatus = MatchStatus.WAITING,
    ) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO matches (id, name, acronym, status, first_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                (match_id, name, acronym, status, _iso(now)),
            )
        return cursor.rowcount == 1

    def get(self, match_id: int) -> StoredMatch | None:
        row = self._conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        return _to_match(row) if row else None

    def with_status(self, *statuses: MatchStatus) -> list[StoredMatch]:
        placeholders = ", ".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"SELECT * FROM matches WHERE status IN ({placeholders}) ORDER BY id",
            statuses,
        ).fetchall()
        return [_to_match(row) for row in rows]

    def mark_checked(self, match_id: int, now: datetime) -> None:
        self._update(match_id, last_checked=_iso(now))

    def mark_finished(self, match_id: int) -> None:
        self._update(match_id, status=MatchStatus.FINISHED)

    def reopen(self, match_id: int, *, forget_posts: bool = False) -> None:
        columns: dict[str, object] = {
            "status": MatchStatus.FINISHED,
            "attempts": 0,
            "error": None,
            "posted_at": None,
        }
        if forget_posts:
            columns["post_ids"] = "[]"
        self._update(match_id, **columns)

    def add_post_id(self, match_id: int, post_id: str) -> None:
        match = self._require(match_id)
        self._update(match_id, post_ids=json.dumps([*match.post_ids, post_id]))

    def mark_posted(self, match_id: int, now: datetime) -> None:
        self._update(match_id, status=MatchStatus.POSTED, posted_at=_iso(now), error=None)

    def record_failure(self, match_id: int, error: str, max_attempts: int) -> StoredMatch:
        match = self._require(match_id)
        attempts = match.attempts + 1
        status = MatchStatus.FAILED if attempts >= max_attempts else match.status
        self._update(match_id, attempts=attempts, status=status, error=error)
        return self._require(match_id)

    def expire_waiting(self, first_seen_before: datetime) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE matches SET status = ? WHERE status = ? AND first_seen < ?",
                (MatchStatus.EXPIRED, MatchStatus.WAITING, _iso(first_seen_before)),
            )
        return cursor.rowcount

    def delete_done(self, first_seen_before: datetime) -> int:
        done = (MatchStatus.POSTED, MatchStatus.FAILED, MatchStatus.EXPIRED)
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM matches WHERE status IN (?, ?, ?) AND first_seen < ?",
                (*done, _iso(first_seen_before)),
            )
        return cursor.rowcount

    def _require(self, match_id: int) -> StoredMatch:
        match = self.get(match_id)
        if match is None:
            raise KeyError(f"Match {match_id} isn't in the database")
        return match

    def _update(self, match_id: int, **columns: object) -> None:
        # column names only ever come from this class, never from outside input
        assignments = ", ".join(f"{column} = ?" for column in columns)
        with self._conn:
            cursor = self._conn.execute(
                f"UPDATE matches SET {assignments} WHERE id = ?", (*columns.values(), match_id)
            )
        if cursor.rowcount == 0:
            raise KeyError(f"Match {match_id} isn't in the database")


def _to_match(row: sqlite3.Row) -> StoredMatch:
    def when(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    return StoredMatch(
        id=row["id"],
        name=row["name"],
        acronym=row["acronym"],
        status=MatchStatus(row["status"]),
        attempts=row["attempts"],
        first_seen=datetime.fromisoformat(row["first_seen"]),
        last_checked=when(row["last_checked"]),
        posted_at=when(row["posted_at"]),
        post_ids=json.loads(row["post_ids"]),
        error=row["error"],
    )


def _iso(moment: datetime) -> str:
    # times stored as UTC
    if moment.tzinfo is None:
        raise ValueError("Times must include a time zone (use datetime.now(UTC))")
    return moment.astimezone(UTC).isoformat()
