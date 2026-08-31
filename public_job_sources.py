from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

import httpx

from hh_api import VacancyCandidate, parse_datetime, strip_html


HIMALAYAS_API_URL = "https://himalayas.app/jobs/api"
JOBICY_API_URL = "https://jobicy.com/api/v2/remote-jobs"
DEFAULT_CACHE_TTL_SECONDS = 60 * 60

_ROLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bproject\s+manager\b",
        r"\bprogram(?:me)?\s+manager\b",
        r"\bdelivery\s+manager\b",
        r"\bimplementation\s+(?:manager|lead)\b",
        r"\btransformation\s+(?:manager|lead)\b",
        r"\b(?:ai|artificial\s+intelligence)\s+(?:project\s+)?manager\b",
        r"\bai(?:/ml)?\s+pm\b",
        r"\bpmo\s+(?:lead|manager|head|director)\b",
        r"\boperations\s+(?:lead|head)\b",
        r"\bchief\s+of\s+staff\b",
        r"\bруководител[ья]\s+(?:проекта|проектов|программ)\b",
        r"\bдиректор\s+программ(?:ы)?\b",
        r"\bруководитель\s+pmo\b",
    )
)
_NON_TARGET_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bproduct\s+manager\b",
        r"\bproject\s+(?:coordinator|assistant|administrator)\b",
        r"\b(?:translation|localization)\s+project\s+manager\b",
        r"\bjunior\s+project\s+manager\b",
        r"\bintern(?:ship)?\b",
        r"\bстаж[её]р\b",
        r"\bассистент\s+руководителя\s+проекта\b",
    )
)
_ENTERPRISE_TERMS = (
    "enterprise",
    "implementation",
    "transformation",
    "pmo",
    "erp",
    "sap",
    "epc",
    "capex",
    "digital transformation",
    "system integration",
    "внедрен",
    "трансформац",
    "нефтегаз",
    "интеграц",
)
_GLOBAL_LOCATIONS = (
    "anywhere",
    "worldwide",
    "world wide",
    "global",
    "globally",
)
_RUSSIA_LOCATIONS = ("russia", "russian federation", "россия", "рф")
_MOBILITY_TERMS = (
    "relocation",
    "relocate",
    "visa sponsorship",
    "visa sponsor",
    "sponsor a visa",
    "sponsorship available",
    "work permit sponsorship",
    "contractor",
    "independent contract",
    "b2b contract",
    "b2b agreement",
    "contract basis",
    "контрактор",
    "релокац",
)
_ON_SITE_TERMS = (
    "onsite only",
    "on-site only",
    "office-based",
    "office based",
    "hybrid role",
    "hybrid position",
)


def is_target_title(title: str | None) -> bool:
    """Return True only for explicitly supported management titles."""
    normalized = " ".join((title or "").split())
    if not normalized or any(pattern.search(normalized) for pattern in _NON_TARGET_TITLE_PATTERNS):
        return False
    return any(pattern.search(normalized) for pattern in _ROLE_PATTERNS)


def classify_track(title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    return "enterprise_epc" if any(term in text for term in _ENTERPRISE_TERMS) else "senior_it"


def normalize_monthly_salary(
    value: Any,
    period: str | None,
    *,
    annual_by_field: bool = False,
) -> int | None:
    """Normalize a numeric salary to a monthly value without FX conversion."""
    if value in (None, ""):
        return None
    try:
        amount = float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None

    normalized_period = (period or "").strip().lower()
    if annual_by_field and not normalized_period:
        normalized_period = "year"
    if normalized_period in {"year", "yearly", "annual", "annually", "per year", "yr"}:
        amount /= 12
    elif normalized_period in {"week", "weekly", "per week", "wk"}:
        amount *= 52 / 12
    elif normalized_period in {"day", "daily", "per day"}:
        amount *= 5 * 52 / 12
    elif normalized_period in {"hour", "hourly", "per hour", "hr"}:
        amount *= 40 * 52 / 12
    return round(amount)


def _currency(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()
    aliases = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "₽": "RUR", "RUB": "RUR"}
    return aliases.get(normalized, normalized or None)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,;|]", value) if part.strip()]
    if isinstance(value, dict):
        for key in ("name", "label", "value", "country"):
            if value.get(key):
                return [str(value[key]).strip()]
        return []
    if isinstance(value, Iterable):
        result: list[str] = []
        for row in value:
            result.extend(_string_list(row))
        return result
    return [str(value).strip()]


def is_allowed_location(restrictions: Any, searchable_text: str) -> bool:
    """Apply the global/Russia-first policy to structured location restrictions."""
    locations = _string_list(restrictions)
    if not locations:
        return True
    normalized_locations = " ".join(locations).lower()
    if any(term in normalized_locations for term in (*_GLOBAL_LOCATIONS, *_RUSSIA_LOCATIONS)):
        return True
    normalized_text = searchable_text.lower()
    return any(term in normalized_text for term in _MOBILITY_TERMS)


def _is_remote(item: dict[str, Any], description: str) -> bool:
    remote_value = item.get("remote")
    if remote_value is False:
        return False
    structured = " ".join(
        str(item.get(key) or "")
        for key in ("workplaceType", "workplace", "workFormat", "remoteType")
    ).lower()
    if structured and any(term in structured for term in ("onsite", "on-site", "hybrid")):
        return False
    text = description.lower()
    return not any(term in text for term in _ON_SITE_TERMS)


def _canonical_id(source: str, raw_id: Any, url: str) -> str:
    stable_id = str(raw_id or "").strip()
    if not stable_id:
        stable_id = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:24]
    if stable_id.lower().startswith(f"{source}:"):
        return stable_id
    return f"{source}:{stable_id}"


def _content_hash(title: str, company: str, description: str) -> str:
    return hashlib.sha256(f"{title}|{company}|{description}".encode("utf-8")).hexdigest()


def _payload_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("jobs", "data", "results", "items"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def parse_himalayas_payload(payload: Any) -> list[VacancyCandidate]:
    candidates: dict[str, VacancyCandidate] = {}
    for item in _payload_rows(payload):
        title = str(item.get("title") or item.get("jobTitle") or "").strip()
        if not is_target_title(title):
            continue
        description = strip_html(item.get("description") or item.get("jobDescription"))
        if not description or not _is_remote(item, description):
            continue
        restrictions = item.get("locationRestrictions")
        searchable = " ".join(
            str(item.get(key) or "")
            for key in ("title", "description", "employmentType", "jobType")
        )
        if not is_allowed_location(restrictions, searchable):
            continue

        company = str(item.get("companyName") or item.get("company") or "Не указана").strip()
        url = str(
            item.get("applicationLink")
            or item.get("url")
            or item.get("jobUrl")
            or ""
        ).strip()
        external_id = _canonical_id(
            "himalayas", item.get("guid") or item.get("id") or item.get("slug"), url
        )
        period = item.get("salaryPeriod") or item.get("salaryInterval")
        location = ", ".join(_string_list(restrictions)) or None
        candidates[external_id] = VacancyCandidate(
            source="himalayas",
            external_id=external_id,
            track=classify_track(title, description),
            title=title,
            company=company,
            description=description,
            salary_from=normalize_monthly_salary(item.get("minSalary"), period),
            salary_to=normalize_monthly_salary(item.get("maxSalary"), period),
            currency=_currency(item.get("currency") or item.get("salaryCurrency")),
            work_format="remote",
            location=location,
            published_at=parse_datetime(
                item.get("pubDate") or item.get("publishedAt") or item.get("createdAt")
            ),
            url=url,
            content_hash=_content_hash(title, company, description),
        )
    return list(candidates.values())


def parse_jobicy_payload(payload: Any) -> list[VacancyCandidate]:
    candidates: dict[str, VacancyCandidate] = {}
    for item in _payload_rows(payload):
        title = str(item.get("jobTitle") or item.get("title") or "").strip()
        if not is_target_title(title):
            continue
        description = strip_html(item.get("jobDescription") or item.get("description"))
        if not description or not _is_remote(item, description):
            continue
        restrictions = item.get("jobGeo") or item.get("locationRestrictions")
        searchable = " ".join(
            str(item.get(key) or "")
            for key in ("jobTitle", "jobDescription", "jobType", "employmentType")
        )
        if not is_allowed_location(restrictions, searchable):
            continue

        company = str(item.get("companyName") or item.get("company") or "Не указана").strip()
        url = str(item.get("url") or item.get("jobUrl") or "").strip()
        external_id = _canonical_id(
            "jobicy", item.get("id") or item.get("jobId") or item.get("jobSlug"), url
        )
        period = item.get("salaryPeriod") or item.get("salaryInterval")
        salary_from = normalize_monthly_salary(
            item.get("annualSalaryMin") if "annualSalaryMin" in item else item.get("salaryMin"),
            period,
            annual_by_field="annualSalaryMin" in item,
        )
        salary_to = normalize_monthly_salary(
            item.get("annualSalaryMax") if "annualSalaryMax" in item else item.get("salaryMax"),
            period,
            annual_by_field="annualSalaryMax" in item,
        )
        location = ", ".join(_string_list(restrictions)) or None
        candidates[external_id] = VacancyCandidate(
            source="jobicy",
            external_id=external_id,
            track=classify_track(title, description),
            title=title,
            company=company,
            description=description,
            salary_from=salary_from,
            salary_to=salary_to,
            currency=_currency(item.get("salaryCurrency") or item.get("currency")),
            work_format="remote",
            location=location,
            published_at=parse_datetime(
                item.get("pubDate") or item.get("publishedAt") or item.get("createdAt")
            ),
            url=url,
            content_hash=_content_hash(title, company, description),
        )
    return list(candidates.values())


class _CachedPublicSource:
    def __init__(
        self,
        *,
        api_url: str,
        parser: Callable[[Any], list[VacancyCandidate]],
        params: dict[str, Any],
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_url = api_url
        self.parser = parser
        self.params = params
        self.cache_ttl_seconds = max(float(cache_ttl_seconds), 0.0)
        self.transport = transport
        self._cached_at: float | None = None
        self._cached_results: tuple[VacancyCandidate, ...] = ()
        self._lock = asyncio.Lock()

    async def fetch_recent(self) -> list[VacancyCandidate]:
        now = time.monotonic()
        if self._cached_at is not None and now - self._cached_at < self.cache_ttl_seconds:
            return [replace(row) for row in self._cached_results]

        async with self._lock:
            now = time.monotonic()
            if self._cached_at is not None and now - self._cached_at < self.cache_ttl_seconds:
                return [replace(row) for row in self._cached_results]
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=httpx.Timeout(20),
                follow_redirects=True,
                headers={"Accept": "application/json", "User-Agent": "AI-Career-Agent/1.0"},
            ) as client:
                response = await client.get(self.api_url, params=self.params)
                response.raise_for_status()
                results = self.parser(response.json())
            self._cached_results = tuple(results)
            self._cached_at = time.monotonic()
            return [replace(row) for row in self._cached_results]


class HimalayasSource(_CachedPublicSource):
    def __init__(
        self,
        *,
        limit: int = 100,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        super().__init__(
            api_url=HIMALAYAS_API_URL,
            parser=parse_himalayas_payload,
            params={"limit": max(1, min(int(limit), 100))},
            cache_ttl_seconds=cache_ttl_seconds,
            transport=transport,
        )


class JobicySource(_CachedPublicSource):
    def __init__(
        self,
        *,
        count: int = 100,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        super().__init__(
            api_url=JOBICY_API_URL,
            parser=parse_jobicy_payload,
            params={"count": max(1, min(int(count), 200))},
            cache_ttl_seconds=cache_ttl_seconds,
            transport=transport,
        )


class PublicJobSources:
    """Aggregate both public remote-job feeds behind one monitor-friendly method."""

    def __init__(
        self,
        *,
        himalayas: HimalayasSource | None = None,
        jobicy: JobicySource | None = None,
    ):
        self.himalayas = himalayas or HimalayasSource()
        self.jobicy = jobicy or JobicySource()

    async def fetch_recent(self) -> list[VacancyCandidate]:
        batches = await asyncio.gather(
            self.himalayas.fetch_recent(),
            self.jobicy.fetch_recent(),
        )
        return [candidate for batch in batches for candidate in batch]
