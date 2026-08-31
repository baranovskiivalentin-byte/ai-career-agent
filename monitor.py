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
        stats = {
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "scored": 0,
            "errors": 0,
        }
        fetchers: list[SourceFetcher] = list(self.optional_fetchers)
        if self._hh_api_configured():
            fetchers.insert(0, self.hh.fetch_recent)
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
                    status = await self.ingest_one_with_status(candidate)
                    stats["created"] += int(status == "created")
                    stats["updated"] += int(status == "updated")
                    stats["scored"] += int(status in {"created", "updated"})
                except Exception:
                    LOGGER.exception(
                        "Не удалось сохранить/оценить вакансию %s:%s",
                        candidate.source,
                        candidate.external_id,
                    )
                    stats["errors"] += 1
        LOGGER.info("Мониторинг завершён: %s", stats)
        return stats

    def _hh_api_configured(self) -> bool:
        if self.settings is None:
            return True
        return bool(
            self.settings.hh_access_token
            or (
                self.settings.hh_client_id
                and self.settings.hh_client_secret
            )
        )

    async def ingest_one(self, candidate: VacancyCandidate) -> bool:
        status = await self.ingest_one_with_status(candidate)
        return status == "created"

    async def ingest_one_with_status(self, candidate: VacancyCandidate) -> str:
        vacancy, status = self.repository.upsert_candidate_with_status(candidate)
        if status in {"created", "updated"}:
            score = await self.ranker.score(vacancy)
            self.repository.save_score(vacancy.id, score)
        return status
