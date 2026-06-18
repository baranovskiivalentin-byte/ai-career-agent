from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import os
import json
from dotenv import load_dotenv

from ai_handler import ask_ai, analyze_vacancy, generate_cover_letter
from vacancy_manager import save_vacancy, get_stats, get_last_vacancies

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")


# =========================
# KEYBOARD
# =========================

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📊 Анализ вакансии", "📝 Сопроводительное"],
        ["📂 Последние вакансии", "👤 Профиль"],
    ],
    resize_keyboard=True
)


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🚀 AI Career Agent\n\nВыбери действие:",
        reply_markup=MAIN_KEYBOARD
    )


# =========================
# MENU ROUTER (КНОПКИ)
# =========================

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text

    # ANALYZE
    if text == "📊 Анализ вакансии":
        context.user_data["waiting_for_vacancy"] = True
        await update.message.reply_text("Пришли описание вакансии 📄")
        return

    # COVER
    if text == "📝 Сопроводительное":
        context.user_data["waiting_for_cover"] = True
        await update.message.reply_text("Пришли вакансию для письма ✉️")
        return

    # LIST
    if text == "📂 Последние вакансии":
        vacancies = get_last_vacancies()

        if not vacancies:
            await update.message.reply_text("Пока пусто")
            return

        msg = "\n\n".join(
            f"{v['date']}\n{v['text'][:100]}..."
            for v in vacancies
        )

        await update.message.reply_text(msg)
        return

    # PROFILE
    if text == "👤 Профиль":
        with open("profile.json", "r", encoding="utf-8") as f:
            profile = json.load(f)

        await update.message.reply_text(
            f"{profile['name']}\n"
            f"{profile['target_salary_min']} - {profile['target_salary_max']}"
        )
        return

    # fallback
    answer = ask_ai(text)
    await update.message.reply_text(answer)


# =========================
# CHAT (ввод вакансий)
# =========================

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_text = update.message.text

    try:

        # VACANCY ANALYSIS
        if context.user_data.get("waiting_for_vacancy"):

            with open("profile.json", "r", encoding="utf-8") as f:
                profile = json.load(f)

            answer = analyze_vacancy(user_text, profile)

            save_vacancy(user_text)

            await update.message.reply_text(answer)

            context.user_data["waiting_for_vacancy"] = False
            return

        # COVER LETTER
        if context.user_data.get("waiting_for_cover"):

            with open("profile.json", "r", encoding="utf-8") as f:
                profile = json.load(f)

            answer = generate_cover_letter(user_text, profile)

            await update.message.reply_text(answer)

            context.user_data["waiting_for_cover"] = False
            return

        # fallback AI chat
        answer = ask_ai(user_text)
        await update.message.reply_text(answer)

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}")


# =========================
# APP
# =========================

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))

app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router)
)

app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, chat)
)

print("AI Career Agent запущен 🚀")

app.run_polling()