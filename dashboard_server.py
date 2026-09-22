from __future__ import annotations

import hmac
import json
import logging
import threading
from datetime import datetime, timezone
from html import escape
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import and_, desc, select

from database import Database, Vacancy, VacancyScore

LOGGER = logging.getLogger(__name__)
SESSION_COOKIE = "career_dashboard_session"


def _parse_json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _local_date(value: datetime | None, tz) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz).strftime("%d.%m.%Y")


def _safe_url(value: str) -> str:
    parsed = urlsplit(value or "")
    return value if parsed.scheme in {"http", "https"} else "#"


def _dashboard_rows(database: Database) -> list[dict]:
    with database.session() as session:
        rows = session.execute(
            select(Vacancy, VacancyScore)
            .outerjoin(
                VacancyScore,
                and_(
                    VacancyScore.vacancy_id == Vacancy.id,
                    VacancyScore.track == "senior_it",
                ),
            )
            .where(Vacancy.source == "scanner")
            .order_by(
                Vacancy.archived,
                desc(VacancyScore.total),
                desc(Vacancy.published_at),
                Vacancy.company,
                Vacancy.title,
            )
        ).all()
        return [
            {
                "id": vacancy.id,
                "title": vacancy.title,
                "company": vacancy.company,
                "description": vacancy.description,
                "url": vacancy.url,
                "match_level": (
                    "PRIMARY"
                    if vacancy.track == "scanner_primary"
                    else "SECONDARY"
                ),
                "archived": vacancy.archived,
                "first_seen": vacancy.published_at,
                "last_seen": vacancy.updated_at,
                "score": score.total if score else None,
                "reasons": _parse_json_list(score.reasons_json if score else None),
                "risks": _parse_json_list(score.risks_json if score else None),
            }
            for vacancy, score in rows
        ]


def _verdict(score: int | None, threshold: int) -> tuple[str, str]:
    if score is None:
        return "Без оценки", "pending"
    if score >= threshold:
        return "Рекомендую откликнуться", "recommended"
    if score >= 50:
        return "Можно рассмотреть", "consider"
    return "Не рекомендую", "low"


def render_dashboard_html(database: Database, tz, scoring_threshold: int) -> str:
    rows = _dashboard_rows(database)
    today = datetime.now(tz).date().isoformat()
    active = [row for row in rows if not row["archived"]]
    scored = [row for row in active if row["score"] is not None]
    recommended = [
        row for row in active if (row["score"] or -1) >= scoring_threshold
    ]
    cards: list[str] = []
    for row in rows:
        first_seen = row["first_seen"]
        if first_seen is not None and first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        is_today = bool(first_seen and first_seen.astimezone(tz).date().isoformat() == today)
        verdict, verdict_class = _verdict(row["score"], scoring_threshold)
        status = "not_seen" if row["archived"] else "active"
        score = "—" if row["score"] is None else f"{row['score']}/100"
        reasons = "".join(f"<li>{escape(item)}</li>" for item in row["reasons"])
        risks = "".join(f"<li>{escape(item)}</li>" for item in row["risks"])
        description = escape(row["description"] or "Описание отсутствует")
        cards.append(
            f"""
            <article class="vacancy-card" data-status="{status}"
                     data-today="{'yes' if is_today else 'no'}"
                     data-verdict="{verdict_class}">
              <div class="card-topline">
                <span class="company">{escape(row['company'])}</span>
                <span class="level {row['match_level'].lower()}">{row['match_level']}</span>
                <span class="state {status}">{'NOT_SEEN' if row['archived'] else 'ACTIVE'}</span>
              </div>
              <h2>{escape(row['title'])}</h2>
              <div class="assessment">
                <strong class="score">{score}</strong>
                <span class="verdict {verdict_class}">{verdict}</span>
              </div>
              <div class="dates">
                Найдена: {_local_date(row['first_seen'], tz)} ·
                проверена: {_local_date(row['last_seen'], tz)}
              </div>
              {f'<div class="evidence"><b>Почему подходит</b><ul>{reasons}</ul></div>' if reasons else ''}
              {f'<div class="evidence risks"><b>Риски</b><ul>{risks}</ul></div>' if risks else ''}
              <div class="actions">
                <a href="{escape(_safe_url(row['url']), quote=True)}" target="_blank"
                   rel="noopener noreferrer">Открыть вакансию ↗</a>
                <details>
                  <summary>Полное описание</summary>
                  <div class="description">{description}</div>
                </details>
              </div>
            </article>
            """
        )

    generated = datetime.now(tz).strftime("%d.%m.%Y, %H:%M")
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Vacancy Scanner</title>
  <style>
    :root {{ --ink:#09213a; --muted:#607286; --line:#d6e0e8; --bg:#edf3f6;
      --brand:#0f7489; --green:#16734b; --amber:#a35a00; --red:#a53b3b; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--bg); font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; }}
    .shell {{ width:min(1120px,calc(100% - 28px)); margin:24px auto 60px; }}
    header {{ background:#fff; border:1px solid var(--line); border-radius:18px; padding:24px; }}
    h1 {{ margin:0 0 6px; font-size:clamp(25px,5vw,38px); }}
    .subtitle,.dates {{ color:var(--muted); }}
    .stats {{ display:grid; grid-template-columns:repeat(5,1fr); gap:10px; margin-top:22px; }}
    .stat {{ padding:14px; border-radius:13px; background:#f4f8fa; border:1px solid var(--line); }}
    .stat strong {{ display:block; font-size:25px; }}
    .filters {{ display:flex; gap:8px; flex-wrap:wrap; position:sticky; top:0; z-index:2;
      margin:16px 0; padding:10px; background:rgba(237,243,246,.96); backdrop-filter:blur(8px); }}
    button {{ border:1px solid #b8c8d4; background:#fff; color:var(--ink); border-radius:999px;
      padding:10px 14px; font:inherit; cursor:pointer; }}
    button.active {{ color:#fff; background:var(--brand); border-color:var(--brand); }}
    .vacancies {{ display:grid; gap:12px; }}
    .vacancy-card {{ background:#fff; border:1px solid var(--line); border-radius:16px; padding:20px; }}
    .card-topline {{ display:flex; align-items:center; flex-wrap:wrap; gap:8px; }}
    .company {{ font-weight:750; margin-right:auto; }}
    .level,.state,.verdict {{ display:inline-flex; padding:4px 9px; border-radius:999px; font-size:13px; }}
    .primary {{ background:#e2f3f6; }} .secondary {{ background:#f2eef9; }}
    .active {{ background:#e8f6ef; }} .not_seen {{ background:#f3f3f3; color:#666; }}
    h2 {{ margin:13px 0 9px; font-size:21px; }}
    .assessment {{ display:flex; align-items:center; gap:10px; margin-bottom:8px; }}
    .score {{ font-size:20px; }}
    .recommended {{ color:var(--green); background:#e7f5ed; }}
    .consider {{ color:var(--amber); background:#fff2df; }}
    .low {{ color:var(--red); background:#fdeaea; }}
    .pending {{ color:#566577; background:#edf1f4; }}
    .evidence {{ margin-top:12px; }} .evidence ul {{ margin:5px 0 0; padding-left:21px; }}
    .risks {{ color:#713939; }}
    .actions {{ display:flex; align-items:flex-start; gap:16px; flex-wrap:wrap; margin-top:16px; }}
    a {{ color:#00677e; font-weight:700; }}
    details {{ flex:1 1 100%; border-top:1px solid var(--line); padding-top:12px; }}
    summary {{ cursor:pointer; font-weight:650; }}
    .description {{ white-space:pre-wrap; margin-top:10px; color:#32465a; }}
    .empty {{ display:none; text-align:center; padding:40px; color:var(--muted); }}
    @media (max-width:760px) {{ .shell {{ width:min(100% - 16px,1120px); margin-top:8px; }}
      header,.vacancy-card {{ border-radius:13px; padding:16px; }} .stats {{ grid-template-columns:repeat(2,1fr); }}
      .filters {{ margin-inline:-4px; overflow:auto; flex-wrap:nowrap; }} button {{ white-space:nowrap; }} }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <h1>Мониторинг вакансий</h1>
      <div class="subtitle">Обновлено {generated} · защищённый режим просмотра</div>
      <section class="stats">
        <div class="stat"><strong>{len(active)}</strong>активных</div>
        <div class="stat"><strong>{len(scored)}</strong>оценено</div>
        <div class="stat"><strong>{len(recommended)}</strong>рекомендовано</div>
        <div class="stat"><strong>{sum(1 for r in active if r['score'] is None)}</strong>ждут оценки</div>
        <div class="stat"><strong>{sum(1 for r in rows if r['archived'])}</strong>NOT_SEEN</div>
      </section>
    </header>
    <nav class="filters" aria-label="Фильтры вакансий">
      <button class="active" data-filter="active">Все активные</button>
      <button data-filter="today">Новые сегодня</button>
      <button data-filter="recommended">Рекомендованные</button>
      <button data-filter="consider">Можно рассмотреть</button>
      <button data-filter="pending">Без оценки</button>
      <button data-filter="not_seen">NOT_SEEN</button>
    </nav>
    <section class="vacancies">{''.join(cards)}</section>
    <div class="empty">В этой категории пока нет вакансий.</div>
  </main>
  <script>
    const cards = [...document.querySelectorAll('.vacancy-card')];
    const empty = document.querySelector('.empty');
    function applyFilter(filter) {{
      let visible = 0;
      cards.forEach(card => {{
        const show = filter === 'active' ? card.dataset.status === 'active'
          : filter === 'today' ? card.dataset.status === 'active' && card.dataset.today === 'yes'
          : filter === 'not_seen' ? card.dataset.status === 'not_seen'
          : card.dataset.status === 'active' && card.dataset.verdict === filter;
        card.hidden = !show;
        visible += show ? 1 : 0;
      }});
      empty.style.display = visible ? 'none' : 'block';
    }}
    document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {{
      document.querySelectorAll('[data-filter]').forEach(item => item.classList.remove('active'));
      button.classList.add('active'); applyFilter(button.dataset.filter);
    }}));
    applyFilter('active');
  </script>
</body>
</html>"""


def _valid_token(value: str | None, expected: str) -> bool:
    return bool(value) and hmac.compare_digest(value, expected)


def _cookie_token(raw_cookie: str | None) -> str | None:
    if not raw_cookie:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie)
    except CookieError:
        return None
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value if morsel else None


def start_dashboard_server(
    database: Database,
    *,
    token: str | None,
    port: int,
    tz,
    scoring_threshold: int,
) -> ThreadingHTTPServer | None:
    if not token:
        LOGGER.warning("Онлайн-дашборд отключён: DASHBOARD_TOKEN не задан")
        return None

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path == "/healthz":
                self._send(200, "text/plain; charset=utf-8", b"ok")
                return
            if parsed.path not in {"/", "/dashboard"}:
                self._send(404, "text/plain; charset=utf-8", b"Not found")
                return

            supplied = parse_qs(parsed.query).get("token", [None])[0]
            if _valid_token(supplied, token):
                self.send_response(303)
                self.send_header("Location", "/dashboard")
                self.send_header(
                    "Set-Cookie",
                    f"{SESSION_COOKIE}={token}; Path=/; Max-Age=2592000; "
                    "Secure; HttpOnly; SameSite=Strict",
                )
                self.end_headers()
                return

            current = _cookie_token(self.headers.get("Cookie"))
            if not _valid_token(current, token):
                body = (
                    "<!doctype html><meta charset=utf-8><meta name=viewport "
                    "content='width=device-width,initial-scale=1'>"
                    "<title>Доступ закрыт</title><body style='font:16px system-ui;"
                    "max-width:620px;margin:80px auto;padding:20px'>"
                    "<h1>Доступ закрыт</h1><p>Откройте персональную ссылку "
                    "на дашборд.</p></body>"
                ).encode()
                self._send(401, "text/html; charset=utf-8", body)
                return

            body = render_dashboard_html(database, tz, scoring_threshold).encode(
                "utf-8"
            )
            self._send(200, "text/html; charset=utf-8", body)

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            LOGGER.info("Dashboard: " + format, *args)

    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    threading.Thread(
        target=server.serve_forever,
        name="vacancy-dashboard",
        daemon=True,
    ).start()
    LOGGER.info("Онлайн-дашборд запущен на порту %s", port)
    return server
