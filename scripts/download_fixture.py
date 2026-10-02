"""
Usage: uv run python scripts/download_fixture.py 117358530 117416879 117500384
"""

import json
import sys
from pathlib import Path
from typing import Any

import httpx

from clutchbot.config import load_settings

TOKEN_URL = "https://osu.ppy.sh/oauth/token"
API_URL = "https://osu.ppy.sh/api/v2"
API_VERSION = "20250515"
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def get_token(client: httpx.Client) -> str:
    settings = load_settings()
    response = client.post(
        TOKEN_URL,
        data={
            "client_id": settings.osu_client_id,
            "client_secret": settings.osu_client_secret.get_secret_value(),
            "grant_type": "client_credentials",
            "scope": "public",
        },
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def download_match(client: httpx.Client, match_id: int) -> dict[str, Any]:
    response = client.get(f"{API_URL}/matches/{match_id}")
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    users = {user["id"]: user for user in data["users"]}
    events = data["events"]
    while events and events[0]["id"] != data["first_event_id"]:
        response = client.get(f"{API_URL}/matches/{match_id}", params={"before": events[0]["id"]})
        response.raise_for_status()
        page = response.json()
        if not page["events"]:
            break
        events = page["events"] + events
        users.update({user["id"]: user for user in page["users"]})

    data["events"] = events
    data["users"] = sorted(users.values(), key=lambda user: user["id"])
    return data


def main(match_ids: list[int]) -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30) as client:
        token = get_token(client)
        client.headers.update({"Authorization": f"Bearer {token}", "x-api-version": API_VERSION})
        for match_id in match_ids:
            data = download_match(client, match_id)
            path = FIXTURES_DIR / f"match_{match_id}.json"
            text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            path.write_text(text, encoding="utf-8")
            finished = "finished" if data["match"]["end_time"] else "NOT finished"
            print(f"{path.name}: {len(data['events'])} events, {finished}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main([int(arg) for arg in sys.argv[1:]])
