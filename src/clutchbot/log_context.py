from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# ContextVar instead of a global so it stays correct across async tasks
_current_match_id: ContextVar[int | None] = ContextVar("current_match_id", default=None)


@contextmanager
def match_context(match_id: int) -> Iterator[None]:
    """Adds match_id to JSON log lines written inside the block"""
    token = _current_match_id.set(match_id)
    try:
        yield
    finally:
        _current_match_id.reset(token)


def current_match_id() -> int | None:
    return _current_match_id.get()
