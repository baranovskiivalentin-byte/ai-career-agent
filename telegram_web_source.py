from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from config import Settings
from hh_api import VacancyCandidate
from vacancy_manager import VacancyRepository


LOGGER = logging.getLogger(__name__)

ROLE_PATTERNS = (
    r"\b(?:senior\s+|technical\s+|it\s+|ai\s+)?project\s+manager\b",
    r"\bprogram\s+manager\b",
    r"\bdelivery\s+manager\b",
    r"\bhead\s+of\s+(?:pmo|projects?|delivery)\b",
    r"\bchief\s+of\s+staff\b",
    r"\boperations?\s+(?:lead|manager|director)\b",
    r"\bруководител[ья]\s+(?:it[- ]?)?проект",
    r"\bменеджер\s+(?:it[- ]?)?проект",
    r"\bпроектн(?:ый|ого)\s+менеджер",
    r"\bруководител[ья]\s+проектного\s+офиса",
    r"\bдиректор\s+по\s+проектам",
)
ROLE_RE = re.compile("|".join(ROLE_PATTERNS), re.IGNORECASE)
REMOTE_RE = re.compile(
    r"\bremote\b|удал[её]н(?:но|ка|ная|ный|ной|ную|ная работа)|работа\s+из\s+дома",
    re.IGNORECASE,
)
RESUME_RE = re.compile(
    r"(?:^|\s)#?(?:резюме|resume|cv|opentowork)(?:\s|$)|\bищу\s+работу\b",
    re.IGNORECASE,
)
ENTERPRISE_RE = re.compile(
    r"\b(?:enterprise|erp|sap|epc|интеграц|внедрен|цифров(?:ая|ой) трансформац)\w*",
    re.IGNORECASE,
)
SALARY_RE = re.compile(
    r"(?:(?P<prefix>₽|RUB|\$|USD|€|EUR)\s*)?"
    r"(?P<from>(?:\d{1,3}(?:[\s\u00a0]\d{3})+|\d{2,6}))"
    r"(?:\s*(?:[-–—]|до)\s*(?P<to>(?:\d{1,3}(?:[\s\u00a0]\d{3})+|\d{2,6})))?"
    r"\s*(?P<suffix>₽|руб(?:\.|лей)?|RUB|\$|USD|€|EUR)?",
    re.IGNORECASE,
)


def normalize_channel(value: str) -> str:
    value = value.strip()
    if "t.me/" in value:
        path = urlparse(value).path.strip("/")
        if path.startswith("s/"):
            path = path[2:]
        value = path.split("/", 1)[0]
    return value.lstrip("@").lower()


def is_target_vacancy(text: str) -> bool:
    if not text or RESUME_RE.search(text):
        return False
    return bool(ROLE_RE.search(text) and REMOTE_RE.search(text))


def _salary(text: str) -> tuple[int | None, int | None, str | None]:
    match = next(
        (
            row
            for row in SALARY_RE.finditer(text)
            if row.group("prefix") or row.group("suffix")
        ),
        None,
    )
    if match is None:
        return None, None, None

    currency_raw = (match.group("prefix") or match.group("suffix") or "").upper()
    currency = (
        "RUR"
        if currency_raw in {"₽", "РУБ", "РУБ.", "РУБЛЕЙ", "RUB"}
        else "USD"
        if currency_raw in {"$", "USD"}
        else "EUR"
    )

    def number(name: str) -> int | None:
        raw = match.group(name)
        if not raw:
            return None
        value = int(re.sub(r"\s|\u00a0", "", raw))
        return value * 1000 if currency == "RUR" and value < 10_000 else value

    return number("from"), number("to"), currency


def _post_datetime(post: Tag) -> datetime:
    time_node = post.select_one("time[datetime]")
    raw = time_node.get("datetime") if time_node else None
    if not raw:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def parse_channel_page(
    html: str,
    channel: str,
    *,
    now: datetime | None = None,
    lookback_hours: int = 72,
    max_posts: int = 5,
) -> list[VacancyCandidate]:
    channel = normalize_channel(channel)
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[VacancyCandidate] = []

    for post in reversed(soup.select("div.tgme_widget_message[data-post]")):
        data_post = str(post.get("data-post") or "")
        if "/" not in data_post:
            continue
        post_channel, post_id = data_post.rsplit("/", 1)
        if not post_id.isdigit():
            continue
        text_node = post.select_one(".tgme_widget_message_text")
        text = text_node.get_text("\n", strip=True) if text_node else ""
        published_at = _post_datetime(post)
        if published_at < cutoff or not is_target_vacancy(text):
            continue

        title = next(
            (line.strip() for line in text.splitlines() if line.strip()),
            "Вакансия из Telegram",
        )
        salary_from, salary_to, currency = _salary(text)
        direct_url = f"https://t.me/{post_channel}/{post_id}"
        track = "enterprise_epc" if ENTERPRISE_RE.search(text) else "senior_it"
        candidates.append(
            VacancyCandidate(
                source="telegram_web",
                external_id=f"{post_channel.lower()}:{post_id}",
                track=track,
                title=title[:500],
                company=f"Telegram: @{post_channel}",
                description=text[:20_000],
                salary_from=salary_from,
                salary_to=salary_to,
                currency=currency,
                work_format="remote",
                location=None,
                published_at=published_at,
                url=direct_url,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
        if len(candidates) >= max_posts:
            break
    return candidates


class TelegramWebSource:
    def __init__(self, settings: Settings, repository: VacancyRepository):
        self.settings = settings
        self.repository = repository

    async def _fetch_channel(
        self, client: httpx.AsyncClient, channel: str
    ) -> list[VacancyCandidate]:
        url = f"https://t.me/s/{channel}"
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = await client.get(url)
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in {429, 500, 502, 503, 504}:
                    raise
                if attempt == 2:
                    raise
            except httpx.TransportError:
                if attempt == 2:
                    raise
            await asyncio.sleep(2**attempt)
        if response is None:
            return []
        if "/s/" not in str(response.url):
            LOGGER.warning(
                "Публичная лента @%s недоступна: перенаправление на %s",
                channel,
                response.url,
            )
            return []
        return parse_channel_page(
            response.text,
            channel,
            lookback_hours=self.settings.telegram_web_lookback_hours,
            max_posts=self.settings.telegram_web_max_posts_per_channel,
        )

    async def fetch_recent(self) -> list[VacancyCandidate]:
        channels = [normalize_channel(row) for row in self.repository.list_sources()]
        channels = list(dict.fromkeys(channel for channel in channels if channel))
        if not channels:
            return []

        headers = {
            "User-Agent": self.settings.hh_user_agent,
            "Accept-Language": "ru,en;q=0.8",
        }
        candidates: list[VacancyCandidate] = []
        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(20.0),
        ) as client:
            for index, channel in enumerate(channels):
                if index:
                    await asyncio.sleep(0.4)
                try:
                    candidates.extend(await self._fetch_channel(client, channel))
                except (httpx.HTTPError, ValueError):
                    LOGGER.exception("Не удалось прочитать Telegram-канал @%s", channel)
        return candidates
