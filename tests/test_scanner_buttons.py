import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from database import Database, Vacancy
from hh_api import VacancyCandidate
from scanner_digest import send_scanner_recommendations, send_scanner_today
from vacancy_manager import VacancyRepository


def candidate(source: str, external_id: str, url: str) -> VacancyCandidate:
    return VacancyCandidate(
        source=source,
        external_id=external_id,
        track="senior_it",
        title="Project Manager",
        company="Example",
        description="Remote role",
        salary_from=None,
        salary_to=None,
        currency=None,
        work_format="remote",
        location=None,
        published_at=datetime.now(timezone.utc),
        url=url,
        content_hash=external_id,
    )


def score(repository: VacancyRepository, vacancy_id: int) -> None:
    repository.save_score(
        vacancy_id,
        {
            "track": "senior_it",
            "total": 80,
            "role_score": 30,
            "seniority_score": 15,
            "domain_score": 10,
            "experience_score": 10,
            "salary_score": 10,
            "freshness_score": 5,
            "reasons": ["Подходит"],
            "risks": [],
            "model": "test",
        },
    )


def test_headhunter_filter_excludes_other_sources(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'sources.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    for row in (
        candidate("hh", "hh-1", "https://hh.ru/vacancy/1"),
        candidate("habr", "habr-1", "https://career.habr.com/vacancies/1"),
    ):
        vacancy, _ = repository.upsert_candidate(row)
        score(repository, vacancy.id)

    rows = repository.get_ranked(0, sources={"hh", "hh_email"})

    assert [vacancy.source for vacancy, _ in rows] == ["hh"]


def test_scanner_button_sends_only_active_rows_for_requested_date(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'scanner.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    today = datetime.now(timezone.utc)
    with db.session() as session:
        session.add_all(
            [
                Vacancy(
                    source="scanner",
                    external_id="today",
                    track="scanner_primary",
                    title="Руководитель проектов",
                    company="Example",
                    description="Scanner",
                    work_format="unknown",
                    published_at=today,
                    url="https://example.com/today",
                    content_hash="today",
                    archived=False,
                ),
                Vacancy(
                    source="scanner",
                    external_id="not-seen",
                    track="scanner_secondary",
                    title="Not seen",
                    company="Example",
                    description="Scanner",
                    work_format="unknown",
                    published_at=today,
                    url="https://example.com/not-seen",
                    content_hash="not-seen",
                    archived=True,
                ),
            ]
        )
    rows = repository.get_scanner_for_date(today.date(), timezone.utc)
    assert [row.external_id for row in rows] == ["today"]
    score(repository, rows[0].id)

    bot = SimpleNamespace(send_message=AsyncMock())
    count = asyncio.run(
        send_scanner_today(
            SimpleNamespace(bot=bot),
            repository,
            SimpleNamespace(timezone=timezone.utc, scoring_threshold=70),
            chat_id=123,
        )
    )
    assert count == 1
    assert bot.send_message.await_count == 2
    assert bot.send_message.await_args_list[1].kwargs[
        "reply_markup"
    ].inline_keyboard[0][0].url == "https://example.com/today"
    assert "80/100" in bot.send_message.await_args_list[1].kwargs["text"]
    assert "Рекомендую откликнуться" in bot.send_message.await_args_list[1].kwargs["text"]


def test_pending_scanner_score_requires_full_description(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'pending-scanner.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    with db.session() as session:
        session.add_all(
            [
                Vacancy(
                    source="scanner",
                    external_id="rich",
                    track="scanner_primary",
                    title="IT Project Manager",
                    company="Example",
                    description="Full official requirements and duties. " * 12,
                    work_format="unknown",
                    published_at=datetime.now(timezone.utc),
                    url="https://example.com/rich",
                    content_hash="rich",
                    archived=False,
                ),
                Vacancy(
                    source="scanner",
                    external_id="stub",
                    track="scanner_primary",
                    title="Project Manager",
                    company="Example",
                    description="Vacancy Scanner: PRIMARY",
                    work_format="unknown",
                    published_at=datetime.now(timezone.utc),
                    url="https://example.com/stub",
                    content_hash="stub",
                    archived=False,
                ),
            ]
        )

    pending = repository.get_pending_scanner_scores()

    assert [row.external_id for row in pending] == ["rich"]
    score(repository, pending[0].id)
    assert repository.get_pending_scanner_scores() == []


def test_scanner_recommendations_show_scored_active_vacancy(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'recommendations.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    with db.session() as session:
        session.add(
            Vacancy(
                source="scanner",
                external_id="best",
                track="scanner_primary",
                title="IT Project Manager",
                company="Example",
                description="Full official requirements and duties. " * 12,
                work_format="unknown",
                published_at=datetime.now(timezone.utc),
                url="https://example.com/best",
                content_hash="best",
                archived=False,
            )
        )
    vacancy = repository.get_pending_scanner_scores()[0]
    score(repository, vacancy.id)
    bot = SimpleNamespace(send_message=AsyncMock())

    count = asyncio.run(
        send_scanner_recommendations(
            SimpleNamespace(bot=bot),
            repository,
            SimpleNamespace(timezone=timezone.utc, scoring_threshold=70),
            chat_id=123,
        )
    )

    assert count == 1
    assert bot.send_message.await_count == 2
    assert "80/100" in bot.send_message.await_args_list[1].kwargs["text"]
