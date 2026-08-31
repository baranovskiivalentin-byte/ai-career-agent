import asyncio

import httpx

from public_job_sources import (
    HimalayasSource,
    JobicySource,
    is_allowed_location,
    is_target_title,
    normalize_monthly_salary,
    parse_himalayas_payload,
    parse_jobicy_payload,
)


def test_strict_target_title_filter():
    assert is_target_title("Senior Technical Project Manager")
    assert is_target_title("AI Program Manager")
    assert is_target_title("AI/ML PM")
    assert is_target_title("Delivery Manager")
    assert is_target_title("PMO Lead")
    assert is_target_title("Chief of Staff")
    assert not is_target_title("Product Manager")
    assert not is_target_title("Project Coordinator")
    assert not is_target_title("Translation Project Manager")
    assert not is_target_title("Localization Project Manager")
    assert not is_target_title("Senior Software Engineer")


def test_location_policy():
    assert is_allowed_location([], "")
    assert is_allowed_location(["Worldwide"], "")
    assert is_allowed_location("Russian Federation", "")
    assert not is_allowed_location("United States", "Full-time employee")
    assert is_allowed_location("United States", "May work as an independent contractor")
    assert is_allowed_location("Germany", "Visa sponsorship and relocation are available")


def test_salary_is_normalized_to_month():
    assert normalize_monthly_salary(120_000, "year") == 10_000
    assert normalize_monthly_salary(6_000, "month") == 6_000
    assert normalize_monthly_salary(60, "hour") == 10_400


def test_parse_himalayas_full_remote_vacancy():
    payload = {
        "jobs": [
            {
                "guid": "hm-42",
                "title": "Enterprise Implementation Manager",
                "companyName": "Example AI",
                "description": "<p>Lead an ERP implementation for global clients.</p>",
                "locationRestrictions": ["Worldwide"],
                "minSalary": 96_000,
                "maxSalary": 120_000,
                "salaryCurrency": "USD",
                "salaryPeriod": "year",
                "pubDate": "2026-08-30T09:00:00Z",
                "applicationLink": "https://example.com/jobs/42",
            },
            {
                "guid": "hm-ignored",
                "title": "Product Manager",
                "description": "Remote product role",
                "locationRestrictions": ["Worldwide"],
            },
        ]
    }

    rows = parse_himalayas_payload(payload)

    assert len(rows) == 1
    row = rows[0]
    assert row.external_id == "himalayas:hm-42"
    assert row.track == "enterprise_epc"
    assert row.description == "Lead an ERP implementation for global clients."
    assert row.salary_from == 8_000
    assert row.salary_to == 10_000
    assert row.currency == "USD"
    assert row.work_format == "remote"


def test_himalayas_rejects_country_only_without_mobility():
    base = {
        "title": "Delivery Manager",
        "description": "Lead delivery for our distributed engineering team.",
        "locationRestrictions": ["Canada"],
        "applicationLink": "https://example.com/canada",
    }
    assert parse_himalayas_payload({"jobs": [base]}) == []

    base["description"] += " We can engage you as an independent contractor."
    assert len(parse_himalayas_payload({"jobs": [base]})) == 1


def test_parse_jobicy_annual_salary_and_location_policy():
    payload = {
        "jobs": [
            {
                "id": 71,
                "jobTitle": "AI Project Manager",
                "companyName": "Remote Labs",
                "jobDescription": "Own AI delivery and stakeholder management.",
                "jobGeo": "Anywhere",
                "annualSalaryMin": "60000",
                "annualSalaryMax": "84000",
                "salaryCurrency": "EUR",
                "pubDate": "2026-08-29T12:00:00Z",
                "url": "https://jobicy.com/jobs/71",
            },
            {
                "id": 72,
                "jobTitle": "Operations Lead",
                "jobDescription": "Hybrid role with three office days.",
                "jobGeo": "Anywhere",
                "url": "https://jobicy.com/jobs/72",
            },
        ]
    }

    rows = parse_jobicy_payload(payload)

    assert len(rows) == 1
    assert rows[0].external_id == "jobicy:71"
    assert rows[0].salary_from == 5_000
    assert rows[0].salary_to == 7_000
    assert rows[0].track == "senior_it"


def test_source_cache_avoids_second_network_request():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "guid": "cached",
                        "title": "Program Manager",
                        "companyName": "Cache Co",
                        "description": "Remote delivery for teams worldwide.",
                        "locationRestrictions": [],
                        "applicationLink": "https://example.com/cached",
                    }
                ]
            },
            request=request,
        )

    source = HimalayasSource(transport=httpx.MockTransport(handler))

    async def run():
        first = await source.fetch_recent()
        second = await source.fetch_recent()
        return first, second

    first, second = asyncio.run(run())

    assert calls == 1
    assert first[0].external_id == second[0].external_id


def test_jobicy_source_uses_documented_count_parameter():
    seen_count = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_count
        seen_count = request.url.params.get("count")
        return httpx.Response(200, json={"jobs": []}, request=request)

    source = JobicySource(count=25, transport=httpx.MockTransport(handler))
    asyncio.run(source.fetch_recent())

    assert seen_count == "25"


def test_empty_result_is_cached_too():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"jobs": []}, request=request)

    source = HimalayasSource(transport=httpx.MockTransport(handler))

    async def run():
        await source.fetch_recent()
        await source.fetch_recent()

    asyncio.run(run())
    assert calls == 1
