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

from ai_handler import analyze_vacancy, ask_ai, configure_ai, generate_cover_letter
from config import (
    TELEGRAM_WEB_CHANNEL_EXPANSION_2026_07_28,
    TELEGRAM_WEB_CHANNEL_EXPANSION_2026_08_31,
    Settings,
)
from dashboard_server import start_dashboard_server
from database import Database
from digest import DigestItem, card_keyboard, card_text, send_digest
from gmail_source import GmailJobAlertsSource
from habr_source import HabrCareerSource
from monitor import VacancyMonitor
from public_job_sources import HimalayasSource, JobicySource
from ranking import VacancyRanker
from scanner_digest import send_scanner_recommendations, send_scanner_today
from telegram_messages import reply_text_safely
from telegram_source import TelegramChannelSource
from telegram_web_source import TelegramWebSource
from vacancy_manager import (
    VacancyRepository,
    configure_legacy_repository,
    get_last_vacancies,
    get_stats,
    save_vacancy,
)
from weekly_hh_backfill import CURSOR_NAME, run_weekly_hh_backfill

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
LOGGER = logging.getLogger(__name__)

ANALYZE_BUTTON = "📊 Анализ вакансии"
COVER_BUTTON = "📝 Сопроводительное"
LIST_BUTTON = "📂 Последние вакансии"
PROFILE_BUTTON = "👤 Профиль"
SEND_HH_DIGEST_BUTTON = "📬 Прислать вакансии HeadHunter"
SEND_SCANNER_BUTTON = "🔎 Прислать новые вакансии Scanner"
SCANNER_RECOMMENDATIONS_BUTTON = "🎯 Рекомендованные Scanner"
LEGACY_SEND_DIGEST_BUTTON = "📬 Прислать собранные вакансии"
TELEGRAM_WEB_EXPANSION_CURSOR = "telegram_web_channels_2026_07_28_seeded"
TELEGRAM_WEB_EXPANSION_2026_08_31_CURSOR = (
    "telegram_web_channels_2026_08_31_seeded"
)
TELEGRAM_WEB_EXPANSION_BACKFILL_CURSOR = (
    "telegram_web_channels_2026_07_28_backfilled"
)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [ANALYZE_BUTTON, COVER_BUTTON],
        [LIST_BUTTON, PROFILE_BUTTON],
        [SEND_HH_DIGEST_BUTTON],
        [SEND_SCANNER_BUTTON],
        [SCANNER_RECOMMENDATIONS_BUTTON],
    ],
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
    repository: VacancyRepository = services(context)["repository"]
    chat_id = settings.telegram_chat_id or db.get_cursor("digest_chat_id")
    telegram_web_sources = repository.list_sources()
    warnings = settings.optional_source_warnings()
    text = [
        "✅ Бот работает",
        f"База данных: {settings.database_url.split(':', 1)[0]}",
        f"Вакансий в базе: {get_stats()}",
        f"Дайджест-чат: {'настроен' if chat_id else 'не настроен — выполните /start'}",
        f"Теневой режим: {'включён' if settings.shadow_mode else 'выключен'}",
        f"HH OAuth: {'настроен' if settings.hh_access_token or (settings.hh_client_id and settings.hh_client_secret) else 'не настроен'}",
        f"Gmail/LinkedIn: {'включён' if settings.gmail_enabled else 'выключен'}",
        "HH-рассылки: отключены (нет полного описания вакансии)",
        f"Habr Карьера: {'включена' if settings.habr_jobs_enabled else 'выключена'}",
        f"Международные вакансии: {'включены' if settings.international_jobs_enabled else 'выключены'} (Himalayas, Jobicy)",
        f"Публичные Telegram-каналы: {'включены' if settings.telegram_web_enabled else 'выключены'} ({len(telegram_web_sources)})",
        f"Telegram MTProto (резерв): {'включён' if settings.telegram_sources_enabled else 'выключен'}",
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
        sources={"hh", "hh_email"},
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
        if text in {SEND_HH_DIGEST_BUTTON, LEGACY_SEND_DIGEST_BUTTON}:
            await digest_command(update, context)
            return
        if text == SEND_SCANNER_BUTTON:
            count = await send_scanner_today(
                context.application,
                services(context)["repository"],
                services(context)["settings"],
                update.effective_chat.id,
            )
            LOGGER.info("Ручная выдача Scanner: %s вакансий", count)
            return
        if text == SCANNER_RECOMMENDATIONS_BUTTON:
            count = await send_scanner_recommendations(
                context.application,
                services(context)["repository"],
                services(context)["settings"],
                update.effective_chat.id,
            )
            LOGGER.info("Рекомендации Scanner: %s вакансий", count)
            return
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


async def scanner_scoring_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    monitor: VacancyMonitor = services(context)["monitor"]
    await monitor.score_pending_scanner(limit=10)


async def telegram_web_expansion_backfill_job(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    data = services(context)
    settings: Settings = data["settings"]
    database: Database = data["database"]
    if (
        not settings.telegram_web_enabled
        or database.get_cursor(TELEGRAM_WEB_EXPANSION_BACKFILL_CURSOR)
    ):
        return

    source: TelegramWebSource = data["telegram_web_source"]
    monitor: VacancyMonitor = data["monitor"]
    try:
        candidates = await source.fetch_recent(
            channels=TELEGRAM_WEB_CHANNEL_EXPANSION_2026_07_28,
            lookback_hours=168,
            max_posts=20,
            max_pages=12,
            raise_on_error=True,
        )
    except Exception:
        LOGGER.exception("Недельный backfill новых Telegram-каналов не выполнен")
        return

    created = 0
    errors = 0
    for candidate in candidates:
        try:
            created += int(await monitor.ingest_one(candidate))
        except Exception:
            errors += 1
            LOGGER.exception(
                "Не удалось сохранить вакансию недельного backfill %s",
                candidate.external_id,
            )
    if errors == 0:
        database.set_cursor(TELEGRAM_WEB_EXPANSION_BACKFILL_CURSOR, "1")
    LOGGER.info(
        "Недельный backfill новых Telegram-каналов: найдено=%s, добавлено=%s, ошибок=%s",
        len(candidates),
        created,
        errors,
    )


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


async def rescore_after_balance_recovery_job(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """One bounded recovery pass after a deployment with restored API credits."""
    data = context.application.bot_data
    database: Database = data["database"]
    cursor_name = "openai_balance_recovery_rescore_v1"
    if database.get_cursor(cursor_name):
        return
    monitor: VacancyMonitor = data["monitor"]
    stats = await monitor.rescore_recent_fallbacks(limit=20)
    if not getattr(monitor.ranker, "quota_exhausted", False):
        database.set_cursor(cursor_name, str(stats["rescored"]))


async def weekly_hh_backfill_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.application.bot_data
    monitor: VacancyMonitor = data["monitor"]
    if not monitor._hh_api_configured():
        LOGGER.warning("Недельная перепроверка HH пропущена: API не настроен")
        return
    database: Database = data["database"]
    try:
        state = await run_weekly_hh_backfill(
            database,
            data["repository"],
            VacancyRanker(data["settings"], data["profile"]),
            monitor.hh,
        )
    except Exception:
        LOGGER.exception("Недельная перепроверка HH завершилась ошибкой")
        return
    if state.get("notified"):
        return
    chat_id = data["settings"].telegram_chat_id or database.get_cursor("digest_chat_id")
    if not chat_id:
        return
    await context.application.bot.send_message(
        chat_id=int(chat_id),
        text=(
            "🔎 Перепроверка HeadHunter за 7 дней: "
            f"собрано {state['saved']}, AI-оценено {state['ai_scored']}. "
            f"Ошибок: {state['errors']}. "
            "Сильные совпадения отправляю ниже, остальные доступны по кнопке HH."
        ),
    )
    rows = data["repository"].get_ranked(70, hours=168, sources={"hh"})
    for vacancy, score in [row for row in rows if row[1].model == "gpt-4.1-mini"][:5]:
        await context.application.bot.send_message(
            chat_id=int(chat_id),
            text=card_text(DigestItem(vacancy, score)),
            parse_mode="HTML",
            reply_markup=card_keyboard(vacancy),
            disable_web_page_preview=True,
        )
    state["notified"] = True
    database.set_cursor(CURSOR_NAME, json.dumps(state))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Необработанная ошибка Telegram", exc_info=context.error)


async def post_init(application: Application) -> None:
    data = application.bot_data
    settings: Settings = data["settings"]
    database: Database = data["database"]
    backfill_pending = (
        settings.telegram_web_enabled
        and not database.get_cursor(TELEGRAM_WEB_EXPANSION_BACKFILL_CURSOR)
    )
    weekly_state = json.loads(database.get_cursor(CURSOR_NAME) or "{}")
    weekly_pending = not weekly_state.get("done")
    application.job_queue.run_repeating(
        collect_job,
        interval=settings.hh_poll_interval_seconds,
        first=settings.hh_poll_interval_seconds if backfill_pending or weekly_pending else 5,
        name="vacancy-monitor",
    )
    application.job_queue.run_repeating(
        scanner_scoring_job,
        interval=300,
        first=settings.hh_poll_interval_seconds if weekly_pending else 15,
        name="scanner-scoring",
    )
    if backfill_pending:
        application.job_queue.run_once(
            telegram_web_expansion_backfill_job,
            when=settings.hh_poll_interval_seconds if weekly_pending else 1,
            name="telegram-web-expansion-backfill",
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
    if weekly_pending:
        application.job_queue.run_once(
            weekly_hh_backfill_job,
            when=15,
            name="hh-weekly-recheck",
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
    if settings.telegram_web_enabled and not database.get_cursor(
        "telegram_web_defaults_seeded"
    ):
        for channel in settings.telegram_web_channels:
            repository.add_source(channel)
        database.set_cursor("telegram_web_defaults_seeded", "1")
    if settings.telegram_web_enabled and not database.get_cursor(
        TELEGRAM_WEB_EXPANSION_CURSOR
    ):
        for channel in TELEGRAM_WEB_CHANNEL_EXPANSION_2026_07_28:
            repository.add_source(channel)
        database.set_cursor(TELEGRAM_WEB_EXPANSION_CURSOR, "1")
    if settings.telegram_web_enabled and not database.get_cursor(
        TELEGRAM_WEB_EXPANSION_2026_08_31_CURSOR
    ):
        for channel in TELEGRAM_WEB_CHANNEL_EXPANSION_2026_08_31:
            repository.add_source(channel)
        database.set_cursor(TELEGRAM_WEB_EXPANSION_2026_08_31_CURSOR, "1")
    configure_legacy_repository(repository)
    configure_ai(settings)
    profile = load_profile()
    ranker = VacancyRanker(settings, profile)
    optional_fetchers = []
    if settings.gmail_enabled:
        optional_fetchers.append(GmailJobAlertsSource(settings).fetch_recent)
    habr_source = HabrCareerSource(
        settings,
        max_vacancies=settings.public_jobs_max_results,
        max_card_attempts=min(settings.public_jobs_max_results * 3, 30),
        ttl_seconds=settings.public_jobs_poll_interval_seconds,
    )
    if settings.habr_jobs_enabled:
        optional_fetchers.append(habr_source.fetch_recent)
    himalayas_source = HimalayasSource(
        limit=settings.public_jobs_max_results,
        cache_ttl_seconds=settings.public_jobs_poll_interval_seconds,
    )
    jobicy_source = JobicySource(
        count=settings.public_jobs_max_results,
        cache_ttl_seconds=settings.public_jobs_poll_interval_seconds,
    )
    if settings.international_jobs_enabled:
        optional_fetchers.extend(
            [himalayas_source.fetch_recent, jobicy_source.fetch_recent]
        )
    telegram_web_source = TelegramWebSource(settings, repository)
    if settings.telegram_web_enabled:
        optional_fetchers.append(telegram_web_source.fetch_recent)
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
            "telegram_web_source": telegram_web_source,
            "habr_source": habr_source,
            "himalayas_source": himalayas_source,
            "jobicy_source": jobicy_source,
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
    settings: Settings = app.bot_data["settings"]
    dashboard_server = start_dashboard_server(
        app.bot_data["database"],
        token=settings.dashboard_token,
        port=settings.dashboard_port,
        tz=settings.timezone,
        scoring_threshold=settings.scoring_threshold,
    )
    LOGGER.info("AI Career Agent запущен 🚀")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        if dashboard_server:
            dashboard_server.shutdown()
            dashboard_server.server_close()
