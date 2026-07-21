from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from config import Settings
from database import Vacancy, VacancyScore
from vacancy_manager import VacancyRepository


@dataclass(slots=True)
class DigestItem:
    vacancy: Vacancy
    score: VacancyScore


def select_digest_items(
    rows: list[tuple[Vacancy, VacancyScore]],
    *,
    per_track: int = 5,
    backfill_score: int = 75,
) -> list[DigestItem]:
    selected: list[tuple[Vacancy, VacancyScore]] = []
    used_ids: set[int] = set()
    for track in ("senior_it", "enterprise_epc"):
        track_rows = [row for row in rows if row[1].track == track]
        for row in track_rows[:per_track]:
            if row[0].id not in used_ids:
                selected.append(row)
                used_ids.add(row[0].id)

    missing = per_track * 2 - len(selected)
    if missing > 0:
        backfill = [
            row
            for row in rows
            if row[0].id not in used_ids and row[1].total >= backfill_score
        ]
        for row in backfill[:missing]:
            selected.append(row)
            used_ids.add(row[0].id)
    return [DigestItem(vacancy=row[0], score=row[1]) for row in selected]


def salary_text(vacancy: Vacancy) -> str:
    currency = vacancy.currency or "RUR"
    if vacancy.salary_from and vacancy.salary_to:
        return f"{vacancy.salary_from:,}–{vacancy.salary_to:,} {currency}".replace(
            ",", " "
        )
    if vacancy.salary_from:
        return f"от {vacancy.salary_from:,} {currency}".replace(",", " ")
    if vacancy.salary_to:
        return f"до {vacancy.salary_to:,} {currency}".replace(",", " ")
    return "не указана"


def card_text(item: DigestItem) -> str:
    vacancy = item.vacancy
    score = item.score
    track_name = (
        "Senior IT / Delivery" if score.track == "senior_it" else "Enterprise / EPC"
    )
    reasons = "\n".join(f"• {html.escape(value)}" for value in score.reasons[:3])
    risks = (
        "\n".join(f"• {html.escape(value)}" for value in score.risks[:3])
        or "• Критичных рисков не выявлено"
    )
    return (
        f"<b>{html.escape(vacancy.title)}</b>\n"
        f"{html.escape(vacancy.company)}\n"
        f"🎯 {score.total}/100 · {track_name}\n"
        f"💰 {html.escape(salary_text(vacancy))}\n"
        f"🏠 Удалённо · источник: {html.escape(vacancy.source)}\n\n"
        f"<b>Почему подходит</b>\n{reasons}\n\n"
        f"<b>Риски</b>\n{risks}"
    )


def card_keyboard(vacancy: Vacancy) -> InlineKeyboardMarkup:
    first_row = []
    if vacancy.url:
        first_row.append(InlineKeyboardButton("Открыть", url=vacancy.url))
    first_row.append(
        InlineKeyboardButton("Сохранить", callback_data=f"save:{vacancy.id}")
    )
    return InlineKeyboardMarkup(
        [
            first_row,
            [
                InlineKeyboardButton(
                    "Не подходит", callback_data=f"reject:{vacancy.id}"
                ),
                InlineKeyboardButton(
                    "Сопроводительное", callback_data=f"cover:{vacancy.id}"
                ),
            ],
        ]
    )


async def send_digest(
    application: Application,
    repository: VacancyRepository,
    settings: Settings,
    chat_id: int,
    *,
    force: bool = False,
) -> int:
    today = datetime.now(settings.timezone).date()
    if not force and repository.digest_exists(today, chat_id):
        return 0
    minimum_score = 0 if force else settings.scoring_threshold
    rows = repository.get_ranked(minimum_score)
    items = select_digest_items(rows)
    if settings.shadow_mode and not force:
        repository.save_digest(
            today,
            chat_id,
            [item.vacancy.id for item in items],
            sent=False,
            shadow=True,
        )
        return len(items)
    if not items:
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "За последние дни не найдено собранных вакансий."
                if force
                else f"За последние дни не найдено вакансий с оценкой от {settings.scoring_threshold}."
            ),
        )
        if not force:
            repository.save_digest(today, chat_id, [], sent=True, shadow=False)
        return 0
    await application.bot.send_message(
        chat_id=chat_id,
        text=f"Подборка на {today:%d.%m.%Y}: {len(items)} релевантных вакансий",
    )
    for item in items:
        await application.bot.send_message(
            chat_id=chat_id,
            text=card_text(item),
            parse_mode=ParseMode.HTML,
            reply_markup=card_keyboard(item.vacancy),
            disable_web_page_preview=True,
        )
    if not force:
        repository.save_digest(
            today,
            chat_id,
            [item.vacancy.id for item in items],
            sent=True,
            shadow=False,
        )
    return len(items)
