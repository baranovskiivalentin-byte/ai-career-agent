import asyncio

import pytest

from telegram_messages import reply_text_safely, split_telegram_text


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


def test_short_text_remains_one_chunk():
    assert split_telegram_text("Короткий ответ", limit=100) == ["Короткий ответ"]


def test_long_text_is_split_without_losing_content():
    text = ("Первый абзац.\n\n" * 20) + ("Финал " * 30)

    chunks = split_telegram_text(text, limit=100)

    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert "".join(chunks) == text


def test_text_without_boundaries_uses_hard_limit():
    text = "я" * 250

    chunks = split_telegram_text(text, limit=100)

    assert [len(chunk) for chunk in chunks] == [100, 100, 50]
    assert "".join(chunks) == text


def test_invalid_limit_is_rejected():
    with pytest.raises(ValueError):
        split_telegram_text("текст", limit=0)


def test_safe_reply_sends_every_chunk():
    message = FakeMessage()
    text = "а" * 9000

    asyncio.run(reply_text_safely(message, text))

    assert len(message.replies) == 3
    assert all(len(reply) <= 4000 for reply in message.replies)
    assert "".join(message.replies) == text
