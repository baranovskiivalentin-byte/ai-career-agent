from __future__ import annotations

import logging
from typing import Awaitable, Callable

from config import Settings
from hh_api import HHClient, VacancyCandidate
from ranking import VacancyRanker
from vacancy_manager import VacancyRepository


LOGGER = logging.getLogger(__name__)
SourceFetcher = Callable[[], Awaitable[list[VacancyCandidate]]]


class VacancyMonitor:
    def __init__(
        self,
        settings: Settings,
        repository: VacancyRepository,
        ranker: VacancyRanker,
        optional_fetchers: list[SourceFetcher] | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.ranker = ranker
        self.hh = HHClient(settings)
        self.optional_fetchers = optional_fetchers or []

    async def collect(self) -> dict[str, int]:
        stats = {"fetched": 0, "created": 0, "scored": 0, "errors": 0}
        fetchers: list[SourceFetcher] = [self.hh.fetch_recent, *self.optional_fetchers]
        for fetcher in fetchers:
            try:
                candidates = await fetcher()
            except Exception:
                LOGGER.exception("Источник вакансий завершился с ошибкой")
                stats["errors"] += 1
                continue
            stats["fetched"] += len(candidates)
            for candidate in candidates:
                try:
                    created = await self.ingest_one(candidate)
                    stats["created"] += int(created)
                    stats["scored"] += int(created)
                except Exception:
                    LOGGER.exception(
                        "Не удалось сохранить/оценить вакансию %s:%s",
                        candidate.source,
                        candidate.external_id,
                    )
                    stats["errors"] += 1
        LOGGER.info("Мониторинг завершён: %s", stats)
        return stats

    async def ingest_one(self, candidate: VacancyCandidate) -> bool:
        vacancy, created = self.repository.upsert_candidate(candidate)
        if created:
            score = await self.ranker.score(vacancy)
            self.repository.save_score(vacancy.id, score)
        return created
