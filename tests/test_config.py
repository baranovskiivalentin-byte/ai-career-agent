from config import (
    DEFAULT_TELEGRAM_WEB_CHANNELS,
    TELEGRAM_WEB_CHANNEL_EXPANSION_2026_07_28,
    TELEGRAM_WEB_CHANNEL_EXPANSION_2026_08_31,
    Settings,
)


def test_default_telegram_channels_include_expansion():
    assert set(TELEGRAM_WEB_CHANNEL_EXPANSION_2026_07_28).issubset(
        DEFAULT_TELEGRAM_WEB_CHANNELS
    )


def test_default_telegram_channels_include_geekjob_sources():
    assert set(TELEGRAM_WEB_CHANNEL_EXPANSION_2026_08_31).issubset(
        DEFAULT_TELEGRAM_WEB_CHANNELS
    )


def test_public_job_sources_are_enabled_by_default(monkeypatch):
    monkeypatch.delenv("INTERNATIONAL_JOBS_ENABLED", raising=False)
    monkeypatch.delenv("HABR_JOBS_ENABLED", raising=False)
    monkeypatch.delenv("PUBLIC_JOBS_POLL_INTERVAL_SECONDS", raising=False)
    settings = Settings.from_env(require_core=False)
    assert settings.international_jobs_enabled is True
    assert settings.habr_jobs_enabled is True
    assert settings.public_jobs_poll_interval_seconds == 3600


def test_vacancy_monitor_runs_every_four_hours_by_default(monkeypatch):
    monkeypatch.delenv("HH_POLL_INTERVAL_SECONDS", raising=False)
    settings = Settings.from_env(require_core=False)
    assert settings.hh_poll_interval_seconds == 4 * 60 * 60
