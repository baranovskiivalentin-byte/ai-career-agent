import asyncio
from types import SimpleNamespace

import httpx

from hh_api import (
    HH_API_URL,
    HHClient,
    is_explicitly_remote,
    normalize_hh_vacancy,
    parse_datetime,
    strip_html,
)


def test_remote_detected_from_schedule():
    assert is_explicitly_remote({"schedule": {"id": "remote"}})


def test_remote_detected_from_new_work_format():
    assert is_explicitly_remote({"work_format": [{"id": "REMOTE", "name": "Из дома"}]})


def test_hybrid_is_not_remote_without_explicit_remote_text():
    assert not is_explicitly_remote(
        {"schedule": {"id": "flexible", "name": "Гибрид"}, "description": "Офис 3 дня"}
    )


def test_remote_detected_from_description():
    assert is_explicitly_remote({"description": "Полностью удалённо из любой точки"})


def test_normalization_strips_html():
    item = {
        "id": "123",
        "name": "Delivery Manager",
        "employer": {"name": "Example"},
        "description": "<p>Управление <b>командой</b></p>",
        "schedule": {"id": "remote"},
        "alternate_url": "https://hh.ru/vacancy/123",
    }
    result = normalize_hh_vacancy(item, "senior_it")
    assert result.description == "Управление командой"
    assert result.work_format == "remote"
    assert strip_html("a<br>b") == "a b"


def test_parse_datetime_accepts_unix_seconds_and_milliseconds():
    seconds = parse_datetime(1_700_000_000)
    milliseconds = parse_datetime(1_700_000_000_000)

    assert seconds is not None
    assert seconds == milliseconds
    assert seconds.tzinfo is not None


def test_cached_app_token_is_added_to_each_new_http_client():
    token_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        assert request.url.path == "/token"
        token_requests += 1
        return httpx.Response(
            200,
            json={"access_token": "app-token", "expires_in": 3600},
        )

    settings = SimpleNamespace(
        hh_user_agent="AI Career Agent tests",
        hh_access_token=None,
        hh_client_id="client-id",
        hh_client_secret="client-secret",
    )
    hh = HHClient(settings)
    transport = httpx.MockTransport(handler)

    async def check_clients() -> None:
        async with httpx.AsyncClient(
            base_url=HH_API_URL,
            headers=hh.headers,
            transport=transport,
        ) as first:
            await hh._ensure_token(first)
            assert first.headers["Authorization"] == "Bearer app-token"
        async with httpx.AsyncClient(
            base_url=HH_API_URL,
            headers=hh.headers,
            transport=transport,
        ) as second:
            await hh._ensure_token(second)
            assert second.headers["Authorization"] == "Bearer app-token"

    asyncio.run(check_clients())
    assert token_requests == 1
