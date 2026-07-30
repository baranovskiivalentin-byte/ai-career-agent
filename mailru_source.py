from __future__ import annotations

import asyncio
import imaplib
import logging
import re
import ssl
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from typing import Any

from bs4 import BeautifulSoup

from config import Settings
from gmail_source import parse_email_alert
from hh_api import VacancyCandidate

LOGGER = logging.getLogger(__name__)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
IMAP_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
ImapFactory = Callable[..., Any]


def _imap_date(value: datetime) -> str:
    return f"{value.day:02d}-{IMAP_MONTHS[value.month - 1]}-{value.year}"


def _message_content(message: Message) -> tuple[str, list[tuple[str, str]]]:
    text_parts: list[str] = []
    links: list[tuple[str, str]] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(
                part.get_content_charset() or "utf-8",
                errors="replace",
            )
        if not isinstance(content, str):
            content = str(content)
        if content_type == "text/html":
            soup = BeautifulSoup(content, "html.parser")
            links.extend(
                (anchor.get_text(" ", strip=True), str(anchor.get("href")))
                for anchor in soup.find_all("a", href=True)
            )
            text_parts.append(soup.get_text(" ", strip=True))
        else:
            links.extend(("", match.group(0)) for match in URL_RE.finditer(content))
            text_parts.append(content)
    text = re.sub(r"\s+", " ", " ".join(text_parts)).strip()
    return text, links


def parse_mailru_message(raw_message: bytes, *, uid: str) -> list[VacancyCandidate]:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    subject = str(message.get("Subject") or "Вакансии")
    text, links = _message_content(message)
    message_id = str(message.get("Message-ID") or f"mailru:{uid}")
    return parse_email_alert(
        message_id=message_id,
        subject=subject,
        text=text,
        raw_links=links,
    )


class MailRuJobAlertsSource:
    HOST = "imap.mail.ru"
    PORT = 993

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ImapFactory = imaplib.IMAP4_SSL,
    ):
        self.settings = settings
        self.client_factory = client_factory

    def _fetch_sync(self) -> list[VacancyCandidate]:
        if not self.settings.mailru_email or not self.settings.mailru_app_password:
            return []
        client = self.client_factory(
            self.HOST,
            self.PORT,
            ssl_context=ssl.create_default_context(),
            timeout=20,
        )
        try:
            client.login(
                self.settings.mailru_email,
                self.settings.mailru_app_password,
            )
            status, _ = client.select(self.settings.mailru_folder, readonly=True)
            if status != "OK":
                raise RuntimeError(
                    f"Mail.ru не открыл папку {self.settings.mailru_folder}"
                )
            since = datetime.now(timezone.utc) - timedelta(
                days=max(1, self.settings.mailru_lookback_days)
            )
            status, rows = client.uid("search", None, "SINCE", _imap_date(since))
            if status != "OK":
                raise RuntimeError("Mail.ru не выполнил поиск новых писем")
            uids = (rows[0] if rows else b"").split()
            uids = uids[-max(1, self.settings.mailru_max_messages) :]
            candidates: list[VacancyCandidate] = []
            for raw_uid in reversed(uids):
                status, payload = client.uid("fetch", raw_uid, "(BODY.PEEK[])")
                if status != "OK":
                    LOGGER.warning(
                        "Mail.ru не вернул письмо UID %s",
                        raw_uid.decode(errors="replace"),
                    )
                    continue
                raw_message = next(
                    (
                        row[1]
                        for row in payload
                        if isinstance(row, tuple)
                        and len(row) > 1
                        and isinstance(row[1], bytes)
                    ),
                    None,
                )
                if raw_message:
                    candidates.extend(
                        parse_mailru_message(
                            raw_message,
                            uid=raw_uid.decode(errors="replace"),
                        )
                    )
            return candidates
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                LOGGER.debug("Mail.ru IMAP-сессия уже закрыта")

    async def fetch_recent(self) -> list[VacancyCandidate]:
        return await asyncio.to_thread(self._fetch_sync)
