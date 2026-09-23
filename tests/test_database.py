from datetime import date, datetime, timedelta, timezone

from database import Database, Vacancy
from hh_api import VacancyCandidate
from vacancy_manager import VacancyRepository


def candidate(external_id="1", url="https://example.com/1", content_hash="hash"):
    return VacancyCandidate(
        source="test",
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
        content_hash=content_hash,
    )


def test_repository_deduplicates_by_source_id(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'test.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    first, first_created = repository.upsert_candidate(candidate())
    second, second_created = repository.upsert_candidate(candidate())
    assert first_created is True
    assert second_created is False
    assert first.id == second.id


def test_repository_deduplicates_by_url(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'test.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    first, _ = repository.upsert_candidate(candidate())
    duplicate, created = repository.upsert_candidate(
        candidate(external_id="2", content_hash="other")
    )
    assert created is False
    assert duplicate.id == first.id


def test_repository_normalizes_unknown_work_format(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'normalize.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    row = candidate()
    row.work_format = None

    vacancy, created = repository.upsert_candidate(row)

    assert created is True
    assert vacancy.work_format == "unknown"


def test_richer_duplicate_updates_description_and_canonicalizes_url(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'enrichment.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    short = candidate(
        external_id="email-1",
        url="https://example.com/jobs/42?utm_source=email",
        content_hash="short-hash",
    )
    short.source = "email"
    short.description = "Project Manager remote"
    first, status = repository.upsert_candidate_with_status(short)
    assert status == "created"

    rich = candidate(
        external_id="api-42",
        url="https://example.com/jobs/42?ref=feed",
        content_hash="rich-hash",
    )
    rich.source = "public_api"
    rich.description = (
        "Senior Project Manager remote. Full responsibilities, requirements, "
        "stakeholder management, budget ownership and delivery details."
    )
    duplicate, status = repository.upsert_candidate_with_status(rich)

    assert status == "updated"
    assert duplicate.id == first.id
    assert duplicate.description == rich.description
    assert duplicate.url == "https://example.com/jobs/42"


def test_ranked_uses_publication_date_not_database_insert_date(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'published-date.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    vacancy, _ = repository.upsert_candidate(candidate())
    repository.save_score(
        vacancy.id,
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
    with db.session() as session:
        stored = session.get(Vacancy, vacancy.id)
        stored.created_at = datetime.now(timezone.utc) - timedelta(days=10)

    assert len(repository.get_ranked(70)) == 1


def test_ranked_includes_hybrid_vacancy(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'hybrid.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    row = candidate()
    row.work_format = "hybrid"
    vacancy, _ = repository.upsert_candidate(row)
    repository.save_score(
        vacancy.id,
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

    assert repository.get_ranked(70)[0][0].work_format == "hybrid"


def test_ranked_can_filter_headhunter_sources(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'sources.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    hh = candidate(external_id="hh", url="https://hh.ru/vacancy/1")
    hh.source = "hh"
    other = candidate(external_id="habr", url="https://career.habr.com/vacancies/1")
    other.source = "habr"
    for row in (hh, other):
        vacancy, _ = repository.upsert_candidate(row)
        repository.save_score(
            vacancy.id,
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

    rows = repository.get_ranked(0, sources={"hh", "hh_email"})

    assert [vacancy.source for vacancy, _ in rows] == ["hh"]


def test_ranked_handles_two_track_scores_for_same_vacancy(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'two-tracks.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    row = candidate(external_id="hh-42", url="https://hh.ru/vacancy/42")
    row.source = "hh"
    vacancy, _ = repository.upsert_candidate(row)
    for track in ("senior_it", "enterprise_epc"):
        repository.save_score(
            vacancy.id,
            {
                "track": track,
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

    rows = repository.get_ranked(0, sources={"hh"})

    assert len(rows) == 2
    assert {score.track for _, score in rows} == {"senior_it", "enterprise_epc"}
    assert all(vacancy_row.id == vacancy.id for vacancy_row, _ in rows)


def test_scanner_query_uses_first_seen_calendar_date(tmp_path):
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
                    title="Project Manager",
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
                    external_id="old",
                    track="scanner_primary",
                    title="Old Project Manager",
                    company="Example",
                    description="Scanner",
                    work_format="unknown",
                    published_at=datetime(2026, 9, 20, 18, 30, tzinfo=timezone.utc),
                    url="https://example.com/old",
                    content_hash="old",
                    archived=False,
                ),
            ]
        )

    rows = repository.get_scanner_for_date(date(2026, 9, 21), timezone.utc)

    assert [row.external_id for row in rows] == ["today"]
