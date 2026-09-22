from __future__ import annotations

import html
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from config import Settings
from database import Vacancy, VacancyScore
from vacancy_manager import VacancyRepository


def recommendation_text(score: VacancyScore | None, threshold: int) -> str:
    if score is None:
        return "⏳ Оценка по полному тексту ещё готовится"
    if score.total >= threshold:
        return "🟢 Рекомендую откликнуться"
    if score.total >= 50:
        return "🟡 Можно рассмотреть"
    return "⚪ Не рекомендую"


def scanner_card_text(
    vacancy: Vacancy, score: VacancyScore | None, tz, threshold: int = 70
) -> str:
    level = "PRIMARY" if vacancy.track == "scanner_primary" else "SECONDARY"
    first_seen = (
        vacancy.published_at.astimezone(tz).strftime("%d.%m.%Y %H:%M")
        if vacancy.published_at
        else "не указано"
    )
    lines = [
        (
            f"<b>{html.escape(vacancy.title)}</b>\n"
            f"{html.escape(vacancy.company)}\n"
            f"Приоритет: {level}\n"
            f"Впервые найдена: {first_seen}"
        ),
        recommendation_text(score, threshold),
    ]
    if score is not None:
        lines.append(f"🎯 Соответствие: <b>{score.total}/100</b>")
        if score.reasons:
            lines.append(
                "<b>Почему подходит:</b>\n"
                + "\n".join(f"• {html.escape(item)}" for item in score.reasons[:3])
            )
        if score.risks:
            lines.append(
                "<b>Риски:</b>\n"
                + "\n".join(f"• {html.escape(item)}" for item in score.risks[:3])
            )
    return "\n".join(lines)


async def send_scanner_today(
    application: Application,
    repository: VacancyRepository,
    settings: Settings,
    chat_id: int,
) -> int:
    today = datetime.now(settings.timezone).date()
    rows = repository.get_scanner_for_date_with_scores(today, settings.timezone)
    if not rows:
        await application.bot.send_message(
            chat_id=chat_id,
            text=f"За {today:%d.%m.%Y} Scanner новых активных вакансий не нашёл.",
        )
        return 0

    await application.bot.send_message(
        chat_id=chat_id,
        text=f"Новые вакансии Scanner за {today:%d.%m.%Y}: {len(rows)}",
    )
    for vacancy, score in rows:
        keyboard = (
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("Открыть вакансию", url=vacancy.url)]]
            )
            if vacancy.url
            else None
        )
        await application.bot.send_message(
            chat_id=chat_id,
            text=scanner_card_text(
                vacancy, score, settings.timezone, settings.scoring_threshold
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    return len(rows)


async def send_scanner_recommendations(
    application: Application,
    repository: VacancyRepository,
    settings: Settings,
    chat_id: int,
    limit: int = 20,
) -> int:
    rows = repository.get_scanner_recommendations(minimum_score=50, limit=limit)
    if not rows:
        await application.bot.send_message(
            chat_id=chat_id,
            text="Scanner ещё не подготовил персональные рекомендации.",
        )
        return 0
    await application.bot.send_message(
        chat_id=chat_id,
        text=(
            f"Лучшие активные вакансии Scanner: {len(rows)}\n"
            "Оценка сделана по полному тексту и вашему профилю."
        ),
    )
    for vacancy, score in rows:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Открыть вакансию", url=vacancy.url)]]
        )
        await application.bot.send_message(
            chat_id=chat_id,
            text=scanner_card_text(
                vacancy, score, settings.timezone, settings.scoring_threshold
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    return len(rows)
