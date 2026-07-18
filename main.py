from __future__ import annotations

import json
import logging
from datetime import time
from pathlib import Path

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ai_handler import ask_ai, analyze_vacancy, configure_ai, generate_cover_letter
from config import Settings
from database import Database
from digest import send_digest
from gmail_source import GmailLinkedInSource
from monitor import VacancyMonitor
from ranking import VacancyRanker
from telegram_source import TelegramChannelSource
from telegram_messages import reply_text_safely
from vacancy_manager import (
    VacancyRepository,
    configure_legacy_repository,
    get_last_vacancies,
    get_stats,
    save_vacancy,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger(__name__)

ANALYZE_BUTTON = "📊 Анализ вакансии"
COVER_BUTTON = "📝 Сопроводительное"
LIST_BUTTON = "📂 Последние вакансии"
PROFILE_BUTTON = "👤 Профиль"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[ANALYZE_BUTTON, COVER_BUTTON], [LIST_BUTTON, PROFILE_BUTTON]],
    resize_keyboard=True,
)


def load_profile() -> dict:
    return json.loads(Path("profile.json").read_text(encoding="utf-8"))


def services(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    db: Database = services(context)["database"]
    db.set_cursor("digest_chat_id", str(chat_id))
    await update.effective_message.reply_text(
        "🚀 AI Career Agent запущен. Этот чат будет получать ежедневный дайджест.",
        reply_markup=MAIN_KEYBOARD,
    )


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = services(context)["settings"]
    db: Database = services(context)["database"]
    chat_id = settings.telegram_chat_id or db.get_cursor("digest_chat_id")
    warnings = settings.optional_source_warnings()
    text = [
        "✅ Бот работает",
        f"База данных: {settings.database_url.split(':', 1)[0]}",
        f"Вакансий в базе: {get_stats()}",
        f"Дайджест-чат: {'настроен' if chat_id else 'не настроен — выполните /start'}",
        f"Теневой режим: {'включён' if settings.shadow_mode else 'выключен'}",
        f"HH OAuth: {'настроен' if settings.hh_access_token or (settings.hh_client_id and settings.hh_client_secret) else 'не настроен'}",
        f"Gmail: {'включён' if settings.gmail_enabled else 'выключен'}",
        f"Telegram-каналы: {'включены' if settings.telegram_sources_enabled else 'выключены'}",
    ]
    text.extend(f"⚠️ {warning}" for warning in warnings)
    await update.effective_message.reply_text("\n".join(text))


async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository: VacancyRepository = services(context)["repository"]
    settings: Settings = services(context)["settings"]
    count = await send_digest(
        context.application,
        repository,
        settings,
        update.effective_chat.id,
        force=True,
    )
    if count:
        LOGGER.info("Ручной дайджест: %s вакансий", count)


async def sources_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository: VacancyRepository = services(context)["repository"]
    rows = repository.list_sources()
    await update.effective_message.reply_text(
        "Telegram-источники:\n"
        + ("\n".join(f"• {row}" for row in rows) if rows else "список пуст")
    )


async def source_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Использование: /source_add @channel")
        return
    identifier = context.args[0].strip().lower()
    repository: VacancyRepository = services(context)["repository"]
    created = repository.add_source(identifier)
    await update.effective_message.reply_text(
        "Источник добавлен." if created else "Источник уже был добавлен и включён."
    )


async def source_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text(
            "Использование: /source_remove @channel"
        )
        return
    repository: VacancyRepository = services(context)["repository"]
    removed = repository.remove_source(context.args[0].strip().lower())
    await update.effective_message.reply_text(
        "Источник отключён." if removed else "Активный источник не найден."
    )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    profile = services(context)["profile"]
    try:
        if context.user_data.pop("waiting_for_vacancy", False):
            answer = await analyze_vacancy(text, profile)
            save_vacancy(text)
            await reply_text_safely(update.effective_message, answer)
            return
        if context.user_data.pop("waiting_for_cover", False):
            answer = await generate_cover_letter(text, profile)
            await reply_text_safely(update.effective_message, answer)
            return
        if text == ANALYZE_BUTTON:
            context.user_data["waiting_for_vacancy"] = True
            await update.effective_message.reply_text("Пришлите описание вакансии 📄")
            return
        if text == COVER_BUTTON:
            context.user_data["waiting_for_cover"] = True
            await update.effective_message.reply_text("Пришлите вакансию для письма ✉️")
            return
        if text == LIST_BUTTON:
            vacancies = get_last_vacancies()
            message = (
                "\n\n".join(f"{row['date']}\n{row['text'][:300]}" for row in vacancies)
                if vacancies
                else "Пока пусто"
            )
            await reply_text_safely(update.effective_message, message)
            return
        if text == PROFILE_BUTTON:
            await update.effective_message.reply_text(
                f"{profile['name']}\n"
                f"Целевая зарплата: {profile['target_salary_min']}–{profile['target_salary_max']} ₽\n"
                "Формат: только удалённо"
            )
            return
        await reply_text_safely(update.effective_message, await ask_ai(text))
    except Exception:
        LOGGER.exception("Ошибка обработки сообщения")
        await update.effective_message.reply_text(
            "Не удалось выполнить запрос. Ошибка записана в журнал, попробуйте позже."
        )


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, raw_id = query.data.split(":", 1)
    vacancy_id = int(raw_id)
    repository: VacancyRepository = services(context)["repository"]
    vacancy = repository.get_vacancy(vacancy_id)
    if not vacancy:
        await query.message.reply_text("Вакансия больше не найдена в базе.")
        return
    repository.record_action(vacancy_id, query.message.chat_id, action)
    if action == "save":
        await query.message.reply_text("Сохранено ✅")
    elif action == "reject":
        await query.message.reply_text("Учту как неподходящую вакансию.")
    elif action == "cover":
        profile = services(context)["profile"]
        try:
            letter = await generate_cover_letter(vacancy.description, profile)
            repository.record_action(
                vacancy_id, query.message.chat_id, "cover_generated", letter
            )
            await reply_text_safely(query.message, letter)
        except Exception:
            LOGGER.exception("Не удалось создать письмо")
            await query.message.reply_text(
                "Не удалось создать письмо. Попробуйте позже."
            )


async def collect_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    monitor: VacancyMonitor = services(context)["monitor"]
    await monitor.collect()


async def digest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = services(context)
    settings: Settings = data["settings"]
    db: Database = data["database"]
    raw_chat_id = settings.telegram_chat_id or db.get_cursor("digest_chat_id")
    if not raw_chat_id:
        LOGGER.warning("Дайджест пропущен: chat_id не настроен")
        return
    await send_digest(
        context.application,
        data["repository"],
        settings,
        int(raw_chat_id),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Необработанная ошибка Telegram", exc_info=context.error)


async def post_init(application: Application) -> None:
    data = application.bot_data
    settings: Settings = data["settings"]
    application.job_queue.run_repeating(
        collect_job,
        interval=settings.hh_poll_interval_seconds,
        first=5,
        name="vacancy-monitor",
    )
    application.job_queue.run_daily(
        digest_job,
        time=time(
            hour=settings.digest_hour,
            minute=settings.digest_minute,
            tzinfo=settings.timezone,
        ),
        name="daily-digest",
    )
    telegram_source: TelegramChannelSource | None = data.get("telegram_source")
    if telegram_source and settings.telegram_sources_enabled:
        await telegram_source.start(data["monitor"].ingest_one)
    LOGGER.info(
        "Планировщик запущен; дайджест %02d:%02d",
        settings.digest_hour,
        settings.digest_minute,
    )


async def post_shutdown(application: Application) -> None:
    source: TelegramChannelSource | None = application.bot_data.get("telegram_source")
    if source:
        await source.stop()


def build_application() -> Application:
    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.create_schema()
    imported = database.migrate_legacy_vacancies()
    if imported:
        LOGGER.info("Импортировано старых вакансий: %s", imported)
    repository = VacancyRepository(database)
    configure_legacy_repository(repository)
    configure_ai(settings)
    profile = load_profile()
    ranker = VacancyRanker(settings, profile)
    optional_fetchers = []
    if settings.gmail_enabled:
        optional_fetchers.append(GmailLinkedInSource(settings).fetch_recent)
    monitor = VacancyMonitor(settings, repository, ranker, optional_fetchers)
    telegram_source = TelegramChannelSource(settings, repository)

    application = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.bot_data.update(
        {
            "settings": settings,
            "database": database,
            "repository": repository,
            "profile": profile,
            "ranker": ranker,
            "monitor": monitor,
            "telegram_source": telegram_source,
        }
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("health", health))
    application.add_handler(CommandHandler("digest", digest_command))
    application.add_handler(CommandHandler("sources", sources_command))
    application.add_handler(CommandHandler("source_add", source_add))
    application.add_handler(CommandHandler("source_remove", source_remove))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_router)
    )
    application.add_error_handler(error_handler)
    return application


if __name__ == "__main__":
    app = build_application()
    LOGGER.info("AI Career Agent запущен 🚀")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
