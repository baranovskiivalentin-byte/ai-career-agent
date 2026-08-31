import asyncio
from datetime import datetime, timezone

import httpx

from habr_source import (
    HabrCareerSource,
    is_target_role,
    parse_habr_rss,
    parse_habr_vacancy,
)


def test_target_roles_are_strict():
    assert is_target_role("Senior Project Manager")
    assert is_target_role("Руководитель проектов внедрения")
    assert is_target_role("PMO Lead")
    assert is_target_role("Chief of Staff")
    assert not is_target_role("Product Manager")
    assert not is_target_role("Project Coordinator")


def test_parse_rss_supports_rss_dates_and_vacancy_links():
    xml = """
    <rss version="2.0"><channel>
      <item>
        <title>Project / Delivery Manager</title>
        <link>https://career.habr.com/vacancies/1000168378</link>
        <pubDate>Sat, 29 Aug 2026 09:00:00 +0300</pubDate>
      </item>
      <item><title>News</title><link>https://career.habr.com/articles/1</link></item>
    </channel></rss>
    """

    rows = parse_habr_rss(xml)

    assert len(rows) == 1
    assert rows[0].url.endswith("/vacancies/1000168378")
    assert rows[0].published_at == datetime(2026, 8, 29, 6, tzinfo=timezone.utc)


def test_json_ld_is_preferred_and_annual_salary_is_monthly():
    html = """
    <html><head><script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "Enterprise Implementation Manager",
      "description": "<p>Lead a global <b>ERP implementation</b>.</p>",
      "datePosted": "2026-08-29T09:00:00+03:00",
      "jobLocationType": "TELECOMMUTE",
      "applicantLocationRequirements": {"@type":"Country", "name":"Worldwide"},
      "hiringOrganization": {"@type":"Organization", "name":"Example AI"},
      "baseSalary": {
        "@type":"MonetaryAmount", "currency":"USD",
        "value":{"@type":"QuantitativeValue", "minValue":96000,
                 "maxValue":120000, "unitText":"YEAR"}
      }
    }
    </script></head><body><h1>Wrong fallback title</h1></body></html>
    """

    row = parse_habr_vacancy(html, "https://career.habr.com/vacancies/1000168449")

    assert row is not None
    assert row.source == "habr"
    assert row.external_id == "1000168449"
    assert row.title == "Enterprise Implementation Manager"
    assert row.company == "Example AI"
    assert row.description == "Lead a global ERP implementation."
    assert (row.salary_from, row.salary_to, row.currency) == (8_000, 10_000, "USD")
    assert row.location == "Worldwide"
    assert row.track == "enterprise_epc"
    assert row.work_format == "remote"
    assert len(row.content_hash) == 64


def test_html_fallback_reads_full_remote_card():
    html = """
    <html><body>
      <h1>Руководитель проектов внедрения (Technical Project Lead)</h1>
      <time datetime="2026-08-29T08:30:00+03:00"></time>
      <div class="vacancy-header__salary">от 180 000 до 220 000 ₽</div>
      <section class="vacancy-conditions">Можно удалённо</section>
      <div class="vacancy-location">Москва</div>
      <div class="vacancy-company__title">Raft Digital Solutions</div>
      <article id="vacancy-description">
        Вести проект: скоуп, сроки, риски и коммуникации с заказчиком.
        Проводить внедрение AI и BI платформы у крупных клиентов.
      </article>
    </body></html>
    """

    row = parse_habr_vacancy(html, "https://career.habr.com/vacancies/1000168449?f=rss")

    assert row is not None
    assert row.company == "Raft Digital Solutions"
    assert row.salary_from == 180_000
    assert row.salary_to == 220_000
    assert row.currency == "RUR"
    assert row.location == "Москва"
    assert "Проводить внедрение AI" in row.description
    assert row.published_at == datetime.fromisoformat("2026-08-29T08:30:00+03:00")


def test_non_remote_and_non_target_cards_are_rejected():
    office = """
    <h1>Delivery Manager</h1><div id="vacancy-description">Office delivery.</div>
    <div class="vacancy-conditions">Москва, офис</div>
    """
    product = """
    <h1>Product Manager</h1><div id="vacancy-description">Product ownership.</div>
    <div>Можно удалённо</div>
    """
    assert parse_habr_vacancy(office, "https://career.habr.com/vacancies/1") is None
    assert parse_habr_vacancy(product, "https://career.habr.com/vacancies/2") is None


def test_source_uses_rss_as_trigger_and_caches_for_at_least_30_minutes():
    calls: list[str] = []
    rss = """
    <rss><channel><item>
      <title>Delivery Manager</title>
      <link>https://career.habr.com/vacancies/42</link>
      <pubDate>2026-08-30T09:00:00Z</pubDate>
    </item></channel></rss>
    """
    card = """
    <h1>Delivery Manager</h1>
    <div>Можно удалённо</div>
    <div class="vacancy-company__title">Cache Co</div>
    <div id="vacancy-description">Manage delivery for distributed teams.</div>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        text = rss if request.url.path.endswith("/rss") else card
        return httpx.Response(200, text=text, request=request)

    source = HabrCareerSource(
        ttl_seconds=1,
        transport=httpx.MockTransport(handler),
    )

    async def run():
        first = await source.fetch_recent()
        second = await source.fetch_recent()
        return first, second

    first, second = asyncio.run(run())

    assert source.ttl_seconds == 1800
    assert len(calls) == 2
    assert first[0].external_id == second[0].external_id == "42"


def test_rss_title_filters_non_target_before_card_request():
    calls: list[str] = []
    rss = """
    <rss><channel><item>
      <title>Senior Python Developer</title>
      <link>https://career.habr.com/vacancies/99</link>
    </item></channel></rss>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=rss, request=request)

    source = HabrCareerSource(transport=httpx.MockTransport(handler))
    assert asyncio.run(source.fetch_recent()) == []
    assert len(calls) == 1


def test_source_caps_card_requests_when_feed_has_no_remote_matches():
    calls: list[str] = []
    rss = """
    <rss><channel>
      <item><title>Project Manager</title><link>https://career.habr.com/vacancies/1</link></item>
      <item><title>Delivery Manager</title><link>https://career.habr.com/vacancies/2</link></item>
      <item><title>Program Manager</title><link>https://career.habr.com/vacancies/3</link></item>
    </channel></rss>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        text = rss if request.url.path.endswith("/rss") else (
            "<h1>Project Manager</h1><div>Только офис</div>"
            '<div id="vacancy-description">Manage delivery.</div>'
        )
        return httpx.Response(200, text=text, request=request)

    source = HabrCareerSource(
        max_card_attempts=2,
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(source.fetch_recent()) == []
    assert len(calls) == 3
