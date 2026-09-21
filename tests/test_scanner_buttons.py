import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from database import Database, Vacancy
from hh_api import VacancyCandidate
from scanner_digest import send_scanner_today
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
                    published_at=datetime(2026, 9, 21, 18, 30, tzinfo=timezone.utc),
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
                    published_at=datetime(2026, 9, 21, 19, 0, tzinfo=timezone.utc),
                    url="https://example.com/not-seen",
                    content_hash="not-seen",
                    archived=True,
                ),
            ]
        )
    rows = repository.get_scanner_for_date(date(2026, 9, 21), timezone.utc)
    assert [row.external_id for row in rows] == ["today"]

    bot = SimpleNamespace(send_message=AsyncMock())
    count = asyncio.run(
        send_scanner_today(
            SimpleNamespace(bot=bot),
            SimpleNamespace(get_scanner_for_date=lambda *_: rows),
            SimpleNamespace(timezone=timezone.utc),
            chat_id=123,
        )
    )
    assert count == 1
    assert bot.send_message.await_count == 2
    assert bot.send_message.await_args_list[1].kwargs[
        "reply_markup"
    ].inline_keyboard[0][0].url == "https://example.com/today"
