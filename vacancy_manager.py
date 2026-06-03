import json
from datetime import datetime

VACANCIES_FILE = "vacancies.json"


def load_vacancies():

    try:

        with open(
            VACANCIES_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except:

        return []


def save_vacancy(vacancy_text):

    vacancies = load_vacancies()

    vacancy = {
        "date": datetime.now().strftime(
            "%Y-%m-%d %H:%M"
        ),
        "text": vacancy_text[:500],
        "status": "new"
    }

    vacancies.append(vacancy)

    with open(
        VACANCIES_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            vacancies,
            f,
            ensure_ascii=False,
            indent=4
        )


def get_stats():

    vacancies = load_vacancies()

    return len(vacancies)


def get_last_vacancies(limit=5):

    vacancies = load_vacancies()

    return vacancies[-limit:]