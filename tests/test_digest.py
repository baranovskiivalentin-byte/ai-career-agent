import asyncio
import json
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from database import Vacancy, VacancyScore
from digest import DigestItem, card_text, select_digest_items, send_digest


def row(identifier: int, track: str, total: int):
    vacancy = Vacancy(
        id=identifier,
        source="test",
        external_id=str(identifier),
        track=track,
        title=f"Vacancy {identifier}",
        company="Example",
        description="remote project manager",
        work_format="remote",
        url=f"https://example.com/{identifier}",
        content_hash=f"hash-{identifier}",
    )
    score = VacancyScore(
        vacancy_id=identifier,
        track=track,
        total=total,
        role_score=30,
        seniority_score=15,
        domain_score=10,
        experience_score=10,
        salary_score=5,
        freshness_score=5,
        reasons_json=json.dumps(["reason"]),
        risks_json=json.dumps([]),
        model="test",
    )
    return vacancy, score


def test_digest_balances_five_plus_five():
    rows = [row(i, "senior_it", 100 - i) for i in range(1, 8)]
    rows += [row(i, "enterprise_epc", 100 - i) for i in range(8, 15)]
    selected = select_digest_items(rows)
    assert len(selected) == 10
    assert sum(x.score.track == "senior_it" for x in selected) == 5
    assert sum(x.score.track == "enterprise_epc" for x in selected) == 5


def test_hybrid_card_is_labeled_hybrid():
    vacancy, score = row(1, "senior_it", 80)
    vacancy.work_format = "hybrid"
    assert "🏠 Гибрид" in card_text(DigestItem(vacancy, score))


def test_digest_backfills_only_high_scores():
    rows = [row(i, "senior_it", 90 - i) for i in range(1, 9)]
    rows += [row(20, "enterprise_epc", 90)]
    selected = select_digest_items(rows, backfill_score=75)
    assert len(selected) == 9


def test_forced_digest_includes_collected_vacancies_below_threshold():
    repository = SimpleNamespace(
        get_ranked=Mock(return_value=[row(1, "senior_it", 18)]),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    application = SimpleNamespace(bot=bot)
    settings = SimpleNamespace(
        timezone=timezone.utc,
        scoring_threshold=70,
        shadow_mode=True,
    )

    count = asyncio.run(
        send_digest(application, repository, settings, chat_id=123, force=True)
    )

    assert count == 1
    repository.get_ranked.assert_called_once_with(0)
    assert bot.send_message.await_count == 2


def test_scheduled_digest_sends_preliminary_items_when_none_passes_threshold():
    repository = SimpleNamespace(
        digest_exists=Mock(return_value=False),
        get_ranked=Mock(side_effect=[[], [row(1, "senior_it", 54)]]),
        save_digest=Mock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    application = SimpleNamespace(bot=bot)
    settings = SimpleNamespace(
        timezone=timezone.utc,
        scoring_threshold=70,
        scoring_fallback_threshold=50,
        shadow_mode=False,
    )

    count = asyncio.run(send_digest(application, repository, settings, chat_id=123))

    assert count == 1
    assert repository.get_ranked.call_args_list == [((70,), {}), ((50,), {})]
    assert "Предварительная подборка" in bot.send_message.await_args_list[0].kwargs["text"]
    assert bot.send_message.await_count == 2
