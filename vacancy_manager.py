import json
import os
from datetime import datetime

VACANCIES_FILE = "vacancies.json"


def load_vacancies():
    try:
        if not os.path.exists(VACANCIES_FILE):
            return []

        with open(VACANCIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return []


def save_vacancy(vacancy_text):
    vacancies = load_vacancies()

    vacancy = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "text": vacancy_text[:500],
        "status": "new"
    }

    vacancies.append(vacancy)

    try:
        with open(VACANCIES_FILE, "w", encoding="utf-8") as f:
            json.dump(vacancies, f, ensure_ascii=False, indent=4)
    except Exception:
        # Railway может не дать писать файл — не валим бота
        pass


def get_stats():
    vacancies = load_vacancies()
    return len(vacancies)


def get_last_vacancies(limit=5):
    vacancies = load_vacancies()
    return vacancies[-limit:]