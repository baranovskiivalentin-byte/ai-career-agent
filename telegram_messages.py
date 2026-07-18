from __future__ import annotations

import re


TELEGRAM_SAFE_MESSAGE_LENGTH = 4000
TELEGRAM_SECTION_DIVIDER = "━━━━━━━━━━━━━━━━"
HEADING_EMOJIS = (
    (("СООТВЕТСТВ", "ОЦЕНК"), "📊"),
    (("СИЛЬН", "ПРЕИМУЩ"), "✅"),
    (("РИСК", "ПРОБЕЛ", "СЛАБ"), "⚠️"),
    (("РЕКОМЕНД", "ДЕЙСТВ"), "🎯"),
    (("ИТОГ", "ВЫВОД"), "🏁"),
)


def format_telegram_text(text: str) -> str:
    """Convert common Markdown decoration to professional Telegram plain text."""
    formatted_lines: list[str] = []
    for raw_line in text.strip().splitlines():
        stripped = raw_line.strip()
        if re.fullmatch(r"[-_*]{3,}", stripped):
            formatted_lines.append(TELEGRAM_SECTION_DIVIDER)
            continue

        is_heading = bool(
            re.match(r"^\s*#{1,6}\s+", raw_line)
            or re.fullmatch(r"\s*\*\*.+\*\*\s*", raw_line)
            or (len(stripped) <= 80 and stripped == stripped.upper())
        )
        line = re.sub(r"^\s*#{1,6}\s*", "", raw_line)
        line = re.sub(r"^\s*[-*]\s+", "• ", line)
        line = (
            line.replace("**", "")
            .replace("__", "")
            .replace("*", "")
            .replace("`", "")
        )
        if is_heading:
            line = _decorate_known_heading(line)
        formatted_lines.append(line.rstrip())

    return "\n".join(formatted_lines).strip()


def _decorate_known_heading(line: str) -> str:
    stripped = line.strip()
    upper = stripped.upper()
    for keywords, emoji in HEADING_EMOJIS:
        if any(keyword in upper for keyword in keywords):
            if stripped.startswith(emoji):
                return line
            return f"{emoji} {stripped}"
    return line


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
