import socket

import httpx

from clutchbot.metrics import bot_version, start_metrics_server
from tests.conftest import metric


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def test_the_endpoint_serves_the_contract_metrics() -> None:
    port = free_port()

    start_metrics_server(port, dry_run=True, addr="127.0.0.1")  # no firewall prompt locally
    body = httpx.get(f"http://127.0.0.1:{port}/metrics", timeout=5).text

    assert metric("clutchbot_info", dry_run="true", version=bot_version()) == 1
    assert "clutchbot_info{" in body
    # the names the Grafana dashboard and alert rules use
    for name in (
        "clutchbot_last_poll_timestamp_seconds",
        "clutchbot_polls_total",
        "clutchbot_poll_duration_seconds",
        "clutchbot_matches_total",
        "clutchbot_matches_waiting",
        "clutchbot_render_duration_seconds",
    ):
        assert f"# TYPE {name.removesuffix('_total')}" in body


def test_bot_version_is_the_package_version() -> None:
    assert bot_version().count(".") == 2  # e.g. 0.1.1
