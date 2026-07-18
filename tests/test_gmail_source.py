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
