from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


load_dotenv()


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    openai_api_key: str
    database_url: str
    telegram_chat_id: int | None
    hh_poll_interval_seconds: int
    digest_hour: int
    digest_minute: int
    timezone: ZoneInfo
    shadow_mode: bool
    scoring_threshold: int
    scoring_model: str
    writing_model: str
    fallback_model: str
    hh_user_agent: str
    hh_access_token: str | None
    hh_client_id: str | None
    hh_client_secret: str | None
    gmail_enabled: bool
    gmail_client_id: str | None
    gmail_client_secret: str | None
    gmail_refresh_token: str | None
    gmail_label: str
    telegram_sources_enabled: bool
    telegram_api_id: int | None
    telegram_api_hash: str | None
    telegram_session: str | None

    @classmethod
    def from_env(cls, *, require_core: bool = True) -> "Settings":
        telegram_token = os.getenv("TELEGRAM_TOKEN", "")
        openai_api_key = os.getenv("OPENAI_API_KEY", "")
        if require_core:
            missing = [
                name
                for name, value in (
                    ("TELEGRAM_TOKEN", telegram_token),
                    ("OPENAI_API_KEY", openai_api_key),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(
                    "Не заданы обязательные переменные: " + ", ".join(missing)
                )

        database_url = os.getenv("DATABASE_URL", "sqlite:///career_agent.db")
        if database_url.startswith("postgres://"):
            database_url = database_url.replace(
                "postgres://", "postgresql+psycopg://", 1
            )
        elif database_url.startswith("postgresql://"):
            database_url = database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )

        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        api_id = os.getenv("TELEGRAM_API_ID")
        return cls(
            telegram_token=telegram_token,
            openai_api_key=openai_api_key,
            database_url=database_url,
            telegram_chat_id=int(chat_id) if chat_id else None,
            hh_poll_interval_seconds=_int_env("HH_POLL_INTERVAL_SECONDS", 600),
            digest_hour=_int_env("DIGEST_HOUR", 19),
            digest_minute=_int_env("DIGEST_MINUTE", 0),
            timezone=ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow")),
            shadow_mode=_bool_env("SHADOW_MODE", True),
            scoring_threshold=_int_env("SCORING_THRESHOLD", 70),
            scoring_model=os.getenv("OPENAI_SCORING_MODEL", "gpt-5.6-luna"),
            writing_model=os.getenv("OPENAI_WRITING_MODEL", "gpt-5.6-terra"),
            fallback_model=os.getenv("OPENAI_FALLBACK_MODEL", "gpt-4.1-mini"),
            hh_user_agent=os.getenv(
                "HH_USER_AGENT", "AI Career Agent/2.0 (career-agent)"
            ),
            hh_access_token=os.getenv("HH_ACCESS_TOKEN"),
            hh_client_id=os.getenv("HH_CLIENT_ID"),
            hh_client_secret=os.getenv("HH_CLIENT_SECRET"),
            gmail_enabled=_bool_env("GMAIL_ENABLED", False),
            gmail_client_id=os.getenv("GMAIL_CLIENT_ID"),
            gmail_client_secret=os.getenv("GMAIL_CLIENT_SECRET"),
            gmail_refresh_token=os.getenv("GMAIL_REFRESH_TOKEN"),
            gmail_label=os.getenv("GMAIL_LABEL", "LinkedIn-Jobs"),
            telegram_sources_enabled=_bool_env("TELEGRAM_SOURCES_ENABLED", False),
            telegram_api_id=int(api_id) if api_id else None,
            telegram_api_hash=os.getenv("TELEGRAM_API_HASH"),
            telegram_session=os.getenv("TELEGRAM_SESSION"),
        )

    def optional_source_warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.gmail_enabled and not all(
            [self.gmail_client_id, self.gmail_client_secret, self.gmail_refresh_token]
        ):
            warnings.append("Gmail включён, но OAuth-переменные заданы не полностью")
        if self.telegram_sources_enabled and not all(
            [self.telegram_api_id, self.telegram_api_hash, self.telegram_session]
        ):
            warnings.append(
                "Telegram-источники включены, но MTProto-переменные неполны"
            )
        return warnings
