import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from database import Vacancy
from weekly_hh_backfill import CURSOR_NAME, MAX_AI_CALLS, run_weekly_hh_backfill


def make_candidate(identifier):
    return SimpleNamespace(
        external_id=str(identifier),
        published_at=datetime.now(timezone.utc),
    )


def make_vacancy(identifier):
    return Vacancy(
        id=identifier,
        source="hh",
        external_id=str(identifier),
        track="senior_it",
        title="Руководитель IT проекта",
        company="Пример",
        description="Управление командой разработки и бюджетом проекта",
        work_format="hybrid",
        published_at=datetime.now(timezone.utc),
    )


def test_weekly_recheck_caps_paid_calls_and_is_restart_safe():
    cursors = {}
    database = SimpleNamespace(
        get_cursor=lambda key: cursors.get(key),
        set_cursor=lambda key, value: cursors.__setitem__(key, value),
    )
    repository = SimpleNamespace(
        upsert_candidate_with_status=Mock(
            side_effect=lambda candidate: (make_vacancy(int(candidate.external_id)), "created")
        ),
        get_score_model=Mock(return_value=None),
        save_score=Mock(),
    )
    hh = SimpleNamespace(
        fetch_recent=AsyncMock(return_value=[make_candidate(i) for i in range(1, 51)]),
        last_fetch_errors=0,
    )
    ranker = SimpleNamespace(
        score=AsyncMock(
            return_value={
                "model": "gpt-4.1-mini",
                "input_tokens": 2500,
                "output_tokens": 250,
            }
        ),
        quota_exhausted=False,
    )

    first = asyncio.run(run_weekly_hh_backfill(database, repository, ranker, hh))
    second = asyncio.run(run_weekly_hh_backfill(database, repository, ranker, hh))

    hh.fetch_recent.assert_awaited_once_with(max_pages=3, period_days=7)
    assert ranker.score.await_count == MAX_AI_CALLS
    assert first["saved"] == 50
    assert first["ai_scored"] == MAX_AI_CALLS
    assert first["spent_usd"] < 1
    assert second == first
    assert json.loads(cursors[CURSOR_NAME])["done"] is True
