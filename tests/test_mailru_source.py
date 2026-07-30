from email.message import EmailMessage
from types import SimpleNamespace

from mailru_source import MailRuJobAlertsSource, parse_mailru_message


def _raw_message(*, subject: str, body: str, html: bool = False) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["Message-ID"] = "<mailru-test@example.com>"
    if html:
        message.set_content("HTML version")
        message.add_alternative(body, subtype="html")
    else:
        message.set_content(body)
    return message.as_bytes()


def test_mailru_hh_alert_is_parsed():
    raw = _raw_message(
        subject="Новые вакансии на hh.ru",
        body=(
            "Senior Project Manager, удалённая работа "
            "https://hh.ru/vacancy/987654321?from=email"
        ),
    )

    results = parse_mailru_message(raw, uid="42")

    assert len(results) == 1
    assert results[0].source == "hh_email"
    assert results[0].external_id == "hh:987654321"
    assert results[0].url == "https://hh.ru/vacancy/987654321"
    assert results[0].work_format == "remote"


def test_mailru_html_alert_extracts_vacancy_link():
    raw = _raw_message(
        subject="Автопоиск вакансий",
        body=(
            '<p>Подходящая вакансия</p><a href="https://hh.ru/vacancy/123456789'
            '?from=email">IT Project Manager</a>'
        ),
        html=True,
    )

    results = parse_mailru_message(raw, uid="43")

    assert len(results) == 1
    assert results[0].title == "IT Project Manager"
    assert results[0].external_id == "hh:123456789"


def test_mailru_unrelated_email_is_ignored():
    raw = _raw_message(subject="Отчёт", body="Еженедельный отчёт по проекту")

    assert parse_mailru_message(raw, uid="44") == []


class FakeImap:
    def __init__(self, raw_message: bytes):
        self.raw_message = raw_message
        self.readonly = None
        self.commands: list[str] = []

    def login(self, email: str, password: str):
        assert email == "bot@example.com"
        assert password == "app-password"
        return "OK", [b"logged in"]

    def select(self, folder: str, readonly: bool = False):
        assert folder == "INBOX"
        self.readonly = readonly
        return "OK", [b"1"]

    def uid(self, command: str, *args):
        self.commands.append(command)
        if command == "search":
            return "OK", [b"42"]
        if command == "fetch":
            assert args[-1] == "(BODY.PEEK[])"
            return "OK", [(b"42 (BODY[])", self.raw_message)]
        raise AssertionError(f"Unexpected command: {command}")

    def logout(self):
        return "BYE", [b"logout"]


def test_mailru_source_uses_readonly_imap():
    raw = _raw_message(
        subject="Вакансии HH",
        body="https://hh.ru/vacancy/111222333",
    )
    fake = FakeImap(raw)
    settings = SimpleNamespace(
        mailru_email="bot@example.com",
        mailru_app_password="app-password",
        mailru_folder="INBOX",
        mailru_lookback_days=2,
        mailru_max_messages=50,
    )

    source = MailRuJobAlertsSource(
        settings,
        client_factory=lambda *args, **kwargs: fake,
    )
    results = source._fetch_sync()

    assert fake.readonly is True
    assert fake.commands == ["search", "fetch"]
    assert len(results) == 1
