import requests


def get_vacancies():

    url = "https://api.hh.ru/vacancies"

    params = {
        "text": "project manager",
        "per_page": 5,
        "page": 0,
        "search_field": "name"
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    }

    session = requests.Session()

    response = session.get(
        url,
        params=params,
        headers=headers,
        timeout=10
    )

    # ЛОГ ДЛЯ ДИАГНОСТИКИ
    print("STATUS:", response.status_code)
    print("TEXT:", response.text[:300])

    if response.status_code != 200:
        return [f"HH BLOCKED: {response.status_code}"]

    data = response.json()

    items = data.get("items", [])

    if not items:
        return ["No vacancies found"]

    vacancies = []

    for item in items:

        name = item.get("name", "no title")
        company = item.get("employer", {}).get("name", "no company")
        link = item.get("alternate_url", "")

        vacancies.append(
            f"📌 {name}\n"
            f"🏢 {company}\n"
            f"🔗 {link}"
        )

    return vacancies