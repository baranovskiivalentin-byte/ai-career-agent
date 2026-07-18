import asyncio
from datetime import datetime, timezone

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
    assert stats == {"fetched": 1, "created": 1, "scored": 1, "errors": 0}
    assert len(repository.get_ranked(70)) == 1
