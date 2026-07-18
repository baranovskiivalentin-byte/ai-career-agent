from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from datetime import datetime, timezone
from email.header import decode_header
from typing import Any
from urllib.parse import unquote

from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import Settings
from hh_api import VacancyCandidate


JOB_URL_RE = re.compile(
    r"https?://[^\s\"'<>]+(?:linkedin\.com/(?:jobs/view|comm/jobs/view)|jobs)[^\s\"'<>]*",
    re.IGNORECASE,
)
REMOTE_WORDS = (
    "remote",
    "удаленная",
    "удалённая",
    "работа из дома",
)


def _decode_header(value: str) -> str:
    parts = []
    for data, encoding in decode_header(value):
        if isinstance(data, bytes):
            parts.append(data.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(data)
    return "".join(parts)


def _content(payload: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    chunks: list[str] = []
    links: list[tuple[str, str]] = []
    data = (payload.get("body") or {}).get("data")
    if data:
        decoded = base64.urlsafe_b64decode(data + "===").decode(
            "utf-8", errors="replace"
        )
        if payload.get("mimeType") == "text/html":
            soup = BeautifulSoup(decoded, "html.parser")
            links.extend(
                (anchor.get_text(" ", strip=True), str(anchor.get("href")))
                for anchor in soup.find_all("a", href=True)
            )
            decoded = soup.get_text(" ")
        else:
            links.extend(("", url) for url in JOB_URL_RE.findall(decoded))
        chunks.append(decoded)
    for part in payload.get("parts") or []:
        part_text, part_links = _content(part)
        chunks.append(part_text)
        links.extend(part_links)
    return " ".join(chunks), links


def parse_gmail_message(message: dict[str, Any]) -> list[VacancyCandidate]:
    payload = message.get("payload") or {}
    headers = {
        row.get("name", "").lower(): row.get("value", "")
        for row in payload.get("headers") or []
    }
    subject = _decode_header(headers.get("subject", "Вакансии LinkedIn"))
    raw_text, raw_links = _content(payload)
    text = re.sub(r"\s+", " ", raw_text).strip()
    lowered = f"{subject} {text}".lower()
    if not any(word in lowered for word in REMOTE_WORDS):
        return []
    link_rows = [
        (title, unquote(url.rstrip(".,)")))
        for title, url in raw_links
        if "linkedin.com" in url.lower()
        and ("/jobs/" in url.lower() or "job" in url.lower())
    ]
    if not link_rows:
        link_rows = [("", url) for url in JOB_URL_RE.findall(text)]
    unique_links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for title, url in link_rows:
        if url not in seen:
            unique_links.append((title, url))
            seen.add(url)
    if not unique_links:
        unique_links = [(subject, "https://www.linkedin.com/jobs/")]
    message_id = str(message.get("id") or hashlib.sha256(text.encode()).hexdigest())
    track = (
        "senior_it"
        if any(
            word in lowered
            for word in ("delivery", "it project", "program manager", "ai project")
        )
        else "enterprise_epc"
    )
    return [
        VacancyCandidate(
            source="linkedin_email",
            external_id=f"{message_id}:{index}",
            track=track,
            title=(link_title or subject)[:500],
            company="Из уведомления LinkedIn",
            description=text[:20000],
            salary_from=None,
            salary_to=None,
            currency=None,
            work_format="remote",
            location=None,
            published_at=datetime.now(timezone.utc),
            url=url,
            content_hash=hashlib.sha256(
                f"{subject}|{url}|{text}".encode("utf-8")
            ).hexdigest(),
        )
        for index, (link_title, url) in enumerate(unique_links)
    ]


class GmailLinkedInSource:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _fetch_sync(self) -> list[VacancyCandidate]:
        if not all(
            [
                self.settings.gmail_client_id,
                self.settings.gmail_client_secret,
                self.settings.gmail_refresh_token,
            ]
        ):
            return []
        credentials = Credentials(
            token=None,
            refresh_token=self.settings.gmail_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.settings.gmail_client_id,
            client_secret=self.settings.gmail_client_secret,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        result = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=f'label:"{self.settings.gmail_label}" newer_than:2d',
                maxResults=50,
            )
            .execute()
        )
        candidates: list[VacancyCandidate] = []
        for row in result.get("messages", []):
            message = (
                service.users()
                .messages()
                .get(userId="me", id=row["id"], format="full")
                .execute()
            )
            candidates.extend(parse_gmail_message(message))
        return candidates

    async def fetch_recent(self) -> list[VacancyCandidate]:
        return await asyncio.to_thread(self._fetch_sync)
