from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY not found")

client = OpenAI(
    api_key=OPENAI_API_KEY
)

SYSTEM_PROMPT = """
Ты персональный AI Career Agent Валентина.

Информация о пользователе:

- опыт управления проектами более 9 лет
- нефтегаз
- нефтехимия
- цифровая трансформация
- управление подрядчиками
- внедрение ИТ решений
- цель перейти в IT / AI направление
- целевая зарплата 350-400 тысяч рублей
- интерес к AI Project Management
- интерес к автоматизации и AI агентам

Правила:

1. Всегда отвечай на русском.
2. Будь карьерным консультантом.
3. Будь проектным директором.
4. Давай конкретные рекомендации.
5. Отвечай структурированно.
6. Не упоминай что ты ИИ модель.
"""


def ask_ai(question, history=None):

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    if history:
        messages.extend(history)

    messages.append(
        {
            "role": "user",
            "content": question
        }
    )

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.3,
        messages=messages
    )

    return response.choices[0].message.content


def analyze_vacancy(vacancy_text, profile):

    prompt = f"""
Профиль кандидата:

{profile}

Описание вакансии:

{vacancy_text}

Сделай анализ:

1. Процент соответствия
2. Сильные стороны
3. Слабые стороны
4. Риски
5. Стоит ли откликаться
6. Вероятность приглашения
7. Итоговый вывод
"""

    return ask_ai(prompt)


def generate_cover_letter(vacancy_text, profile):

    prompt = f"""
Профиль кандидата:

{profile}

Описание вакансии:

{vacancy_text}

Напиши сильное сопроводительное письмо.

Требования:

- до 1500 символов
- уверенный стиль
- без шаблонных фраз
- акцент на управлении проектами
- акцент на цифровой трансформации
"""

    return ask_ai(prompt)