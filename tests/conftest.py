"""
Pytest fixtures for translatebot-django tests.
"""

import json

import polib
import pytest


@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    """Enable database access for all tests."""
    pass


@pytest.fixture
def temp_locale_dir(tmp_path, settings):
    """Create a temporary locale directory structure and configure Django settings."""
    locale_dir = tmp_path / "locale"
    locale_dir.mkdir()
    # Configure Django's LOCALE_PATHS setting to point to our temp directory
    settings.LOCALE_PATHS = [str(locale_dir)]
    return locale_dir


@pytest.fixture
def sample_po_file(temp_locale_dir):
    """Create a sample .po file for testing."""
    nl_dir = temp_locale_dir / "nl" / "LC_MESSAGES"
    nl_dir.mkdir(parents=True)

    po_path = nl_dir / "django.po"

    # Create a PO file with some entries
    po = polib.POFile()
    po.metadata = {
        "Content-Type": "text/plain; charset=utf-8",
    }

    # Add some test entries
    entry1 = polib.POEntry(
        msgid="Hello, world!",
        msgstr="",  # Untranslated
    )
    po.append(entry1)

    entry2 = polib.POEntry(
        msgid="Welcome to %(site_name)s",
        msgstr="",  # Untranslated with placeholder
    )
    po.append(entry2)

    entry3 = polib.POEntry(
        msgid="Already translated",
        msgstr="Al vertaald",  # Already has translation
    )
    po.append(entry3)

    po.save(str(po_path))
    return po_path


@pytest.fixture
def mock_env_api_key(monkeypatch):
    """Set TRANSLATEBOT_API_KEY environment variable for tests."""
    monkeypatch.setenv("TRANSLATEBOT_API_KEY", "test-api-key")


@pytest.fixture
def mock_model_config(settings):
    """Set TRANSLATEBOT_MODEL in Django settings for tests."""
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"


@pytest.fixture
def mock_completion(mocker):
    """Create a mock for litellm completion that returns translated strings."""

    def _create_mock(translation_text="Vertaalde tekst"):
        def side_effect(**kwargs):
            user_content = kwargs["messages"][1]["content"]
            input_strings = json.loads(user_content[user_content.find("[") :])
            if callable(translation_text):
                translations = [translation_text(s) for s in input_strings]
            else:
                translations = [translation_text] * len(input_strings)
            mock_resp = mocker.MagicMock()
            mock_resp.choices[0].message.content = json.dumps(translations)
            return mock_resp

        mock = mocker.patch(
            "translatebot_django.management.commands.translate.completion"
        )
        mock.side_effect = side_effect
        return mock

    return _create_mock
