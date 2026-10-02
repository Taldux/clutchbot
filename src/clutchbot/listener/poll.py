import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from clutchbot.acronyms import AcronymsError, AcronymsFile
from clutchbot.alerts import Alerter, Priority
from clutchbot.listener.publishing import MAX_POST_ATTEMPTS, AlreadyPostedError, publish_match
from clutchbot.log_context import match_context
from clutchbot.metrics import ACRONYMS, MATCHES, MATCHES_WAITING, RENDER_DURATION
from clutchbot.osu.client import OsuClient
from clutchbot.processing.pipeline import ProcessedMatch, ProcessingError, process_match
from clutchbot.processing.tournament import describe_lobby, parse_tournament_name
from clutchbot.render.cards import MatchCards
from clutchbot.storage.database import Database, MatchStatus, StoredMatch
from clutchbot.twitter.client import XClient
from clutchbot.twitter.poster import PublishError, post_url
from clutchbot.twitter.posts import format_plan, plan_posts

log = logging.getLogger(__name__)

KEEP_DONE_MATCHES_FOR = timedelta(days=30)

RenderCards = Callable[[ProcessedMatch], Awaitable[MatchCards]]


@dataclass
class PollReport:
    """What one poll did, for the log"""

    new: list[int] = field(default_factory=list)
    finished: list[int] = field(default_factory=list)
    posted: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    expired: int = 0

    def summary(self) -> str:
        return (
            f"{len(self.new)} new, {len(self.finished)} finished, {len(self.posted)} posted, "
            f"{len(self.skipped)} skipped, {len(self.failed)} failed, {self.expired} expired"
        )


class Listener:
    # x=None means dry run
    def __init__(
        self,
        *,
        osu: OsuClient,
        db: Database,
        acronyms: AcronymsFile,
        render_cards: RenderCards,
        x: XClient | None,
        dry_run_dir: Path,
        match_expiry: timedelta,
        warmup_games_checked: int,
        clutch_threshold_percent: float,
        alerter: Alerter,
    ) -> None:
        self._alerter = alerter
        self._osu = osu
        self._db = db
        self._acronyms = acronyms
        self._render_cards = render_cards
        self._x = x
        self._dry_run_dir = dry_run_dir
        self._match_expiry = match_expiry
        self._warmup_games_checked = warmup_games_checked
        self._clutch_threshold_percent = clutch_threshold_percent

    async def poll_once(self, now: datetime) -> PollReport:
        report = PollReport()
        await self._reload_acronyms()
        ACRONYMS.set(len(self._acronyms.current))
        try:
            await self._discover(now, report)
        except Exception:
            log.exception("Couldn't read the match listing")
        await self._check_waiting(now, report)
        for stored in self._db.with_status(MatchStatus.FINISHED):
            with match_context(stored.id):
                await self._post(stored, now, report)
        report.expired = self._db.expire_waiting(now - self._match_expiry)
        MATCHES.labels(event="expired").inc(report.expired)
        MATCHES_WAITING.set(len(self._db.with_status(MatchStatus.WAITING)))
        self._db.delete_done(now - KEEP_DONE_MATCHES_FOR)
        log.info("Poll done: %s", report.summary())
        return report

    async def _reload_acronyms(self) -> None:
        try:
            change = self._acronyms.reload_if_changed()
        except AcronymsError as exc:
            log.error("Acronyms file not reloaded, keeping the previous list: %s", exc)
            await self._alerter.send(
                "Acronyms file not reloaded",
                f"{exc}. The previous list stays in use until the file is fixed.",
                priority=Priority.HIGH,
                tags=["warning"],
            )
            return
        if change is not None:
            log.info(
                "Acronyms reloaded, now %d tournament(s): %s",
                len(self._acronyms.current),
                change.summary(),
            )

    async def _discover(self, now: datetime, report: PollReport) -> None:
        """store every new tournament lobby from the listing as waiting"""
        matches = await self._osu.list_new_matches(self._db.newest_seen_id())
        for match in matches:
            names = parse_tournament_name(match.name, self._acronyms.current)
            if names is not None and self._db.add_match(match.id, match.name, names.acronym, now):
                with match_context(match.id):
                    log.info("Tracking match %d: %s", match.id, match.name)
                report.new.append(match.id)
                MATCHES.labels(event="tracked").inc()
        if matches:
            self._db.set_newest_seen_id(max(match.id for match in matches))

    async def _check_waiting(self, now: datetime, report: PollReport) -> None:
        for stored in self._db.with_status(MatchStatus.WAITING):
            with match_context(stored.id):
                await self._check_one(stored, now, report)

    async def _check_one(self, stored: StoredMatch, now: datetime, report: PollReport) -> None:
        try:
            info = await self._osu.get_match_info(stored.id)
        except Exception as exc:
            # if osu is down, dont fail the matches
            log.warning("Couldn't check match %d, trying again next poll: %s", stored.id, exc)
            return
        self._db.mark_checked(stored.id, now)
        if info.is_finished:
            log.info("Match %d finished", stored.id)
            self._db.mark_finished(stored.id)
            report.finished.append(stored.id)
            MATCHES.labels(event="finished").inc()

    async def _post(self, stored: StoredMatch, now: datetime, report: PollReport) -> None:
        try:
            detail = await self._osu.get_match(stored.id)
            # the acronym may have perhaps left the list
            names = parse_tournament_name(
                detail.match.name, self._acronyms.current
            ) or describe_lobby(detail.match.name, self._acronyms.current)
            processed = process_match(
                detail,
                names,
                warmup_games_checked=self._warmup_games_checked,
                clutch_threshold_percent=self._clutch_threshold_percent,
            )
        except ProcessingError as exc:
            # when lobby closes without any maps played or something
            self._db.record_failure(stored.id, str(exc), max_attempts=1)
            report.skipped.append(stored.id)
            MATCHES.labels(event="skipped").inc()
            await self._alerter.send(
                "Match can't be posted",
                f"{stored.name} ({_match_link(stored.id)}): {exc}",
                priority=Priority.LOW,
                tags=["information_source"],
            )
            return
        except Exception as exc:
            await self._fail(stored, f"Couldn't fetch or process the match: {exc}", report)
            return

        try:
            with RENDER_DURATION.time():
                cards = await self._render_cards(processed)
            plan = plan_posts(processed, cards)
            if self._x is None:
                self._dry_run(processed, cards, format_plan(plan), now)
                MATCHES.labels(event="dry_run").inc()
            else:
                post_ids = await publish_match(self._db, self._x, processed, plan, now=now)
                log.info("Posted match %d: %s", stored.id, post_url(post_ids[0]))
                MATCHES.labels(event="posted").inc()
        except PublishError as exc:
            # publish_match already counted the attempt and kept the published post ids
            log.warning("Posting match %d stopped: %s", stored.id, exc)
            report.failed.append(stored.id)
            await self._alert_if_given_up(self._db.get(stored.id))
            return
        except AlreadyPostedError:
            log.warning("Match %d was already posted; not posting it again", stored.id)
            return
        except Exception as exc:
            await self._fail(stored, f"Couldn't render or post the match: {exc}", report)
            return
        report.posted.append(stored.id)

    def _dry_run(
        self, processed: ProcessedMatch, cards: MatchCards, plan: str, now: datetime
    ) -> None:
        match_id = processed.detail.match.id
        folder = self._dry_run_dir / str(match_id)
        folder.mkdir(parents=True, exist_ok=True)
        for card in cards.all:
            (folder / card.file_name).write_bytes(card.png)
        log.info("Dry run, not posting match %d (cards in %s):\n%s", match_id, folder, plan)
        # marked as handled so it isn't redone every poll; no post ids, since nothing went out
        self._db.mark_posted(match_id, now)

    async def _fail(self, stored: StoredMatch, error: str, report: PollReport) -> None:
        failed = self._db.record_failure(stored.id, error, MAX_POST_ATTEMPTS)
        log.warning(
            "Match %d: %s (attempt %d of %d)", stored.id, error, failed.attempts, MAX_POST_ATTEMPTS
        )
        report.failed.append(stored.id)
        await self._alert_if_given_up(failed)

    async def _alert_if_given_up(self, match: StoredMatch | None) -> None:
        """A match marked failed will never be posted, so someone should look at it"""
        if match is None or match.status is not MatchStatus.FAILED:
            return
        MATCHES.labels(event="failed").inc()
        await self._alerter.send(
            "Match failed",
            f"{match.name} ({_match_link(match.id)}) was not posted after "
            f"{match.attempts} attempt(s): {match.error}",
            priority=Priority.HIGH,
            tags=["warning"],
        )


def _match_link(match_id: int) -> str:
    return f"https://osu.ppy.sh/mp/{match_id}"
