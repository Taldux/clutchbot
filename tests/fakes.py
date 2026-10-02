"""Fake osu! and X API responses for respx"""

from collections.abc import Callable
from itertools import count
from typing import Any

import httpx
import respx

TOKEN_URL = "https://osu.ppy.sh/oauth/token"
X_UPLOAD_URL = "https://api.x.com/2/media/upload"
X_POSTS_URL = "https://api.x.com/2/tweets"


def fake_x(respx_mock: respx.MockRouter) -> tuple[respx.Route, respx.Route]:
    """X media uploads get ids m1, m2, ... and posts get ids p1, p2, ..."""
    media_ids, post_ids = count(1), count(1)
    upload = respx_mock.post(X_UPLOAD_URL).mock(
        side_effect=lambda _: httpx.Response(200, json={"data": {"id": f"m{next(media_ids)}"}})
    )
    posts = respx_mock.post(X_POSTS_URL).mock(
        side_effect=lambda _: httpx.Response(200, json={"data": {"id": f"p{next(post_ids)}"}})
    )
    return upload, posts


def token_response(token: str = "tok", expires_in: int = 86400) -> httpx.Response:
    return httpx.Response(
        200, json={"token_type": "Bearer", "expires_in": expires_in, "access_token": token}
    )


def fake_match_endpoint(
    match_json: dict[str, Any], page_size: int = 100
) -> Callable[[httpx.Request], httpx.Response]:
    """Serves a saved match the way osu! does: the newest `limit` events before `before`,
    oldest first, with only the users from those events"""
    all_events = match_json["events"]

    def handler(request: httpx.Request) -> httpx.Response:
        limit = int(request.url.params.get("limit", page_size))
        before = request.url.params.get("before")
        events = [e for e in all_events if before is None or e["id"] < int(before)][-limit:]
        user_ids = {e["user_id"] for e in events} | {
            score["user_id"] for e in events if e.get("game") for score in e["game"]["scores"]
        }
        users = [user for user in match_json["users"] if user["id"] in user_ids]
        return httpx.Response(200, json={**match_json, "events": events, "users": users})

    return handler
