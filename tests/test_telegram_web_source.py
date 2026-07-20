from datetime import datetime, timezone

from telegram_web_source import (
    is_target_vacancy,
    normalize_channel,
    parse_channel_page,
)


def post(post_id: int, text: str, published: str = "2026-07-21T08:00:00+00:00") -> str:
    return f"""
    <div class="tgme_widget_message" data-post="jobs_pm/{post_id}">
      <div class="tgme_widget_message_text">{text}</div>
      <time datetime="{published}"></time>
    </div>
    """


def test_normalize_channel_accepts_username_and_urls():
    assert normalize_channel("@RemoteIT") == "remoteit"
    assert normalize_channel("https://t.me/s/jobs_pm") == "jobs_pm"
    assert normalize_channel("https://t.me/jobs_pm/123") == "jobs_pm"


def test_target_filter_requires_role_and_remote_format():
    assert is_target_vacancy("Senior Project Manager. Полностью удалённая работа")
    assert is_target_vacancy("Ищем руководителя проектов, формат remote")
    assert not is_target_vacancy("Senior Project Manager, офис в Москве")
    assert not is_target_vacancy("#резюме Project Manager, ищу удалённую работу")
    assert not is_target_vacancy("Удалённая работа для Python-разработчика")


def test_parse_channel_page_extracts_only_fresh_target_vacancy():
    html = "".join(
        [
            post(100, "#резюме Project Manager. Ищу удалённую работу"),
            post(101, "Senior Python Developer. Remote. 400 000 ₽"),
            post(
                102,
                "Senior IT Project Manager\nУдалённая работа\nЗарплата 350 000–450 000 ₽\nSAP и ERP",
            ),
            post(
                103,
                "Delivery Manager. Remote. $5 000–6 000",
                "2026-07-01T08:00:00+00:00",
            ),
        ]
    )
    rows = parse_channel_page(
        html,
        "jobs_pm",
        now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
        lookback_hours=72,
        max_posts=5,
    )

    assert len(rows) == 1
    vacancy = rows[0]
    assert vacancy.external_id == "jobs_pm:102"
    assert vacancy.track == "enterprise_epc"
    assert vacancy.salary_from == 350_000
    assert vacancy.salary_to == 450_000
    assert vacancy.currency == "RUR"
    assert vacancy.url == "https://t.me/jobs_pm/102"
    assert vacancy.work_format == "remote"


def test_parse_channel_page_honors_max_posts():
    html = "".join(
        post(index, f"Project Manager #{index}. Remote") for index in range(1, 5)
    )
    rows = parse_channel_page(
        html,
        "jobs_pm",
        now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
        max_posts=2,
    )
    assert [row.external_id for row in rows] == ["jobs_pm:4", "jobs_pm:3"]


def test_parse_channel_page_keeps_currency_salary_scale():
    html = post(200, "Delivery Manager. Remote. $5 000–6 000")
    rows = parse_channel_page(
        html,
        "jobs_pm",
        now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
    )
    assert rows[0].salary_from == 5_000
    assert rows[0].salary_to == 6_000
    assert rows[0].currency == "USD"
