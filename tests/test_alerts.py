import json
import re

import httpx
import pytest
import respx

from clutchbot.alerts import Alerter, Priority, split_topic_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://ntfy.sh/clutchbot-abc123", ("https://ntfy.sh", "clutchbot-abc123")),
        ("https://ntfy.sh/clutchbot-abc123/", ("https://ntfy.sh", "clutchbot-abc123")),
        ("http://ntfy.local:8080/topic", ("http://ntfy.local:8080", "topic")),
    ],
)
def test_split_topic_url(url: str, expected: tuple[str, str]) -> None:
    assert split_topic_url(url) == expected


@pytest.mark.parametrize(
    "url",
    ["ntfy.sh/topic", "https://ntfy.sh", "https://ntfy.sh/", "https://ntfy.sh/a/b", "ftp://x/t"],
)
def test_split_topic_url_rejects_other_urls(url: str) -> None:
    with pytest.raises(ValueError, match=re.escape("https://ntfy.sh/<topic>")):
        split_topic_url(url)


async def test_send_posts_json_to_the_server(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post("https://ntfy.sh/").mock(return_value=httpx.Response(200))

    async with Alerter("https://ntfy.sh/my-topic") as alerter:
        await alerter.send(
            "Match failed", "Grand Finals: ñ ö 日本", priority=Priority.HIGH, tags=["warning"]
        )

    assert json.loads(route.calls[0].request.content) == {
        "topic": "my-topic",
        "title": "Match failed",
        "message": "Grand Finals: ñ ö 日本",  # any characters, since it's JSON
        "priority": 4,
        "tags": ["warning"],
    }


async def test_a_failed_send_is_only_logged(
    respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    respx_mock.post("https://ntfy.sh/").mock(side_effect=httpx.ConnectError("offline"))

    async with Alerter("https://ntfy.sh/my-topic") as alerter:
        await alerter.send("Title", "message")  # must not raise

    assert "Couldn't send the alert to ntfy: ConnectError" in caplog.text


async def test_without_a_url_alerts_are_only_logged(caplog: pytest.LogCaptureFixture) -> None:
    async with Alerter(None) as alerter:
        assert not alerter.enabled
        await alerter.send("Match failed", "details", priority=Priority.HIGH)

    assert "Alert: Match failed: details" in caplog.text
