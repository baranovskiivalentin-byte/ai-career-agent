import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from database import Database
from hh_api import VacancyCandidate
from monitor import VacancyMonitor
from vacancy_manager import VacancyRepository


class FakeRanker:
    async def score(self, vacancy):
        return {
            "track": vacancy.track,
            "total": 80,
            "role_score": 30,
            "seniority_score": 15,
            "domain_score": 10,
            "experience_score": 10,
            "salary_score": 10,
            "freshness_score": 5,
            "reasons": ["Подходит"],
            "risks": [],
            "model": "fake",
        }


class FakeHH:
    def __init__(self, rows):
        self.rows = rows

    async def fetch_recent(self):
        return self.rows


class FailingHH:
    async def fetch_recent(self):
        raise AssertionError("HH API не должен вызываться без OAuth")


def test_monitor_persists_and_scores_new_candidate(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'monitor.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    candidate = VacancyCandidate(
        source="hh",
        external_id="42",
        track="senior_it",
        title="Senior IT Project Manager",
        company="Example",
        description="Remote SAP delivery",
        salary_from=350000,
        salary_to=None,
        currency="RUR",
        work_format="remote",
        location="Россия",
        published_at=datetime.now(timezone.utc),
        url="https://hh.ru/vacancy/42",
        content_hash="monitor-hash",
    )
    monitor = VacancyMonitor.__new__(VacancyMonitor)
    monitor.settings = None
    monitor.repository = repository
    monitor.ranker = FakeRanker()
    monitor.hh = FakeHH([candidate])
    monitor.optional_fetchers = []

    stats = asyncio.run(monitor.collect())
    assert stats == {
        "fetched": 1,
        "created": 1,
        "updated": 0,
        "scored": 1,
        "errors": 0,
    }
    assert len(repository.get_ranked(70)) == 1


def test_monitor_skips_hh_without_oauth_credentials(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'monitor-no-hh.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    monitor = VacancyMonitor.__new__(VacancyMonitor)
    monitor.settings = SimpleNamespace(
        hh_access_token=None,
        hh_client_id=None,
        hh_client_secret=None,
    )
    monitor.repository = repository
    monitor.ranker = FakeRanker()
    monitor.hh = FailingHH()
    monitor.optional_fetchers = []

    stats = asyncio.run(monitor.collect())

    assert stats == {
        "fetched": 0,
        "created": 0,
        "updated": 0,
        "scored": 0,
        "errors": 0,
    }


def test_monitor_rescores_richer_duplicate(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'monitor-update.db'}")
    db.create_schema()
    repository = VacancyRepository(db)
    short = VacancyCandidate(
        source="email",
        external_id="email-42",
        track="senior_it",
        title="Project Manager",
        company="Из уведомления",
        description="Remote Project Manager",
        salary_from=None,
        salary_to=None,
        currency=None,
        work_format="remote",
        location=None,
        published_at=datetime.now(timezone.utc),
        url="https://example.com/jobs/42?utm_source=email",
        content_hash="short",
    )
    repository.upsert_candidate(short)
    rich = VacancyCandidate(
        source="api",
        external_id="api-42",
        track="senior_it",
        title="Senior Technical Project Manager",
        company="Example",
        description=(
            "Remote senior delivery role with full responsibilities, requirements, "
            "stakeholders, budget, vendors, integrations and risk management."
        ),
        salary_from=5000,
        salary_to=None,
        currency="USD",
        work_format="remote",
        location="Worldwide",
        published_at=datetime.now(timezone.utc),
        url="https://example.com/jobs/42?ref=api",
        content_hash="rich",
    )
    monitor = VacancyMonitor.__new__(VacancyMonitor)
    monitor.settings = SimpleNamespace(
        hh_access_token=None,
        hh_client_id=None,
        hh_client_secret=None,
    )
    monitor.repository = repository
    monitor.ranker = FakeRanker()
    monitor.hh = FailingHH()
    monitor.optional_fetchers = [FakeHH([rich]).fetch_recent]

    stats = asyncio.run(monitor.collect())

    assert stats["updated"] == 1
    assert stats["scored"] == 1
