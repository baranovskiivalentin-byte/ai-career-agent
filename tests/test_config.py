from config import (
    DEFAULT_TELEGRAM_WEB_CHANNELS,
    TELEGRAM_WEB_CHANNEL_EXPANSION_2026_07_28,
)


def test_default_telegram_channels_include_expansion():
    assert set(TELEGRAM_WEB_CHANNEL_EXPANSION_2026_07_28).issubset(
        DEFAULT_TELEGRAM_WEB_CHANNELS
    )
