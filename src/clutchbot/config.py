from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_TWITTER_FIELDS = (
    "twitter_consumer_key",
    "twitter_consumer_secret",
    "twitter_access_token",
    "twitter_access_secret",
)


class ConfigError(Exception):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    osu_client_id: int = Field(gt=0)
    osu_client_secret: SecretStr

    # only for no dry run
    twitter_consumer_key: SecretStr | None = None
    twitter_consumer_secret: SecretStr | None = None
    twitter_access_token: SecretStr | None = None
    twitter_access_secret: SecretStr | None = None

    dry_run: bool = True
    poll_interval_seconds: int = Field(default=600, ge=60)
    match_expiry_hours: int = Field(default=24, ge=1)

    clutch_threshold_percent: float = Field(default=5.0, gt=0, le=100)
    warmup_games_checked: int = Field(default=2, ge=0, le=2)

    data_dir: Path = Path("data")
    acronyms_path: Path | None = None

    log_level: LogLevel = "INFO"
    log_format: Literal["text", "json"] = "text"

    ntfy_url: SecretStr | None = None

    metrics_port: int | None = Field(default=None, ge=1, le=65535)

    @model_validator(mode="after")
    def _require_twitter_when_posting(self) -> Self:
        if not self.dry_run:
            missing = [name.upper() for name in _TWITTER_FIELDS if getattr(self, name) is None]
            if missing:
                raise ValueError(f"DRY_RUN=false requires {', '.join(missing)}")
        return self

    @property
    def resolved_acronyms_path(self) -> Path:
        return self.acronyms_path or self.data_dir / "acronyms.json"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "clutchbot.db"

    @property
    def image_cache_dir(self) -> Path:
        return self.data_dir / "image-cache"

    @property
    def heartbeat_path(self) -> Path:
        return self.data_dir / "heartbeat"

    @property
    def dry_run_dir(self) -> Path:
        return self.data_dir / "dry-run"


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        name = ".".join(str(part) for part in err["loc"]).upper() or "CONFIG"
        message = "missing" if err["type"] == "missing" else err["msg"]
        lines.append(f"  {name}: {message}")
    return "Invalid configuration:\n" + "\n".join(lines)


def load_settings(dry_run: bool | None = None) -> Settings:
    try:
        return Settings() if dry_run is None else Settings(dry_run=dry_run)
    except ValidationError as exc:
        raise ConfigError(_format_errors(exc)) from None
