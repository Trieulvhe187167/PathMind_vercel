"""Vietnamese preferences survive storage and drive model language directives."""
import pytest

from pathmind.services.settings import interface_settings
from pathmind.services.prompt.language import language_directive, language_label
from pathmind.services.config.loader import parse_language
from pathmind.api.routers.settings import UISettings, UISettingsUpdate, LanguageUpdate


@pytest.mark.parametrize("value", ["vi", "vi-VN", "vietnamese", "Tiếng Việt"])
def test_vietnamese_language_aliases(value):
    assert parse_language(value) == "vi"
    assert interface_settings.resolve_languages({"language": value}) == {
        "language": "vi", "response_language": "vi",
    }


def test_languages_persist_independently(tmp_path, monkeypatch):
    monkeypatch.setattr(interface_settings, "_interface_settings_file", lambda: tmp_path / "interface.json")
    interface_settings.update_ui_settings({"language": "vi", "response_language": "en"})
    assert interface_settings.get_ui_settings()["language"] == "vi"
    assert interface_settings.get_response_language() == "en"
    interface_settings.update_ui_settings({"response_language": "vi"})
    assert interface_settings.get_response_language() == "vi"
    interface_settings.update_ui_settings({"language": "zh"})
    assert interface_settings.get_response_language() == "vi"


def test_api_accepts_vietnamese():
    assert UISettings(language="vi", response_language="vi").language == "vi"
    assert UISettingsUpdate(language="vi", response_language="vi").response_language == "vi"
    assert LanguageUpdate(language="vi").language == "vi"


def test_model_directive_names_vietnamese():
    assert "Vietnamese" in language_label("vi-VN")
    assert "strictly in Vietnamese" in language_directive("vi")
    assert "user explicitly asks" in language_directive("vi", allow_user_override=True)
