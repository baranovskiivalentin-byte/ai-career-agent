from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from config import Settings


SYSTEM_PROMPT = """
Ты персональный AI Career Agent Валентина.

Подтвержденный профиль:
- более 9 лет управления крупными проектами;
- нефтегаз, нефтехимия, Enterprise IT;
- цифровая трансформация и внедрение корпоративных систем;
- управление разработкой, подрядчиками, релизами, интеграциями и миграцией данных;
- управление командами 20–30 человек и бюджетами 150+ млн рублей;
- цель: Senior/AI Project Manager, Delivery Manager или Program Manager;
- приоритет: удаленная работа и доход 350–450 тысяч рублей на руки.

Всегда отвечай на русском, конкретно и структурированно. Не приписывай кандидату
опыт, которого нет в профиле. Не называй себя ИИ-моделью.
""".strip()


class CareerAI:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def _response(self, prompt: str) -> str:
        last_error: Exception | None = None
        for model in dict.fromkeys(
            [self.settings.writing_model, self.settings.fallback_model]
        ):
            try:
                response = await self.client.responses.create(
                    model=model,
                    instructions=SYSTEM_PROMPT,
                    input=prompt,
                )
                return response.output_text
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"OpenAI API недоступен: {last_error}")

    async def ask(
        self, question: str, history: list[dict[str, str]] | None = None
    ) -> str:
        if history:
            history_text = "\n".join(
                f"{row.get('role')}: {row.get('content')}" for row in history[-10:]
            )
            question = f"Контекст:\n{history_text}\n\nВопрос:\n{question}"
        return await self._response(question)

    async def analyze_vacancy(self, vacancy_text: str, profile: dict[str, Any]) -> str:
        return await self._response(
            "Профиль кандидата:\n"
            f"{json.dumps(profile, ensure_ascii=False)}\n\n"
            f"Вакансия:\n{vacancy_text}\n\n"
            "Дай: процент соответствия, сильные стороны, пробелы, риски, "
            "стоит ли откликаться и итоговый вывод."
        )

    async def generate_cover_letter(
        self, vacancy_text: str, profile: dict[str, Any]
    ) -> str:
        return await self._response(
            "Профиль кандидата:\n"
            f"{json.dumps(profile, ensure_ascii=False)}\n\n"
            f"Вакансия:\n{vacancy_text}\n\n"
            "Напиши сильное сопроводительное письмо на 800–1200 знаков. "
            "Без шаблонных фраз и выдуманного опыта; акцент на управлении проектами, "
            "цифровой трансформации и измеримых результатах."
        )


_career_ai: CareerAI | None = None


def configure_ai(settings: Settings) -> CareerAI:
    global _career_ai
    _career_ai = CareerAI(settings)
    return _career_ai


def _get_ai() -> CareerAI:
    if _career_ai is None:
        raise RuntimeError("CareerAI не инициализирован")
    return _career_ai


async def ask_ai(question: str, history: list[dict[str, str]] | None = None) -> str:
    return await _get_ai().ask(question, history)


async def analyze_vacancy(vacancy_text: str, profile: dict[str, Any]) -> str:
    return await _get_ai().analyze_vacancy(vacancy_text, profile)


async def generate_cover_letter(vacancy_text: str, profile: dict[str, Any]) -> str:
    return await _get_ai().generate_cover_letter(vacancy_text, profile)
