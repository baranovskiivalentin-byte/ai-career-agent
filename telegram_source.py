from __future__ import annotations

import hashlib
import logging
import re
from datetime import timezone
from typing import Awaitable, Callable

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from config import Settings
from hh_api import VacancyCandidate
from vacancy_manager import VacancyRepository


LOGGER = logging.getLogger(__name__)
REMOTE_WORDS = ("remote", "удаленная", "удалённая", "из дома")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class TelegramChannelSource:
    def __init__(self, settings: Settings, repository: VacancyRepository):
        self.settings = settings
        self.repository = repository
        self.client: TelegramClient | None = None

    async def start(
        self, on_candidate: Callable[[VacancyCandidate], Awaitable[bool]]
    ) -> bool:
        if not all(
            [
                self.settings.telegram_api_id,
                self.settings.telegram_api_hash,
                self.settings.telegram_session,
            ]
        ):
            return False
        self.client = TelegramClient(
            StringSession(self.settings.telegram_session),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
        )

        @self.client.on(events.NewMessage)
        async def handler(event):
            chat = await event.get_chat()
            identifiers = set(self.repository.list_sources())
            username = getattr(chat, "username", None)
            candidates = {str(event.chat_id)}
            if username:
                candidates.update({username.lower(), f"@{username.lower()}"})
            if not identifiers.intersection(candidates):
                return
            text = (event.raw_text or "").strip()
            if not text or not any(word in text.lower() for word in REMOTE_WORDS):
                return
            title = text.splitlines()[0][:500]
            urls = URL_RE.findall(text)
            if username:
                fallback_url = f"https://t.me/{username}/{event.id}"
            else:
                fallback_url = ""
            candidate = VacancyCandidate(
                source="telegram_channel",
                external_id=f"{event.chat_id}:{event.id}",
                track=(
                    "senior_it"
                    if any(
                        word in text.lower()
                        for word in ("delivery", "it project", "ai project")
                    )
                    else "enterprise_epc"
                ),
                title=title or "Вакансия из Telegram",
                company=getattr(chat, "title", None) or username or "Telegram-канал",
                description=text,
                salary_from=None,
                salary_to=None,
                currency=None,
                work_format="remote",
                location=None,
                published_at=event.date.astimezone(timezone.utc),
                url=urls[0].rstrip(".,)") if urls else fallback_url,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
            try:
                await on_candidate(candidate)
            except Exception:
                LOGGER.exception("Не удалось обработать Telegram-вакансию")

        await self.client.start()
        LOGGER.info("Telegram MTProto source запущен")
        return True

    async def stop(self) -> None:
        if self.client:
            await self.client.disconnect()
            self.client = None
