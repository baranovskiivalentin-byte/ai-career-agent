from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Any

import httpx

from config import Settings

LOGGER = logging.getLogger(__name__)
HH_API_URL = "https://api.hh.ru"

TRACK_QUERIES: dict[str, list[str]] = {
    "senior_it": [
        "Руководитель проекта",
        "Руководитель проектов",
        "Руководитель ИТ проекта",
        "Руководитель проекта ИТ",
        "Руководитель IT проекта",
        "Руководитель проекта IT",
        "Менеджер проекта",
        "Менеджер ИТ проекта",
        "Менеджер проекта ИТ",
        "Project Manager",
        "IT Project Manager",
        "Project IT Manager",
        "Senior IT Project Manager",
        "Delivery Manager",
        "Program Manager",
        "AI Project Manager",
    ],
    "enterprise_epc": [
        "Руководитель проектов по внедрению",
        "Руководитель проектов цифровой трансформации",
        "Руководитель проекта ERP",
        "Project Manager ERP",
    ],
}


@dataclass(slots=True)
class VacancyCandidate:
    source: str
    external_id: str
    track: str
    title: str
    company: str
    description: str
    salary_from: int | None
    salary_to: int | None
    currency: str | None
    work_format: str
    location: str | None
    published_at: datetime | None
    url: str
    content_hash: str
    archived: bool = False


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def parse_datetime(value: str | float | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if abs(timestamp) > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _format_ids(item: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    schedule = item.get("schedule") or {}
    if schedule.get("id"):
        result.add(str(schedule["id"]).lower())
    for field in ("work_format", "work_schedule_by_days"):
        value = item.get(field)
        if isinstance(value, dict) and value.get("id"):
            result.add(str(value["id"]).lower())
        elif isinstance(value, list):
            result.update(str(row.get("id", "")).lower() for row in value)
    return result


def hh_work_format(item: dict[str, Any]) -> str:
    ids = _format_ids(item)
    if "hybrid" in ids or "гибрид" in ids:
        return "hybrid"
    if any(value in {"remote", "fully_remote", "remote_work"} for value in ids):
        return "remote"
    if "on_site" in ids or "office" in ids:
        return "on_site"
    searchable = " ".join(
        [
            str((item.get("schedule") or {}).get("name", "")),
            str(item.get("description", "")),
            str((item.get("snippet") or {}).get("requirement", "")),
            str((item.get("snippet") or {}).get("responsibility", "")),
        ]
    ).lower()
    hybrid_words = (
        "гибридный формат",
        "гибридном формате",
        "гибридная работа",
        "гибридный график",
        "hybrid work",
        "hybrid schedule",
    )
    if any(word in searchable for word in hybrid_words):
        return "hybrid"
    remote_words = (
        "удаленная работа",
        "удалённая работа",
        "полностью удаленно",
        "полностью удалённо",
        "fully remote",
        "remote work",
    )
    if any(word in searchable for word in remote_words):
        return "remote"
    return "unknown"


def is_explicitly_remote(item: dict[str, Any]) -> bool:
    """Backward-compatible predicate for callers that need remote-only roles."""
    return hh_work_format(item) == "remote"


def normalize_hh_vacancy(item: dict[str, Any], track: str) -> VacancyCandidate:
    salary = item.get("salary") or {}
    description = strip_html(item.get("description"))
    if not description:
        snippet = item.get("snippet") or {}
        description = strip_html(
            " ".join(
                [snippet.get("requirement") or "", snippet.get("responsibility") or ""]
            )
        )
    url = item.get("alternate_url") or item.get("apply_alternate_url") or ""
    external_id = str(item.get("id") or hashlib.sha256(url.encode()).hexdigest())
    content_hash = hashlib.sha256(
        f"{item.get('name', '')}|{(item.get('employer') or {}).get('name', '')}|{description}".encode()
    ).hexdigest()
    return VacancyCandidate(
        source="hh",
        external_id=external_id,
        track=track,
        title=str(item.get("name") or "Без названия"),
        company=str((item.get("employer") or {}).get("name") or "Не указана"),
        description=description,
        salary_from=salary.get("from"),
        salary_to=salary.get("to"),
        currency=salary.get("currency"),
        work_format=hh_work_format(item),
        location=(item.get("area") or {}).get("name"),
        published_at=parse_datetime(item.get("published_at")),
        url=url,
        content_hash=content_hash,
        archived=bool(item.get("archived", False)),
    )


class HHClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.headers = {
            "HH-User-Agent": settings.hh_user_agent,
            "User-Agent": settings.hh_user_agent,
            "Accept": "application/json",
        }
        if settings.hh_access_token:
            self.headers["Authorization"] = f"Bearer {settings.hh_access_token}"
        self._access_token = settings.hh_access_token
        self._token_expires_at = float("inf") if settings.hh_access_token else 0.0
        self.last_fetch_errors = 0

    async def _ensure_token(self, client: httpx.AsyncClient) -> None:
        if self._access_token and time.monotonic() < self._token_expires_at - 60:
            client.headers["Authorization"] = f"Bearer {self._access_token}"
            return
        if not self.settings.hh_client_id or not self.settings.hh_client_secret:
            return
        response = await client.post(
            "/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.settings.hh_client_id,
                "client_secret": self.settings.hh_client_secret,
            },
        )
        response.raise_for_status()
        data = response.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.monotonic() + int(data.get("expires_in", 3600))
        authorization = f"Bearer {self._access_token}"
        self.headers["Authorization"] = authorization
        client.headers["Authorization"] = authorization

    async def _request(
        self, client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        await self._ensure_token(client)
        for attempt in range(3):
            try:
                response = await client.get(path, params=params)
                if response.status_code == 403:
                    raise RuntimeError(
                        "HH API вернул 403. Зарегистрируйте приложение и задайте "
                        "HH_CLIENT_ID/HH_CLIENT_SECRET или HH_ACCESS_TOKEN в Railway Variables."
                    )
                if response.status_code == 429:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        raise RuntimeError(f"HH API недоступен: {last_error}")

    async def fetch_recent(
        self, max_pages: int = 2, *, period_days: int = 1
    ) -> list[VacancyCandidate]:
        candidates: dict[str, VacancyCandidate] = {}
        self.last_fetch_errors = 0
        async with httpx.AsyncClient(
            base_url=HH_API_URL,
            headers=self.headers,
            timeout=httpx.Timeout(20),
        ) as client:
            for track, queries in TRACK_QUERIES.items():
                for query in queries:
                    for page in range(max_pages):
                        try:
                            data = await self._request(
                                client,
                                "/vacancies",
                                {
                                    "text": query,
                                    "work_format": ["REMOTE", "HYBRID"],
                                    "search_field": "name",
                                    "period": period_days,
                                    "order_by": "publication_time",
                                    "per_page": 50,
                                    "page": page,
                                },
                            )
                        except RuntimeError:
                            self.last_fetch_errors += 1
                            LOGGER.exception("Поиск HH прервался: %s, page=%s", query, page)
                            break
                        items = data.get("items", [])
                        for short_item in items:
                            vacancy_id = str(short_item.get("id", ""))
                            if not vacancy_id or vacancy_id in candidates:
                                continue
                            try:
                                full_item = await self._request(
                                    client, f"/vacancies/{vacancy_id}"
                                )
                            except RuntimeError:
                                self.last_fetch_errors += 1
                                LOGGER.exception(
                                    "Не удалось получить вакансию HH %s", vacancy_id
                                )
                                continue
                            if full_item.get("archived") or hh_work_format(
                                full_item
                            ) not in {"remote", "hybrid"}:
                                continue
                            candidates[vacancy_id] = normalize_hh_vacancy(
                                full_item, track
                            )
                        if page + 1 >= int(data.get("pages", 0)) or not items:
                            break
        return list(candidates.values())


async def get_vacancies() -> list[VacancyCandidate]:
    """Совместимый вход для старого кода и ручных проверок."""
    return await HHClient(Settings.from_env()).fetch_recent()
