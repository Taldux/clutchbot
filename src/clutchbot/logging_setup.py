import json
import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit

from clutchbot.config import Settings
from clutchbot.log_context import current_match_id

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

_NOISY_LOGGERS = ("httpx", "httpcore", "oauthlib", "playwright")


def _redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


class _RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, secrets: list[str]) -> None:
        super().__init__(fmt)
        self._secrets = [secret for secret in secrets if secret]

    def format(self, record: logging.LogRecord) -> str:
        return _redact(super().format(record), self._secrets)


class _JsonFormatter(logging.Formatter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [secret for secret in secrets if secret]

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact(record.getMessage(), self._secrets),
        }
        match_id = current_match_id()
        if match_id is not None:
            entry["match_id"] = match_id
        if record.exc_info:
            entry["exception"] = _redact(self.formatException(record.exc_info), self._secrets)
        return json.dumps(entry, ensure_ascii=False)


def _secret_values(settings: Settings) -> list[str]:
    secrets = (
        settings.osu_client_secret,
        settings.twitter_consumer_key,
        settings.twitter_consumer_secret,
        settings.twitter_access_token,
        settings.twitter_access_secret,
        settings.ntfy_url,
    )
    values = [secret.get_secret_value() for secret in secrets if secret is not None]
    if settings.ntfy_url is not None:
        values.append(urlsplit(settings.ntfy_url.get_secret_value()).path.strip("/"))
    return values


def setup_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    secrets = _secret_values(settings)
    if settings.log_format == "json":
        handler.setFormatter(_JsonFormatter(secrets))
    else:
        handler.setFormatter(_RedactingFormatter(_FORMAT, secrets))
    logging.basicConfig(level=settings.log_level, handlers=[handler], force=True)
    # debug logs can contain auth headers
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
