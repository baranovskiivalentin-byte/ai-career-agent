import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from main import (
    ANALYZE_BUTTON,
    COVER_BUTTON,
    LIST_BUTTON,
    MAIN_KEYBOARD,
    PROFILE_BUTTON,
    SCANNER_RECOMMENDATIONS_BUTTON,
    SEND_HH_DIGEST_BUTTON,
    SEND_SCANNER_BUTTON,
    text_router,
)


def test_main_keyboard_keeps_existing_buttons_and_adds_scanner_actions():
    labels = {button.text for row in MAIN_KEYBOARD.keyboard for button in row}
    assert labels == {
        ANALYZE_BUTTON,
        COVER_BUTTON,
        LIST_BUTTON,
        PROFILE_BUTTON,
        SEND_HH_DIGEST_BUTTON,
        SEND_SCANNER_BUTTON,
        SCANNER_RECOMMENDATIONS_BUTTON,
    }


def test_manual_digest_button_routes_to_forced_digest():
    message = SimpleNamespace(text=SEND_HH_DIGEST_BUTTON)
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"profile": {}}),
        user_data={},
    )

    with patch("main.digest_command", new=AsyncMock()) as digest_command:
        asyncio.run(text_router(update, context))

    digest_command.assert_awaited_once_with(update, context)
