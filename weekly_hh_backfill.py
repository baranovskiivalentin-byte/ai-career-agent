"""One-time, restart-safe review of recent HeadHunter vacancies."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from database import Database, Vacancy
from hh_api import HHClient
from ranking import VacancyRanker, deterministic_score
from vacancy_manager import VacancyRepository

LOGGER = logging.getLogger(__name__)
CURSOR_NAME = "hh_weekly_recheck_2026_09_23"
AI_MODEL = "gpt-4.1-mini"
MAX_AI_CALLS = 40
MAX_ESTIMATED_USD = 0.90
RESERVED_USD_PER_CALL = 0.02


def _cost_usd(score: dict[str, Any]) -> float:
    """Use current gpt-4.1-mini list rates; charge a reserve if usage is absent."""
    input_tokens = int(score.get("input_tokens") or 0)
    output_tokens = int(score.get("output_tokens") or 0)
    if not input_tokens and not output_tokens:
        return RESERVED_USD_PER_CALL
    return input_tokens * 0.40 / 1_000_000 + output_tokens * 1.60 / 1_000_000


async def run_weekly_hh_backfill(
    database: Database,
    repository: VacancyRepository,
    ranker: VacancyRanker,
    hh: HHClient,
) -> dict[str, Any]:
    """Collect seven days, score all locally, then AI-score a bounded top batch.

    The cursor is written after each paid attempt so a restart cannot reset the
    call budget. Existing AI scores are preserved until successfully replaced.
    """
    raw_state = database.get_cursor(CURSOR_NAME)
    state = json.loads(raw_state) if raw_state else {}
    if state.get("done"):
        return state
    state.setdefault("ai_ids", [])
    state.setdefault("spent_usd", 0.0)
    state.setdefault("attempts", 0)
    candidates = await hh.fetch_recent(max_pages=3, period_days=7)
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    ranked: list[tuple[int, Vacancy, dict[str, Any]]] = []
    stats = {
        "fetched": len(candidates),
        "saved": 0,
        "ai_scored": int(state.get("ai_scored", 0)),
        "errors": hh.last_fetch_errors,
    }
    for candidate in candidates:
        published = candidate.published_at
        if published is not None:
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published < recent_cutoff:
                continue
        try:
            vacancy, _ = repository.upsert_candidate_with_status(candidate)
            stats["saved"] += 1
            track = vacancy.track if vacancy.track in {"senior_it", "enterprise_epc"} else "senior_it"
            baseline = deterministic_score(vacancy, track)
            existing_model = repository.get_score_model(vacancy.id, track)
            if existing_model is None or existing_model == "deterministic":
                repository.save_score(vacancy.id, baseline)
            ranked.append((baseline["total"], vacancy, baseline))
        except Exception:
            stats["errors"] += 1
            LOGGER.exception("Не удалось сохранить вакансию недельного прохода %s", candidate.external_id)

    ranked.sort(key=lambda row: row[0], reverse=True)
    processed = set(state["ai_ids"])
    for _, vacancy, _ in ranked:
        vacancy_id = str(vacancy.id)
        if vacancy_id in processed:
            continue
        if state["attempts"] >= MAX_AI_CALLS:
            break
        if state["spent_usd"] + RESERVED_USD_PER_CALL > MAX_ESTIMATED_USD:
            break
        try:
            score = await ranker.score(
                vacancy,
                model_override=AI_MODEL,
                max_output_tokens=500,
            )
            if score["model"] == AI_MODEL:
                repository.save_score(vacancy.id, score)
                stats["ai_scored"] += 1
            else:
                stats["errors"] += 1
            state["spent_usd"] += _cost_usd(score)
        except Exception:
            stats["errors"] += 1
            state["spent_usd"] += RESERVED_USD_PER_CALL
            LOGGER.exception("Не удалось AI-оценить HH-вакансию %s", vacancy.external_id)
        finally:
            state["ai_ids"].append(vacancy_id)
            processed.add(vacancy_id)
            state["attempts"] += 1
            database.set_cursor(CURSOR_NAME, json.dumps(state))
        if ranker.quota_exhausted:
            break

    state.update(stats)
    state["done"] = not stats["errors"] and not ranker.quota_exhausted
    database.set_cursor(CURSOR_NAME, json.dumps(state))
    LOGGER.info("Недельная перепроверка HH завершена: %s", state)
    return state
