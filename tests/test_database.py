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
