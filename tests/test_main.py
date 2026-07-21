import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from main import MAIN_KEYBOARD, SEND_DIGEST_BUTTON, text_router


def test_main_keyboard_contains_manual_digest_button():
    assert any(
        button.text == SEND_DIGEST_BUTTON
        for row in MAIN_KEYBOARD.keyboard
        for button in row
    )


def test_manual_digest_button_routes_to_forced_digest():
    message = SimpleNamespace(text=SEND_DIGEST_BUTTON)
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"profile": {}}),
        user_data={},
    )

    with patch("main.digest_command", new=AsyncMock()) as digest_command:
        asyncio.run(text_router(update, context))

    digest_command.assert_awaited_once_with(update, context)
