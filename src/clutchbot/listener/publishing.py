import logging
from datetime import UTC, datetime

from clutchbot.processing.pipeline import ProcessedMatch
from clutchbot.storage.database import Database, MatchStatus, StoredMatch
from clutchbot.twitter.client import XClient
from clutchbot.twitter.poster import PublishError, publish
from clutchbot.twitter.posts import PlannedPost

log = logging.getLogger(__name__)

MAX_POST_ATTEMPTS = 3


class AlreadyPostedError(Exception):
    def __init__(self, match: StoredMatch) -> None:
        super().__init__(f"Match {match.id} was already posted")
        self.match = match


def posted_so_far(db: Database, match_id: int, *, force: bool = False) -> list[str]:
    """Resume posts when they are unfinished"""
    stored = db.get(match_id)
    if stored is None:
        return []
    if stored.status is MatchStatus.POSTED:
        if not force:
            raise AlreadyPostedError(stored)
        return []
    return stored.post_ids


async def publish_match(
    db: Database,
    x: XClient,
    processed: ProcessedMatch,
    plan: list[PlannedPost],
    *,
    force: bool = False,
    now: datetime | None = None,
) -> list[str]:

    now = now or datetime.now(UTC)
    match = processed.detail.match
    already_posted = posted_so_far(db, match.id, force=force)

    stored = db.get(match.id)
    if stored is None:
        db.add_match(match.id, match.name, processed.names.acronym, now, MatchStatus.FINISHED)
    elif force and stored.status is MatchStatus.POSTED:
        db.reopen(match.id, forget_posts=True)
    elif stored.status is not MatchStatus.FINISHED:
        db.reopen(match.id)

    if already_posted:
        log.info("Match %d: resuming after %d published post(s)", match.id, len(already_posted))
    try:
        post_ids = await publish(
            x,
            plan,
            already_posted=already_posted,
            on_posted=lambda post_id: db.add_post_id(match.id, post_id),
        )
    except PublishError as exc:
        # trying again could post the same thing twice so just give up
        max_attempts = 1 if exc.maybe_published else MAX_POST_ATTEMPTS
        failed = db.record_failure(match.id, str(exc), max_attempts)
        log.warning("Match %d: attempt %d failed: %s", match.id, failed.attempts, exc)
        raise
    db.mark_posted(match.id, now)
    return post_ids
