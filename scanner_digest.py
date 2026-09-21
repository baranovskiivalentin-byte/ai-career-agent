from __future__ import annotations

import html
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from config import Settings
from database import Vacancy
from vacancy_manager import VacancyRepository


def scanner_card_text(vacancy: Vacancy, tz) -> str:
    level = "PRIMARY" if vacancy.track == "scanner_primary" else "SECONDARY"
    first_seen = (
        vacancy.published_at.astimezone(tz).strftime("%d.%m.%Y %H:%M")
        if vacancy.published_at
        else "не указано"
    )
    return (
        f"<b>{html.escape(vacancy.title)}</b>\n"
        f"{html.escape(vacancy.company)}\n"
        f"Приоритет: {level}\n"
        f"Впервые найдена: {first_seen}"
    )


async def send_scanner_today(
    application: Application,
    repository: VacancyRepository,
    settings: Settings,
    chat_id: int,
) -> int:
    today = datetime.now(settings.timezone).date()
    vacancies = repository.get_scanner_for_date(today, settings.timezone)
    if not vacancies:
        await application.bot.send_message(
            chat_id=chat_id,
            text=f"За {today:%d.%m.%Y} Scanner новых активных вакансий не нашёл.",
        )
        return 0

    await application.bot.send_message(
        chat_id=chat_id,
        text=f"Новые вакансии Scanner за {today:%d.%m.%Y}: {len(vacancies)}",
    )
    for vacancy in vacancies:
        keyboard = (
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("Открыть вакансию", url=vacancy.url)]]
            )
            if vacancy.url
            else None
        )
        await application.bot.send_message(
            chat_id=chat_id,
            text=scanner_card_text(vacancy, settings.timezone),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    return len(vacancies)
