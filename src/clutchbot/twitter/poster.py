import logging
from collections.abc import Callable, Sequence

from clutchbot.twitter.client import PostMaybePublishedError, XClient
from clutchbot.twitter.posts import PlannedPost

log = logging.getLogger(__name__)


class PublishError(Exception):
    def __init__(self, posted_ids: list[str], cause: Exception) -> None:
        super().__init__(f"Publishing stopped after {len(posted_ids)} post(s): {cause}")
        self.posted_ids = posted_ids
        self.maybe_published = isinstance(cause, PostMaybePublishedError)


def post_url(post_id: str) -> str:
    return f"https://x.com/i/status/{post_id}"


async def publish(
    x: XClient,
    posts: list[PlannedPost],
    *,
    already_posted: Sequence[str] = (),
    on_posted: Callable[[str], None] | None = None,
) -> list[str]:
    if len(already_posted) > len(posts):
        raise ValueError(
            f"{len(already_posted)} posts already published, but only {len(posts)} planned"
        )
    posted_ids = list(already_posted)
    for post in posts[len(posted_ids) :]:
        try:
            media_ids = [await x.upload_image(card.png) for card in post.cards]
            reply_to = posted_ids[-1] if posted_ids else None
            post_id = await x.create_post(post.text, media_ids, reply_to=reply_to)
        except Exception as exc:
            raise PublishError(posted_ids, exc) from exc
        log.info("Published %s", post_url(post_id))
        posted_ids.append(post_id)
        if on_posted is not None:
            on_posted(post_id)
    return posted_ids
