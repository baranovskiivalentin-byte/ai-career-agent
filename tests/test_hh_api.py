from hh_api import is_explicitly_remote, normalize_hh_vacancy, strip_html


def test_remote_detected_from_schedule():
    assert is_explicitly_remote({"schedule": {"id": "remote"}})


def test_remote_detected_from_new_work_format():
    assert is_explicitly_remote({"work_format": [{"id": "REMOTE", "name": "Из дома"}]})


def test_hybrid_is_not_remote_without_explicit_remote_text():
    assert not is_explicitly_remote(
        {"schedule": {"id": "flexible", "name": "Гибрид"}, "description": "Офис 3 дня"}
    )


def test_remote_detected_from_description():
    assert is_explicitly_remote({"description": "Полностью удалённо из любой точки"})


def test_normalization_strips_html():
    item = {
        "id": "123",
        "name": "Delivery Manager",
        "employer": {"name": "Example"},
        "description": "<p>Управление <b>командой</b></p>",
        "schedule": {"id": "remote"},
        "alternate_url": "https://hh.ru/vacancy/123",
    }
    result = normalize_hh_vacancy(item, "senior_it")
    assert result.description == "Управление командой"
    assert result.work_format == "remote"
    assert strip_html("a<br>b") == "a b"
