import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from database import Vacancy
from ranking import VacancyRanker, deterministic_score


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


def test_monthly_international_salary_uses_currency_target():
    score = deterministic_score(
        make_vacancy(salary_from=5000, salary_to=None, currency="USD"),
        "senior_it",
    )
    assert score["salary_score"] == 10


def test_extended_international_role_is_recognized():
    vacancy = make_vacancy(
        title="Senior Implementation Manager",
        description="Remote enterprise AI transformation and stakeholder delivery",
    )
    score = deterministic_score(vacancy, "senior_it")
    assert score["role_score"] > 0


def test_insufficient_quota_disables_repeated_openai_calls():
    class QuotaError(Exception):
        code = "insufficient_quota"

    settings = SimpleNamespace(
        openai_api_key="test-key",
        scoring_model="primary-model",
        fallback_model="fallback-model",
    )
    ranker = VacancyRanker(settings, {})
    parse = AsyncMock(side_effect=QuotaError("credit_balance_exhausted"))
    ranker.client = SimpleNamespace(responses=SimpleNamespace(parse=parse))

    first = asyncio.run(ranker.score(make_vacancy()))
    second = asyncio.run(ranker.score(make_vacancy(external_id="2")))

    assert first["model"] == "deterministic"
    assert second["model"] == "deterministic"
    assert parse.await_count == 1
