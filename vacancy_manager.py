from __future__ import annotations

import json
import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import desc, func, or_, select

from database import (
    Database,
    Digest,
    SourceConfig,
    UserAction,
    Vacancy,
    VacancyScore,
)
from hh_api import VacancyCandidate


UpsertStatus = Literal["created", "updated", "unchanged"]
TRACKING_QUERY_KEYS = {
    "from",
    "gh_src",
    "hhtmfrom",
    "hhtmfromlabel",
    "lever-source",
    "ref",
    "referrer",
    "source",
    "trk",
}


def normalize_work_format(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("-", "_")
    if normalized in {"remote", "telecommute", "work_from_home"}:
        return "remote"
    if normalized in {"hybrid", "гибрид"}:
        return "hybrid"
    if normalized in {"on_site", "onsite", "office", "офис"}:
        return "on_site"
    return "unknown"


def canonicalize_vacancy_url(value: str) -> str:
    if not value:
        return ""
    parts = urlsplit(value.strip())
    filtered_query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in TRACKING_QUERY_KEYS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(sorted(filtered_query)),
            "",
        )
    )


class VacancyRepository:
    def __init__(self, database: Database):
        self.db = database

    def upsert_candidate(self, candidate: VacancyCandidate) -> tuple[Vacancy, bool]:
        vacancy, status = self.upsert_candidate_with_status(candidate)
        return vacancy, status == "created"

    def upsert_candidate_with_status(
        self, candidate: VacancyCandidate
    ) -> tuple[Vacancy, UpsertStatus]:
        canonical_url = canonicalize_vacancy_url(candidate.url)
        description = (candidate.description or candidate.title).strip()
        content_hash = candidate.content_hash or hashlib.sha256(
            description.encode("utf-8")
        ).hexdigest()
        work_format = normalize_work_format(candidate.work_format)
        with self.db.session() as session:
            vacancy = session.scalar(
                select(Vacancy).where(
                    Vacancy.source == candidate.source,
                    Vacancy.external_id == candidate.external_id,
                )
            )
            if vacancy is None:
                duplicate_filters = [Vacancy.content_hash == content_hash]
                if canonical_url:
                    duplicate_filters.extend(
                        [
                            Vacancy.url == canonical_url,
                            Vacancy.url == candidate.url,
                        ]
                    )
                duplicate = session.scalar(
                    select(Vacancy).where(or_(*duplicate_filters))
                )
                if duplicate:
                    changed = self._merge_candidate(
                        duplicate,
                        candidate,
                        canonical_url=canonical_url,
                        description=description,
                        content_hash=content_hash,
                        work_format=work_format,
                    )
                    session.flush()
                    session.expunge(duplicate)
                    return duplicate, "updated" if changed else "unchanged"
                vacancy = Vacancy(
                    source=candidate.source,
                    external_id=candidate.external_id,
                    track=candidate.track,
                    title=candidate.title,
                    company=candidate.company,
                    description=description,
                    salary_from=candidate.salary_from,
                    salary_to=candidate.salary_to,
                    currency=candidate.currency,
                    work_format=work_format,
                    location=candidate.location,
                    published_at=candidate.published_at,
                    url=canonical_url,
                    content_hash=content_hash,
                    archived=candidate.archived,
                )
                session.add(vacancy)
                status: UpsertStatus = "created"
            else:
                changed = self._merge_candidate(
                    vacancy,
                    candidate,
                    canonical_url=canonical_url,
                    description=description,
                    content_hash=content_hash,
                    work_format=work_format,
                )
                status = "updated" if changed else "unchanged"
            session.flush()
            session.expunge(vacancy)
            return vacancy, status

    @staticmethod
    def _merge_candidate(
        vacancy: Vacancy,
        candidate: VacancyCandidate,
        *,
        canonical_url: str,
        description: str,
        content_hash: str,
        work_format: str,
    ) -> bool:
        changed = False

        def assign(name: str, value) -> None:
            nonlocal changed
            if value != getattr(vacancy, name):
                setattr(vacancy, name, value)
                changed = True

        richer_description = len(description) > len(vacancy.description or "")
        same_record = (
            vacancy.source == candidate.source
            and vacancy.external_id == candidate.external_id
        )
        if richer_description or not vacancy.description:
            assign("description", description)
            assign("content_hash", content_hash)
            assign("track", candidate.track)
        elif same_record and content_hash == vacancy.content_hash:
            assign("track", candidate.track)

        if candidate.title and (
            same_record
            or not vacancy.title
            or vacancy.title.startswith("Сохранённая вакансия")
        ):
            assign("title", candidate.title)
        if candidate.company and (
            same_record
            or not vacancy.company
            or vacancy.company.startswith("Из уведомления")
        ):
            assign("company", candidate.company)

        for name in ("salary_from", "salary_to", "currency", "location"):
            value = getattr(candidate, name)
            if value is not None:
                assign(name, value)
        if work_format != "unknown" or vacancy.work_format in {None, "unknown"}:
            assign("work_format", work_format)
        if candidate.published_at is not None:
            assign("published_at", candidate.published_at)
        if canonical_url:
            assign("url", canonical_url)
        assign("archived", candidate.archived)
        return changed

    def save_score(self, vacancy_id: int, score: dict) -> None:
        with self.db.session() as session:
            row = session.scalar(
                select(VacancyScore).where(
                    VacancyScore.vacancy_id == vacancy_id,
                    VacancyScore.track == score["track"],
                )
            )
            values = {
                "total": score["total"],
                "role_score": score["role_score"],
                "seniority_score": score["seniority_score"],
                "domain_score": score["domain_score"],
                "experience_score": score["experience_score"],
                "salary_score": score["salary_score"],
                "freshness_score": score["freshness_score"],
                "reasons_json": json.dumps(score["reasons"], ensure_ascii=False),
                "risks_json": json.dumps(score["risks"], ensure_ascii=False),
                "model": score["model"],
            }
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
            else:
                session.add(
                    VacancyScore(vacancy_id=vacancy_id, track=score["track"], **values)
                )

    def get_vacancy(self, vacancy_id: int) -> Vacancy | None:
        with self.db.session() as session:
            row = session.get(Vacancy, vacancy_id)
            if row:
                session.expunge(row)
            return row

    def get_ranked(
        self, minimum_score: int, hours: int = 72
    ) -> list[tuple[Vacancy, VacancyScore]]:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self.db.session() as session:
            rows = session.execute(
                select(Vacancy, VacancyScore)
                .join(VacancyScore, VacancyScore.vacancy_id == Vacancy.id)
                .where(
                    Vacancy.archived.is_(False),
                    Vacancy.work_format == "remote",
                    VacancyScore.total >= minimum_score,
                    Vacancy.created_at >= since,
                )
                .order_by(desc(VacancyScore.total), desc(Vacancy.published_at))
            ).all()
            result = []
            for vacancy, score in rows:
                session.expunge(vacancy)
                session.expunge(score)
                result.append((vacancy, score))
            return result

    def record_action(
        self,
        vacancy_id: int,
        chat_id: int | str,
        action: str,
        payload: str | None = None,
    ) -> None:
        with self.db.session() as session:
            session.add(
                UserAction(
                    vacancy_id=vacancy_id,
                    chat_id=str(chat_id),
                    action=action,
                    payload=payload,
                )
            )

    def action_count(self) -> int:
        with self.db.session() as session:
            return int(session.scalar(select(func.count(UserAction.id))) or 0)

    def digest_exists(self, digest_date: date, chat_id: int | str) -> bool:
        with self.db.session() as session:
            return (
                session.scalar(
                    select(Digest).where(
                        Digest.digest_date == digest_date,
                        Digest.chat_id == str(chat_id),
                    )
                )
                is not None
            )

    def save_digest(
        self,
        digest_date: date,
        chat_id: int | str,
        vacancy_ids: Iterable[int],
        *,
        sent: bool,
        shadow: bool,
    ) -> None:
        with self.db.session() as session:
            session.add(
                Digest(
                    digest_date=digest_date,
                    chat_id=str(chat_id),
                    vacancy_ids_json=json.dumps(list(vacancy_ids)),
                    sent=sent,
                    shadow=shadow,
                )
            )

    def list_sources(self, source_type: str = "telegram") -> list[str]:
        with self.db.session() as session:
            return list(
                session.scalars(
                    select(SourceConfig.identifier).where(
                        SourceConfig.source_type == source_type,
                        SourceConfig.enabled.is_(True),
                    )
                )
            )

    def add_source(self, identifier: str, source_type: str = "telegram") -> bool:
        with self.db.session() as session:
            existing = session.scalar(
                select(SourceConfig).where(
                    SourceConfig.source_type == source_type,
                    SourceConfig.identifier == identifier,
                )
            )
            if existing:
                existing.enabled = True
                return False
            session.add(SourceConfig(source_type=source_type, identifier=identifier))
            return True

    def remove_source(self, identifier: str, source_type: str = "telegram") -> bool:
        with self.db.session() as session:
            existing = session.scalar(
                select(SourceConfig).where(
                    SourceConfig.source_type == source_type,
                    SourceConfig.identifier == identifier,
                    SourceConfig.enabled.is_(True),
                )
            )
            if not existing:
                return False
            existing.enabled = False
            return True


_legacy_repository: VacancyRepository | None = None


def configure_legacy_repository(repository: VacancyRepository) -> None:
    global _legacy_repository
    _legacy_repository = repository


def save_vacancy(vacancy_text: str) -> None:
    if not _legacy_repository:
        return
    now = datetime.now(timezone.utc)
    candidate = VacancyCandidate(
        source="manual",
        external_id=f"manual-{int(now.timestamp() * 1000)}",
        track="manual",
        title="Вакансия для ручного анализа",
        company="Не указана",
        description=vacancy_text,
        salary_from=None,
        salary_to=None,
        currency=None,
        work_format="unknown",
        location=None,
        published_at=now,
        url="",
        content_hash=str(hash(vacancy_text)),
    )
    _legacy_repository.upsert_candidate(candidate)


def get_stats() -> int:
    if not _legacy_repository:
        return 0
    with _legacy_repository.db.session() as session:
        return int(session.scalar(select(func.count(Vacancy.id))) or 0)


def get_last_vacancies(limit: int = 5) -> list[dict[str, str]]:
    if not _legacy_repository:
        return []
    with _legacy_repository.db.session() as session:
        rows = list(
            session.scalars(
                select(Vacancy).order_by(desc(Vacancy.created_at)).limit(limit)
            )
        )
        return [
            {
                "date": row.created_at.strftime("%Y-%m-%d %H:%M"),
                "text": row.description,
                "status": "new",
            }
            for row in rows
        ]
