from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from config import Settings
from database import Vacancy, VacancyScore
from vacancy_manager import VacancyRepository

LOGGER = logging.getLogger(__name__)

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
    format_label = "Гибрид" if vacancy.work_format == "hybrid" else "Удалённо"
    return (
        f"<b>{html.escape(vacancy.title)}</b>\n"
        f"{html.escape(vacancy.company)}\n"
        f"🎯 {score.total}/100 · {track_name}\n"
        f"💰 {html.escape(salary_text(vacancy))}\n"
        f"🏠 {format_label} · источник: {html.escape(vacancy.source)}\n\n"
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


def select_top_items(
    rows: list[tuple[Vacancy, VacancyScore]], *, limit: int = 5
) -> list[DigestItem]:
    """Keep the database score order for a compact fallback digest."""
    return [DigestItem(vacancy=vacancy, score=score) for vacancy, score in rows[:limit]]


async def send_digest(
    application: Application,
    repository: VacancyRepository,
    settings: Settings,
    chat_id: int,
    *,
    force: bool = False,
    sources: set[str] | None = None,
) -> int:
    today = datetime.now(settings.timezone).date()
    if not force and repository.digest_exists(today, chat_id):
        return 0
    minimum_score = 0 if force else settings.scoring_threshold
    def ranked(score: int):
        return (
            repository.get_ranked(score, sources=sources)
            if sources
            else repository.get_ranked(score)
        )

    rows = ranked(minimum_score)
    items = select_digest_items(rows)
    delivery_mode = "manual" if force else "qualified"
    if not force and not items:
        fallback_threshold = getattr(settings, "scoring_fallback_threshold", 50)
        fallback_rows = ranked(fallback_threshold)
        if fallback_rows:
            items = select_top_items(fallback_rows, limit=5)
            delivery_mode = "preliminary"
        else:
            # Collection has succeeded, but the current scores are low.  A short
            # top list is more useful than a misleading empty daily notification.
            items = select_top_items(ranked(0), limit=5)
            delivery_mode = "review"
    LOGGER.info(
        "Дайджест: mode=%s threshold=%s qualifying=%s delivered=%s force=%s",
        delivery_mode,
        minimum_score,
        len(rows),
        len(items),
        force,
    )
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
        text=_digest_heading(
            today,
            len(items),
            delivery_mode=delivery_mode,
            threshold=settings.scoring_threshold,
        ),
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


def _digest_heading(
    today,
    count: int,
    *,
    delivery_mode: str,
    threshold: int,
) -> str:
    if delivery_mode == "preliminary":
        return (
            f"Предварительная подборка на {today:%d.%m.%Y}: {count} вакансий\n"
            f"Ни одна пока не достигла {threshold}/100 — отправляю лучшие варианты "
            "для твоей проверки."
        )
    if delivery_mode == "review":
        return (
            f"Подборка для ручной проверки на {today:%d.%m.%Y}: {count} вакансий\n"
            "Сильных совпадений пока нет, но эти варианты лучше пустого дайджеста."
        )
    return f"Подборка на {today:%d.%m.%Y}: {count} релевантных вакансий"
