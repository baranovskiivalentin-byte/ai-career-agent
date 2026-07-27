import base64

from gmail_source import parse_gmail_message


def test_gmail_remote_alert_is_parsed():
    body = (
        "Remote Senior IT Project Manager Example "
        "https://www.linkedin.com/jobs/view/123"
    )
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    message = {
        "id": "gmail-1",
        "payload": {
            "headers": [{"name": "Subject", "value": "Senior IT Project Manager"}],
            "mimeType": "text/plain",
            "body": {"data": encoded},
        },
    }
    results = parse_gmail_message(message)
    assert len(results) == 1
    result = results[0]
    assert result.source == "linkedin_email"
    assert result.work_format == "remote"


def test_gmail_office_alert_is_ignored():
    encoded = base64.urlsafe_b64encode(b"Office Project Manager").decode().rstrip("=")
    message = {
        "id": "gmail-2",
        "payload": {
            "headers": [{"name": "Subject", "value": "Project Manager"}],
            "mimeType": "text/plain",
            "body": {"data": encoded},
        },
    }
    assert parse_gmail_message(message) == []


def test_hh_alert_is_parsed_without_remote_requirement():
    body = (
        "Senior Project Manager, Москва "
        "https://hh.ru/vacancy/123456789?from=vacancy_search_list"
    )
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    message = {
        "id": "gmail-hh-1",
        "payload": {
            "headers": [{"name": "Subject", "value": "Новые вакансии на hh.ru"}],
            "mimeType": "text/plain",
            "body": {"data": encoded},
        },
    }

    results = parse_gmail_message(message)

    assert len(results) == 1
    result = results[0]
    assert result.source == "hh_email"
    assert result.external_id == "hh:123456789"
    assert result.url == "https://hh.ru/vacancy/123456789"
    assert result.work_format is None


def test_hh_alert_deduplicates_same_vacancy():
    body = (
        "Удалённая работа "
        "https://hh.ru/vacancy/123456789 "
        "https://hh.ru/vacancy/123456789?from=email"
    )
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    message = {
        "id": "gmail-hh-2",
        "payload": {
            "headers": [{"name": "Subject", "value": "Автопоиск вакансий"}],
            "mimeType": "text/plain",
            "body": {"data": encoded},
        },
    }

    results = parse_gmail_message(message)

    assert len(results) == 1
    assert results[0].work_format == "remote"


def test_unrelated_email_is_ignored():
    encoded = base64.urlsafe_b64encode(b"Weekly project update").decode().rstrip("=")
    message = {
        "id": "gmail-other",
        "payload": {
            "headers": [{"name": "Subject", "value": "Weekly update"}],
            "mimeType": "text/plain",
            "body": {"data": encoded},
        },
    }

    assert parse_gmail_message(message) == []
