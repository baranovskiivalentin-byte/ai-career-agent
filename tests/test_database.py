from datetime import datetime, timezone

from database import Database
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
