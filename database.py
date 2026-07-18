from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Vacancy(Base):
    __tablename__ = "vacancies"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_vacancy_source_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    track: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500))
    company: Mapped[str] = mapped_column(String(500), default="Не указана")
    description: Mapped[str] = mapped_column(Text)
    salary_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    work_format: Mapped[str] = mapped_column(String(32), default="unknown")
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    scores: Mapped[list["VacancyScore"]] = relationship(
        back_populates="vacancy", cascade="all, delete-orphan"
    )


class VacancyScore(Base):
    __tablename__ = "vacancy_scores"
    __table_args__ = (
        UniqueConstraint("vacancy_id", "track", name="uq_score_vacancy_track"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"), index=True)
    track: Mapped[str] = mapped_column(String(32), index=True)
    total: Mapped[int] = mapped_column(Integer, index=True)
    role_score: Mapped[int] = mapped_column(Integer)
    seniority_score: Mapped[int] = mapped_column(Integer)
    domain_score: Mapped[int] = mapped_column(Integer)
    experience_score: Mapped[int] = mapped_column(Integer)
    salary_score: Mapped[int] = mapped_column(Integer)
    freshness_score: Mapped[int] = mapped_column(Integer)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    risks_json: Mapped[str] = mapped_column(Text, default="[]")
    model: Mapped[str] = mapped_column(String(100), default="deterministic")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    vacancy: Mapped[Vacancy] = relationship(back_populates="scores")

    @property
    def reasons(self) -> list[str]:
        return json.loads(self.reasons_json)

    @property
    def risks(self) -> list[str]:
        return json.loads(self.risks_json)


class Digest(Base):
    __tablename__ = "digests"
    __table_args__ = (
        UniqueConstraint("digest_date", "chat_id", name="uq_digest_date_chat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    digest_date: Mapped[date] = mapped_column(Date, index=True)
    chat_id: Mapped[str] = mapped_column(String(64))
    vacancy_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    shadow: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class UserAction(Base):
    __tablename__ = "user_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"), index=True)
    chat_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class SourceCursor(Base):
    __tablename__ = "source_cursors"

    source: Mapped[str] = mapped_column(String(100), primary_key=True)
    cursor: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SourceConfig(Base):
    __tablename__ = "source_configs"
    __table_args__ = (
        UniqueConstraint("source_type", "identifier", name="uq_source_config"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    identifier: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class Database:
    def __init__(self, url: str):
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def get_cursor(self, source: str) -> str | None:
        with self.session() as session:
            row = session.get(SourceCursor, source)
            return row.cursor if row else None

    def set_cursor(self, source: str, cursor: str) -> None:
        with self.session() as session:
            row = session.get(SourceCursor, source)
            if row:
                row.cursor = cursor
            else:
                session.add(SourceCursor(source=source, cursor=cursor))

    def migrate_legacy_vacancies(self, path: str = "vacancies.json") -> int:
        legacy_path = Path(path)
        if not legacy_path.exists() or self.get_cursor("legacy_json_migrated"):
            return 0
        try:
            rows: list[dict[str, Any]] = json.loads(
                legacy_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, TypeError):
            self.set_cursor("legacy_json_migrated", "invalid")
            return 0

        imported = 0
        with self.session() as session:
            for index, row in enumerate(rows):
                text = str(row.get("text", "")).strip()
                if not text:
                    continue
                external_id = f"legacy-{index}"
                exists = session.scalar(
                    select(Vacancy).where(
                        Vacancy.source == "legacy",
                        Vacancy.external_id == external_id,
                    )
                )
                if exists:
                    continue
                session.add(
                    Vacancy(
                        source="legacy",
                        external_id=external_id,
                        track="manual",
                        title="Сохранённая вакансия",
                        company="Не указана",
                        description=text,
                        work_format="unknown",
                        url="",
                        content_hash=f"legacy-{index}",
                    )
                )
                imported += 1
        self.set_cursor("legacy_json_migrated", str(imported))
        return imported
