from datetime import datetime, timezone

from database import Vacancy
from ranking import deterministic_score


def make_vacancy(**overrides):
    values = {
        "source": "test",
        "external_id": "1",
        "track": "senior_it",
        "title": "Senior IT Project Manager",
        "company": "Example",
        "description": (
            "Delivery SAP ERP, цифровая трансформация, управление рисками, "
            "бюджет и команда 20 человек, подрядчики, интеграции и релизы"
        ),
        "salary_from": 350000,
        "salary_to": 450000,
        "currency": "RUR",
        "work_format": "remote",
        "location": "Россия",
        "published_at": datetime.now(timezone.utc),
        "url": "https://example.com/1",
        "content_hash": "hash",
    }
    values.update(overrides)
    return Vacancy(**values)


def test_deterministic_score_components_equal_total():
    score = deterministic_score(make_vacancy(), "senior_it")
    components = sum(
        score[key]
        for key in (
            "role_score",
            "seniority_score",
            "domain_score",
            "experience_score",
            "salary_score",
            "freshness_score",
        )
    )
    assert score["total"] == components
    assert score["salary_score"] == 10
    assert len(score["reasons"]) == 3


def test_unknown_salary_is_not_rejected():
    score = deterministic_score(
        make_vacancy(salary_from=None, salary_to=None), "senior_it"
    )
    assert score["salary_score"] == 5
    assert "Зарплата не указана" in score["risks"]
