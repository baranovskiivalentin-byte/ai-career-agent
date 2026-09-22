from datetime import datetime, timezone
from http.client import HTTPConnection
from zoneinfo import ZoneInfo

from dashboard_server import (
    _cookie_token,
    _valid_token,
    render_dashboard_html,
    start_dashboard_server,
)
from database import Database, Vacancy
from vacancy_manager import VacancyRepository


def test_dashboard_renders_scanner_vacancy_and_escapes_untrusted_text(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'dashboard.db'}")
    database.create_schema()
    with database.session() as session:
        session.add(
            Vacancy(
                source="scanner",
                external_id="scanner-1",
                track="scanner_primary",
                title="IT Project Manager <script>alert(1)</script>",
                company="Example",
                description="Full official description " * 15,
                work_format="unknown",
                published_at=datetime.now(timezone.utc),
                url="https://example.com/job/1",
                content_hash="scanner-1",
                archived=False,
            )
        )
    repository = VacancyRepository(database)
    vacancy = repository.get_pending_scanner_scores()[0]
    repository.save_score(
        vacancy.id,
        {
            "track": "senior_it",
            "total": 82,
            "role_score": 30,
            "seniority_score": 15,
            "domain_score": 12,
            "experience_score": 10,
            "salary_score": 10,
            "freshness_score": 5,
            "reasons": ["Подходит опыт внедрения"],
            "risks": ["Не указана зарплата"],
            "model": "test",
        },
    )

    page = render_dashboard_html(database, ZoneInfo("Europe/Moscow"), 70)

    assert "82/100" in page
    assert "Рекомендую откликнуться" in page
    assert "https://example.com/job/1" in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page


def test_dashboard_token_helpers():
    assert _valid_token("secret", "secret")
    assert not _valid_token("wrong", "secret")
    assert _cookie_token("career_dashboard_session=secret") == "secret"


def test_dashboard_http_requires_token_and_accepts_session_cookie(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'server.db'}")
    database.create_schema()
    server = start_dashboard_server(
        database,
        token="secret",
        port=0,
        tz=ZoneInfo("Europe/Moscow"),
        scoring_threshold=70,
    )
    assert server is not None
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request("GET", "/dashboard")
        response = connection.getresponse()
        assert response.status == 401
        response.read()

        connection.request("GET", "/?token=secret")
        response = connection.getresponse()
        assert response.status == 303
        cookie = response.getheader("Set-Cookie")
        response.read()
        assert cookie

        connection.request(
            "GET",
            "/dashboard",
            headers={"Cookie": cookie.split(";", 1)[0]},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert "Мониторинг вакансий" in response.read().decode()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
