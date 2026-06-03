from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from ai_handler import (
    ask_ai,
    analyze_vacancy,
    generate_cover_letter
)

from vacancy_manager import (
    save_vacancy,
    get_stats,
    get_last_vacancies
)

import os
import json
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")


# =====================================
# START
# =====================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
🚀 AI Career Agent

Команды:

/profile - профиль
/analyze - анализ вакансии
/cover - сопроводительное письмо
/stats - статистика
/list - последние вакансии

Или просто задайте вопрос.
"""

    await update.message.reply_text(text)


# =====================================
# PROFILE
# =====================================

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:

        with open(
            "profile.json",
            "r",
            encoding="utf-8"
        ) as f:

            profile_data = json.load(f)

        text = f"""
👤 {profile_data['name']}

💰 Зарплата:
{profile_data['target_salary_min']} - {profile_data['target_salary_max']}

🎯 Роли:
{chr(10).join(profile_data['target_roles'])}
"""

        await update.message.reply_text(text)

    except Exception as e:

        await update.message.reply_text(
            f"Ошибка профиля: {str(e)}"
        )


# =====================================
# ANALYZE
# =====================================

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["waiting_for_vacancy"] = True

    await update.message.reply_text(
        "Пришли описание вакансии."
    )


# =====================================
# COVER
# =====================================

async def cover(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["waiting_for_cover"] = True

    await update.message.reply_text(
        "Пришли описание вакансии для сопроводительного письма."
    )


# =====================================
# STATS
# =====================================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    count = get_stats()

    await update.message.reply_text(
        f"📊 Всего проанализировано вакансий: {count}"
    )


# =====================================
# LIST
# =====================================

async def list_vacancies(update: Update, context: ContextTypes.DEFAULT_TYPE):

    vacancies = get_last_vacancies()

    if not vacancies:

        await update.message.reply_text(
            "Вакансий пока нет."
        )

        return

    text = "📋 Последние вакансии:\n\n"

    for i, vacancy in enumerate(vacancies, start=1):

        text += (
            f"{i}. {vacancy['date']}\n"
            f"{vacancy['text'][:100]}...\n\n"
        )

    await update.message.reply_text(text)


# =====================================
# CHAT
# =====================================

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_text = update.message.text

    try:

        # Анализ вакансии

        if context.user_data.get("waiting_for_vacancy"):

            with open(
                "profile.json",
                "r",
                encoding="utf-8"
            ) as f:

                profile = json.load(f)

            answer = analyze_vacancy(
                user_text,
                profile
            )

            save_vacancy(user_text)

            await update.message.reply_text(answer)

            context.user_data["waiting_for_vacancy"] = False

            return

        # Сопроводительное письмо

        if context.user_data.get("waiting_for_cover"):

            with open(
                "profile.json",
                "r",
                encoding="utf-8"
            ) as f:

                profile = json.load(f)

            answer = generate_cover_letter(
                user_text,
                profile
            )

            await update.message.reply_text(answer)

            context.user_data["waiting_for_cover"] = False

            return

        # Обычный чат

        answer = ask_ai(user_text)

        await update.message.reply_text(answer)

    except Exception as e:

        await update.message.reply_text(
            f"Ошибка: {str(e)}"
        )


# =====================================
# APP
# =====================================

app = ApplicationBuilder().token(
    TELEGRAM_TOKEN
).build()

app.add_handler(
    CommandHandler("start", start)
)

app.add_handler(
    CommandHandler("profile", profile)
)

app.add_handler(
    CommandHandler("analyze", analyze)
)

app.add_handler(
    CommandHandler("cover", cover)
)

app.add_handler(
    CommandHandler("stats", stats)
)

app.add_handler(
    CommandHandler("list", list_vacancies)
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        chat
    )
)

print("AI Career Agent запущен 🚀")

app.run_polling()