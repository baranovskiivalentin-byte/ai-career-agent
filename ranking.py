from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from config import Settings
from database import Vacancy


LOGGER = logging.getLogger(__name__)
Track = Literal["senior_it", "enterprise_epc"]


class AIAnalysis(BaseModel):
    track: Track
    total: int = Field(ge=0, le=100)
    role_score: int = Field(ge=0, le=35)
    seniority_score: int = Field(ge=0, le=20)
    domain_score: int = Field(ge=0, le=15)
    experience_score: int = Field(ge=0, le=15)
    salary_score: int = Field(ge=0, le=10)
    freshness_score: int = Field(ge=0, le=5)
    reasons: list[str] = Field(min_length=1, max_length=3)
    risks: list[str] = Field(max_length=3)


ROLE_WORDS = {
    "senior_it": (
        "senior project manager",
        "it project manager",
        "delivery manager",
        "program manager",
        "руководитель it-проект",
        "руководитель ит-проект",
    ),
    "enterprise_epc": (
        "руководитель проектов",
        "project manager",
        "program manager",
        "заместитель руководителя проекта",
    ),
}
DOMAIN_WORDS = {
    "senior_it": (
        "sap",
        "erp",
        "enterprise",
        "digital transformation",
        "цифровая трансформация",
        "интеграц",
        "миграц",
        "релиз",
        "ai",
    ),
    "enterprise_epc": (
        "epc",
        "capex",
        "инжиниринг",
        "нефтегаз",
        "подрядчик",
        "wbs",
        "pmbok",
        "управление рисками",
    ),
}
EXPERIENCE_WORDS = (
    "stakeholder",
    "стейкхолдер",
    "risk management",
    "управление рисками",
    "budget",
    "бюджет",
    "vendor",
    "подрядчик",
    "команд",
    "agile",
    "waterfall",
)


def _keyword_points(text: str, words: tuple[str, ...], maximum: int) -> int:
    matches = sum(1 for word in words if word in text)
    if not words:
        return 0
    return min(maximum, round(maximum * matches / min(len(words), 5)))


def deterministic_score(vacancy: Vacancy, track: Track) -> dict[str, Any]:
    text = f"{vacancy.title} {vacancy.description}".lower()
    role_score = _keyword_points(text, ROLE_WORDS[track], 35)
    seniority_score = _keyword_points(
        text,
        ("senior", "руководител", "lead", "delivery", "program", "20", "бюджет"),
        20,
    )
    domain_score = _keyword_points(text, DOMAIN_WORDS[track], 15)
    experience_score = _keyword_points(text, EXPERIENCE_WORDS, 15)
    salary_value = vacancy.salary_from or vacancy.salary_to
    if salary_value is None:
        salary_score = 5
    elif (vacancy.currency or "RUR").upper() in {"RUR", "RUB"}:
        salary_score = (
            10
            if salary_value >= 330_000
            else max(0, round(salary_value / 330_000 * 10))
        )
    else:
        salary_score = 5
    if not vacancy.published_at:
        freshness_score = 3
    else:
        published = vacancy.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_hours = max(
            0, (datetime.now(timezone.utc) - published).total_seconds() / 3600
        )
        freshness_score = 5 if age_hours <= 24 else 3 if age_hours <= 72 else 1
    total = min(
        100,
        role_score
        + seniority_score
        + domain_score
        + experience_score
        + salary_score
        + freshness_score,
    )
    reasons: list[str] = []
    if role_score >= 20:
        reasons.append("Название и обязанности соответствуют целевой роли")
    if domain_score >= 8:
        reasons.append("Домен совпадает с опытом enterprise/IT/EPC")
    if experience_score >= 8:
        reasons.append("Востребован подтверждённый опыт управления проектами")
    if salary_score >= 8:
        reasons.append("Зарплата соответствует целевому диапазону")
    while len(reasons) < 3:
        reasons.append("Есть пересечение с навыками и карьерным направлением")
    risks: list[str] = []
    if salary_value is None:
        risks.append("Зарплата не указана")
    if role_score < 15:
        risks.append("Название роли совпадает не полностью")
    if domain_score < 5:
        risks.append("Домен вакансии описан недостаточно конкретно")
    return {
        "track": track,
        "total": total,
        "role_score": role_score,
        "seniority_score": seniority_score,
        "domain_score": domain_score,
        "experience_score": experience_score,
        "salary_score": salary_score,
        "freshness_score": freshness_score,
        "reasons": reasons[:3],
        "risks": risks[:3],
        "model": "deterministic",
    }


class VacancyRanker:
    def __init__(self, settings: Settings, profile: dict[str, Any]):
        self.settings = settings
        self.profile = profile
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def score(self, vacancy: Vacancy) -> dict[str, Any]:
        track: Track = (
            vacancy.track
            if vacancy.track in {"senior_it", "enterprise_epc"}
            else "senior_it"
        )
        baseline = deterministic_score(vacancy, track)
        prompt = self._prompt(vacancy, track, baseline)
        for model in dict.fromkeys(
            [self.settings.scoring_model, self.settings.fallback_model]
        ):
            try:
                response = await self.client.responses.parse(
                    model=model,
                    input=[
                        {
                            "role": "system",
                            "content": (
                                "Ты оцениваешь вакансии строго по подтвержденному профилю. "
                                "Не приписывай кандидату отсутствующий опыт. Сумма шести "
                                "компонентов должна равняться total."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    text_format=AIAnalysis,
                )
                parsed = response.output_parsed
                if parsed is None:
                    continue
                result = parsed.model_dump()
                component_total = sum(
                    result[key]
                    for key in (
                        "role_score",
                        "seniority_score",
                        "domain_score",
                        "experience_score",
                        "salary_score",
                        "freshness_score",
                    )
                )
                result["total"] = min(100, component_total)
                result["model"] = model
                return result
            except Exception as exc:
                LOGGER.warning("Скоринг OpenAI (%s) не выполнен: %s", model, exc)
        return baseline

    def _prompt(self, vacancy: Vacancy, track: Track, baseline: dict[str, Any]) -> str:
        safe_profile = {
            key: self.profile.get(key)
            for key in (
                "target_salary_min",
                "target_salary_max",
                "target_roles",
                "target_domains",
                "experience_years",
                "industries",
                "project_budget",
                "team_size",
                "methodologies",
                "skills",
                "strong_points",
            )
        }
        return (
            f"Профиль:\n{json.dumps(safe_profile, ensure_ascii=False)}\n\n"
            f"Трек: {track}\n"
            f"Вакансия: {vacancy.title} — {vacancy.company}\n"
            f"Описание:\n{vacancy.description[:12000]}\n"
            f"Зарплата: {vacancy.salary_from}–{vacancy.salary_to} {vacancy.currency}\n"
            f"Детерминированная предварительная оценка: "
            f"{json.dumps(baseline, ensure_ascii=False)}"
        )
