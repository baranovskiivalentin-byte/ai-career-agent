from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from hh_api import VacancyCandidate, parse_datetime, strip_html


LOGGER = logging.getLogger(__name__)
HABR_RSS_URL = "https://career.habr.com/vacancies/rss"
MIN_CACHE_TTL_SECONDS = 30 * 60

ROLE_PATTERNS = (
    r"\b(?:senior\s+|technical\s+|it\s+|ai(?:/ml)?\s+)?project\s+manager\b",
    r"\b(?:ai\s+)?program(?:me)?\s+manager\b",
    r"\bdelivery\s+manager\b",
    r"\bimplementation\s+(?:manager|lead)\b",
    r"\btransformation\s+(?:manager|lead)\b",
    r"\bpmo\s+(?:lead|head|manager)\b",
    r"\boperations?\s+lead\b",
    r"\bchief\s+of\s+staff\b",
    r"\bруководител[ья]\s+(?:it[- ]?)?проект(?:а|ов|ами)?\b",
    r"\bменеджер\s+(?:it[- ]?)?проект(?:а|ов)?\b",
    r"\bпроектн(?:ый|ого)\s+менеджер\b",
    r"\bруководител[ья]\s+программ(?:ы|ами)?\b",
    r"\bруководител[ья]\s+(?:проектов\s+)?внедрения\b",
    r"\bруководител[ья]\s+(?:цифровой\s+)?трансформации\b",
    r"\bруководител[ья]\s+проектного\s+офиса\b",
    r"\bдиректор\s+по\s+проектам\b",
)
ROLE_RE = re.compile("|".join(ROLE_PATTERNS), re.IGNORECASE)
ENTERPRISE_RE = re.compile(
    r"\b(?:enterprise|erp|sap|epc|implementation|transformation|внедрен|"
    r"интеграц|цифров\w*\s+трансформац)\w*",
    re.IGNORECASE,
)
VACANCY_ID_RE = re.compile(r"/vacancies/(\d+)(?:\b|/)")
REMOTE_LABEL_RE = re.compile(r"\bМожно\s+удал[её]нно\b", re.IGNORECASE)
SALARY_NUMBER = r"(?:\d{1,3}(?:[\s\u00a0]\d{3})+|\d{2,9})"
SALARY_CURRENCY = r"(?:₽|руб(?:\.|лей)?|RUB|RUR|\$|USD|€|EUR)"


@dataclass(frozen=True, slots=True)
class HabrRssEntry:
    url: str
    title: str
    published_at: datetime | None


def is_target_role(title: str) -> bool:
    """Return True only for the agreed PM/Program/Delivery leadership roles."""
    return bool(title and ROLE_RE.search(title))


def _parse_feed_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value.strip())
    if parsed is not None:
        return parsed.astimezone(timezone.utc)
    try:
        result = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, OverflowError):
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in node:
        if _local_name(child.tag) not in wanted:
            continue
        text = "".join(child.itertext()).strip()
        if text:
            return text
        href = child.attrib.get("href", "").strip()
        if href:
            return href
    return ""


def parse_habr_rss(xml_text: str) -> list[HabrRssEntry]:
    """Parse RSS or Atom without network access and keep only vacancy URLs."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        raise ValueError("Habr Career returned invalid RSS/Atom XML") from error

    entries: list[HabrRssEntry] = []
    seen: set[str] = set()
    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        title = _child_text(node, "title")
        raw_url = _child_text(node, "link", "guid", "id")
        url = urljoin(HABR_RSS_URL, raw_url)
        if not VACANCY_ID_RE.search(url) or url in seen:
            continue
        seen.add(url)
        entries.append(
            HabrRssEntry(
                url=url,
                title=title,
                published_at=_parse_feed_datetime(
                    _child_text(node, "pubDate", "published", "updated")
                ),
            )
        )
    return entries


def _iter_job_postings(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _iter_job_postings(item)
        return
    if not isinstance(value, dict):
        return
    raw_type = value.get("@type")
    types = raw_type if isinstance(raw_type, list) else [raw_type]
    if any(str(item).lower() == "jobposting" for item in types):
        yield value
    for key in ("@graph", "mainEntity", "itemListElement"):
        if key in value:
            yield from _iter_job_postings(value[key])


def _job_posting(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        posting = next(_iter_job_postings(payload), None)
        if posting is not None:
            return posting
    return None


def _first_text(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node is None:
            continue
        if isinstance(node, Tag) and node.name == "meta":
            value = str(node.get("content") or "").strip()
        else:
            value = node.get_text("\n", strip=True)
        if value:
            return value
    return ""


def _organization_name(posting: dict[str, Any] | None, soup: BeautifulSoup) -> str:
    organization = (posting or {}).get("hiringOrganization")
    if isinstance(organization, list):
        organization = next((row for row in organization if isinstance(row, dict)), None)
    if isinstance(organization, dict) and organization.get("name"):
        return str(organization["name"]).strip()
    return _first_text(
        soup,
        (
            '[itemprop="hiringOrganization"] [itemprop="name"]',
            '[itemprop="hiringOrganization"]',
            ".vacancy-company__title",
            ".vacancy-company-name",
            '[data-test="company-name"]',
        ),
    ) or "Не указана"


def _description(posting: dict[str, Any] | None, soup: BeautifulSoup) -> str:
    raw = (posting or {}).get("description")
    if raw:
        cleaned = strip_html(str(raw))
        return re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    raw = _first_text(
        soup,
        (
            '[itemprop="description"]',
            "#vacancy-description",
            ".vacancy-description",
            ".vacancy-description__text",
            '[data-test="vacancy-description"]',
        ),
    )
    return re.sub(r"\s+", " ", raw).strip()


def _currency(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if raw in {"₽", "РУБ", "РУБ.", "РУБЛЕЙ", "RUB", "RUR"}:
        return "RUR"
    if raw in {"$", "USD"}:
        return "USD"
    if raw in {"€", "EUR"}:
        return "EUR"
    return raw or None


def _number(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return round(float(str(value).replace("\u00a0", "").replace(" ", "")))
    except (TypeError, ValueError):
        return None


def _monthly(value: int | None, unit: str) -> int | None:
    if value is None:
        return None
    normalized = unit.strip().upper()
    if normalized in {"YEAR", "YEARLY", "ANNUAL", "P1Y"}:
        return round(value / 12)
    if normalized in {"WEEK", "WEEKLY"}:
        return round(value * 52 / 12)
    return value


def _json_salary(posting: dict[str, Any] | None) -> tuple[int | None, int | None, str | None]:
    salary = (posting or {}).get("baseSalary")
    if isinstance(salary, list):
        salary = next((row for row in salary if isinstance(row, dict)), None)
    if not isinstance(salary, dict):
        return None, None, None
    currency = _currency(salary.get("currency"))
    value = salary.get("value", salary)
    if not isinstance(value, dict):
        amount = _number(value)
        return amount, amount, currency
    unit = str(value.get("unitText") or salary.get("unitText") or "MONTH")
    salary_from = _number(value.get("minValue"))
    salary_to = _number(value.get("maxValue"))
    exact = _number(value.get("value"))
    if salary_from is None and salary_to is None and exact is not None:
        salary_from = salary_to = exact
    return _monthly(salary_from, unit), _monthly(salary_to, unit), currency


def _parse_salary_text(text: str) -> tuple[int | None, int | None, str | None]:
    normalized = re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()
    if not normalized or "зарплата не указана" in normalized.lower():
        return None, None, None
    currency_match = re.search(SALARY_CURRENCY, normalized, re.IGNORECASE)
    if currency_match is None:
        return None, None, None
    currency = _currency(currency_match.group(0))
    numbers = [_number(row) for row in re.findall(SALARY_NUMBER, normalized)]
    values = [row for row in numbers if row is not None]
    if not values:
        return None, None, None
    lower = normalized.lower()
    if len(values) >= 2:
        return values[0], values[1], currency
    if re.search(r"(?:^|\s)до\s", lower):
        return None, values[0], currency
    return values[0], None, currency


def _html_salary(soup: BeautifulSoup) -> tuple[int | None, int | None, str | None]:
    selectors = (
        '[itemprop="baseSalary"]',
        ".vacancy-header__salary",
        ".vacancy-salary",
        ".basic-salary",
        '[data-test="vacancy-salary"]',
    )
    for selector in selectors:
        node = soup.select_one(selector)
        if node is None:
            continue
        parsed = _parse_salary_text(node.get_text(" ", strip=True))
        if parsed != (None, None, None):
            return parsed
    return None, None, None


def _location_name(value: Any) -> list[str]:
    if isinstance(value, list):
        result: list[str] = []
        for row in value:
            result.extend(_location_name(row))
        return result
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, dict):
        return []
    address = value.get("address")
    if isinstance(address, dict):
        parts = [
            str(address.get(key) or "").strip()
            for key in ("addressLocality", "addressRegion", "addressCountry")
        ]
        joined = ", ".join(dict.fromkeys(part for part in parts if part))
        if joined:
            return [joined]
    name = str(value.get("name") or "").strip()
    return [name] if name else []


def _location(posting: dict[str, Any] | None, soup: BeautifulSoup) -> str | None:
    rows: list[str] = []
    if posting:
        rows.extend(_location_name(posting.get("applicantLocationRequirements")))
        rows.extend(_location_name(posting.get("jobLocation")))
    if not rows:
        fallback = _first_text(
            soup,
            (
                '[itemprop="jobLocation"]',
                ".vacancy-location",
                '[data-test="vacancy-location"]',
            ),
        )
        if fallback:
            rows.append(fallback)
    cleaned = list(dict.fromkeys(row for row in rows if row and row != "Можно удалённо"))
    return "; ".join(cleaned) or None


def _is_remote(posting: dict[str, Any] | None, soup: BeautifulSoup) -> bool:
    raw = (posting or {}).get("jobLocationType")
    values = raw if isinstance(raw, list) else [raw]
    if any(str(value or "").strip().upper() == "TELECOMMUTE" for value in values):
        return True
    return bool(REMOTE_LABEL_RE.search(soup.get_text(" ", strip=True)))


def _published_at(
    posting: dict[str, Any] | None,
    soup: BeautifulSoup,
    fallback: datetime | None,
) -> datetime | None:
    parsed = parse_datetime(str((posting or {}).get("datePosted") or ""))
    if parsed is not None:
        return parsed
    node = soup.select_one('time[datetime], [itemprop="datePosted"]')
    if node is not None:
        raw = node.get("datetime") or node.get("content") or node.get_text(strip=True)
        parsed = _parse_feed_datetime(str(raw or ""))
        if parsed is not None:
            return parsed
    return fallback


def parse_habr_vacancy(
    html: str,
    url: str,
    *,
    published_at: datetime | None = None,
) -> VacancyCandidate | None:
    """Normalize a public Habr Career card; non-target/non-remote cards are skipped."""
    soup = BeautifulSoup(html, "html.parser")
    posting = _job_posting(soup)
    title = str((posting or {}).get("title") or "").strip() or _first_text(
        soup, ('h1[itemprop="title"]', "h1")
    )
    if not is_target_role(title) or not _is_remote(posting, soup):
        return None
    description = _description(posting, soup)
    if not description:
        LOGGER.warning("Habr vacancy has no full description: %s", url)
        return None

    company = _organization_name(posting, soup)
    salary_from, salary_to, currency = _json_salary(posting)
    if salary_from is None and salary_to is None:
        salary_from, salary_to, currency = _html_salary(soup)
    normalized_url = url.split("#", 1)[0]
    vacancy_id = VACANCY_ID_RE.search(urlparse(normalized_url).path)
    external_id = vacancy_id.group(1) if vacancy_id else hashlib.sha256(
        normalized_url.encode("utf-8")
    ).hexdigest()
    content_hash = hashlib.sha256(
        f"{title}|{company}|{description}".encode("utf-8")
    ).hexdigest()
    track = "enterprise_epc" if ENTERPRISE_RE.search(f"{title} {description}") else "senior_it"

    return VacancyCandidate(
        source="habr",
        external_id=external_id,
        track=track,
        title=title,
        company=company,
        description=description[:20_000],
        salary_from=salary_from,
        salary_to=salary_to,
        currency=currency,
        work_format="remote",
        location=_location(posting, soup),
        published_at=_published_at(posting, soup, published_at),
        url=normalized_url,
        content_hash=content_hash,
    )


class HabrCareerSource:
    """RSS-triggered reader for public Habr Career vacancy cards."""

    def __init__(
        self,
        settings: Any | None = None,
        *,
        rss_url: str = HABR_RSS_URL,
        max_vacancies: int = 100,
        max_card_attempts: int = 30,
        concurrency: int = 4,
        ttl_seconds: float = MIN_CACHE_TTL_SECONDS,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ):
        self.rss_url = rss_url
        self.max_vacancies = max(1, max_vacancies)
        self.max_card_attempts = max(1, max_card_attempts)
        self.concurrency = max(1, min(concurrency, 8))
        self.ttl_seconds = max(float(ttl_seconds), MIN_CACHE_TTL_SECONDS)
        self.transport = transport
        self.user_agent = getattr(settings, "hh_user_agent", "AI-Career-Agent/1.0")
        self._cached: tuple[VacancyCandidate, ...] | None = None
        self._cache_expires_at = 0.0
        self._lock = asyncio.Lock()

    async def fetch_recent(self, *, force_refresh: bool = False) -> list[VacancyCandidate]:
        now = time.monotonic()
        if not force_refresh and self._cached is not None and now < self._cache_expires_at:
            return list(self._cached)

        async with self._lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self._cached is not None
                and now < self._cache_expires_at
            ):
                return list(self._cached)

            headers = {
                "User-Agent": self.user_agent,
                "Accept": "application/rss+xml, application/xml, text/html;q=0.9",
                "Accept-Language": "ru,en;q=0.8",
            }
            async with httpx.AsyncClient(
                headers=headers,
                follow_redirects=True,
                timeout=httpx.Timeout(20.0),
                transport=self.transport,
            ) as client:
                response = await client.get(self.rss_url)
                response.raise_for_status()
                entries = [
                    entry
                    for entry in parse_habr_rss(response.text)
                    if not entry.title or is_target_role(entry.title)
                ][: self.max_card_attempts]
                semaphore = asyncio.Semaphore(self.concurrency)

                async def read_card(entry: HabrRssEntry) -> VacancyCandidate | None:
                    try:
                        async with semaphore:
                            card = await client.get(entry.url)
                            card.raise_for_status()
                    except httpx.HTTPError:
                        LOGGER.exception(
                            "Не удалось прочитать вакансию Habr Career: %s",
                            entry.url,
                        )
                        return None
                    return parse_habr_vacancy(
                        card.text,
                        str(card.url),
                        published_at=entry.published_at,
                    )

                rows = await asyncio.gather(*(read_card(entry) for entry in entries))

            candidates: list[VacancyCandidate] = []
            seen: set[str] = set()
            for candidate in rows:
                if candidate is None or candidate.external_id in seen:
                    continue
                seen.add(candidate.external_id)
                candidates.append(candidate)
                if len(candidates) >= self.max_vacancies:
                    break

            self._cached = tuple(candidates)
            self._cache_expires_at = time.monotonic() + self.ttl_seconds
            return list(candidates)
