from __future__ import annotations


TELEGRAM_SAFE_MESSAGE_LENGTH = 4000


def split_telegram_text(
    text: str,
    limit: int = TELEGRAM_SAFE_MESSAGE_LENGTH,
) -> list[str]:
    """Split text into Telegram-safe chunks without losing content."""
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    if not text:
        return [""]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = _find_split_position(remaining, limit)
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def _find_split_position(text: str, limit: int) -> int:
    minimum_preferred_cut = limit // 2
    for separator in ("\n\n", "\n", " "):
        position = text.rfind(separator, minimum_preferred_cut, limit + 1)
        if position != -1:
            return position + len(separator)
    return limit


async def reply_text_safely(message, text: str) -> None:
    for chunk in split_telegram_text(text):
        await message.reply_text(chunk)
