"""
Tests for the translate management command.
"""

from io import StringIO

import polib
import pytest

from django.core.management import call_command
from django.core.management.base import CommandError

from translatebot_django.management.commands.translate import translate_text
from translatebot_django.utils import get_all_po_paths, get_api_key, get_model


def test_get_api_key_from_env(monkeypatch):
    """Test that get_api_key returns key from environment variable."""
    monkeypatch.setenv("TRANSLATEBOT_API_KEY", "test-api-key")
    assert get_api_key() == "test-api-key"


def test_get_api_key_from_settings(settings):
    """Test that get_api_key returns key from Django settings."""
    settings.TRANSLATEBOT_API_KEY = "settings-api-key"
    assert get_api_key() == "settings-api-key"


def test_get_api_key_settings_priority_over_env(settings, monkeypatch):
    """Test that Django settings takes priority over environment variable."""
    monkeypatch.setenv("TRANSLATEBOT_API_KEY", "env-api-key")
    settings.TRANSLATEBOT_API_KEY = "settings-api-key"
    assert get_api_key() == "settings-api-key"


def test_get_api_key_without_config(monkeypatch):
    """Test that get_api_key raises error when no config is found."""
    monkeypatch.delenv("TRANSLATEBOT_API_KEY", raising=False)
    with pytest.raises(CommandError, match="API key not configured"):
        get_api_key()


def test_get_model_from_settings(settings):
    """Test that get_model returns model from Django settings."""
    settings.TRANSLATEBOT_MODEL = "claude-3-sonnet"
    assert get_model() == "claude-3-sonnet"


def test_get_model_without_config():
    """Test that get_model raises error when not configured."""
    with pytest.raises(CommandError, match="Model is not configured"):
        get_model()


def test_get_all_po_paths_finds_files_in_locale_paths(temp_locale_dir):
    """Test that get_all_po_paths finds files in LOCALE_PATHS."""
    nl_dir = temp_locale_dir / "nl" / "LC_MESSAGES"
    nl_dir.mkdir(parents=True)
    po_path = nl_dir / "django.po"
    po_path.write_text("")

    paths = get_all_po_paths("nl")
    assert len(paths) >= 1
    assert po_path in paths


@pytest.mark.usefixtures("temp_locale_dir")
def test_get_all_po_paths_raises_error_when_not_found():
    """Test that get_all_po_paths raises error when no files found."""
    with pytest.raises(CommandError, match="No translation files found"):
        get_all_po_paths("xx")


def test_get_all_po_paths_excludes_third_party_packages(temp_locale_dir):
    """Test that get_all_po_paths excludes third-party packages in site-packages."""
    nl_dir = temp_locale_dir / "nl" / "LC_MESSAGES"
    nl_dir.mkdir(parents=True)
    po_path = nl_dir / "django.po"
    po_path.write_text("")

    paths = get_all_po_paths("nl")

    for path in paths:
        assert "site-packages" not in str(path), f"Third-party package found: {path}"
    assert po_path in paths


def test_finds_po_in_app_locale_directory(tmp_path, settings, mocker):
    """Test that get_all_po_paths finds .po files in app locale directories."""
    app_path = tmp_path / "myapp"
    locale_dir = app_path / "locale" / "nl" / "LC_MESSAGES"
    locale_dir.mkdir(parents=True)
    po_path = locale_dir / "django.po"
    po_path.write_text("")

    settings.LOCALE_PATHS = []

    mock_app = mocker.MagicMock()
    mock_app.path = str(app_path)
    mocker.patch("django.apps.apps.get_app_configs", return_value=[mock_app])

    paths = get_all_po_paths("nl")
    assert po_path in paths


def test_finds_po_in_default_locale_directory(tmp_path, settings, mocker, monkeypatch):
    """Test that get_all_po_paths finds .po files in default locale/ directory."""
    monkeypatch.chdir(tmp_path)

    locale_dir = tmp_path / "locale" / "nl" / "LC_MESSAGES"
    locale_dir.mkdir(parents=True)
    po_path = locale_dir / "django.po"
    po_path.write_text("")

    settings.LOCALE_PATHS = []
    mocker.patch("django.apps.apps.get_app_configs", return_value=[])

    paths = get_all_po_paths("nl")
    assert len(paths) == 1
    assert paths[0].name == "django.po"


def test_translate_text_basic(mocker):
    """Test basic translation with JSON array input/output."""
    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = '["Hallo, wereld!"]'

    mock_completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )
    mock_completion.return_value = mock_response

    result = translate_text(["Hello, world!"], "nl", "gpt-4o-mini", "test-api-key")

    assert result == ["Hallo, wereld!"]
    mock_completion.assert_called_once()


def test_translate_text_preserves_placeholders(mocker):
    """Test that translation prompt includes placeholder preservation."""
    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = '["Welkom bij %(site_name)s"]'

    mock_completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )
    mock_completion.return_value = mock_response

    translate_text(["Welcome to %(site_name)s"], "nl", "gpt-4o-mini", "test-api-key")

    call_args = mock_completion.call_args
    system_message = call_args[1]["messages"][0]["content"]
    assert "%(name)s" in system_message
    assert "preserve" in system_message.lower()


def test_translate_text_uses_correct_model(mocker):
    """Test that the correct model is used."""
    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = '["Test"]'

    mock_completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )
    mock_completion.return_value = mock_response

    translate_text(["Test"], "nl", "claude-3-sonnet", "test-api-key")

    call_args = mock_completion.call_args
    assert call_args[1]["model"] == "claude-3-sonnet"


def test_translate_text_batch(mocker):
    """Test batch translation with multiple strings."""
    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = '["Hallo", "Wereld", "Test"]'

    mock_completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )
    mock_completion.return_value = mock_response

    result = translate_text(
        ["Hello", "World", "Test"], "nl", "gpt-4o-mini", "test-api-key"
    )

    assert result == ["Hallo", "Wereld", "Test"]
    mock_completion.assert_called_once()


def test_command_requires_target_lang():
    """Test that command requires --target-lang argument."""
    with pytest.raises(CommandError, match="--target-lang"):
        call_command("translate")


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_po_file_not_found():
    """Test error when .po file doesn't exist for target language."""
    with pytest.raises(CommandError, match="No translation files found"):
        call_command("translate", target_lang="xx")


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_translates_empty_entries(sample_po_file, mock_completion):
    """Test that command translates entries without msgstr."""
    mock_completion()

    call_command("translate", target_lang="nl")

    po = polib.pofile(str(sample_po_file))
    translated_count = sum(
        1 for entry in po if entry.msgid and not entry.obsolete and entry.msgstr
    )
    assert translated_count > 0


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_dry_run(sample_po_file, mock_completion):
    """Test that --dry-run doesn't save changes."""
    mock_completion()

    original_po = polib.pofile(str(sample_po_file))
    original_entries = [(e.msgid, e.msgstr) for e in original_po]

    out = StringIO()
    call_command("translate", target_lang="nl", dry_run=True, stdout=out)

    po = polib.pofile(str(sample_po_file))
    current_entries = [(e.msgid, e.msgstr) for e in po]
    assert original_entries == current_entries
    assert "Dry run" in out.getvalue()


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_skips_already_translated(sample_po_file, mock_completion):
    """Test that command skips entries that already have msgstr."""
    mock_completion("Nieuwe vertaling")

    call_command("translate", target_lang="nl")

    po = polib.pofile(str(sample_po_file))
    already_translated = [e for e in po if e.msgid == "Already translated"][0]
    assert already_translated.msgstr == "Al vertaald"


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_overwrite_flag(sample_po_file, mock_completion):
    """Test that --overwrite re-translates existing entries."""
    mock_completion("Nieuwe vertaling")

    call_command("translate", target_lang="nl", overwrite=True)

    po = polib.pofile(str(sample_po_file))
    already_translated = [e for e in po if e.msgid == "Already translated"][0]
    assert already_translated.msgstr == "Nieuwe vertaling"


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_skips_obsolete_entries(temp_locale_dir, mocker):
    """Test that obsolete entries are skipped."""
    test_lang = temp_locale_dir / "test" / "LC_MESSAGES"
    test_lang.mkdir(parents=True)
    po_path = test_lang / "django.po"

    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po.append(polib.POEntry(msgid="Obsolete text", msgstr="", obsolete=True))
    po.save(str(po_path))

    mock_comp = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )

    call_command("translate", target_lang="test")

    mock_comp.assert_not_called()


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_output_messages(sample_po_file, mock_completion):
    """Test that command outputs appropriate messages."""
    mock_completion()

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    output = out.getvalue()
    assert "Processing:" in output
    assert "Saved" in output or "Successfully translated" in output


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_batches_large_input(temp_locale_dir, mocker):
    """Test that command batches translations and all entries are translated."""
    import json

    nl_dir = temp_locale_dir / "nl" / "LC_MESSAGES"
    nl_dir.mkdir(parents=True, exist_ok=True)
    po_path = nl_dir / "django.po"

    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}

    num_entries = 50
    for i in range(num_entries):
        po.append(
            polib.POEntry(
                msgid=f"String {i} with extra content to increase token count",
                msgstr="",
            )
        )

    po.save(str(po_path))

    mocker.patch(
        "translatebot_django.management.commands.translate.get_max_tokens",
        return_value=500,
    )

    call_count = 0
    translated_strings = []

    def mock_completion_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        user_content = kwargs["messages"][1]["content"]
        input_strings = json.loads(user_content[user_content.find("[") :])
        translations = [f"Vertaald: {s}" for s in input_strings]
        translated_strings.extend(input_strings)
        mock_resp = mocker.MagicMock()
        mock_resp.choices[0].message.content = json.dumps(translations)
        return mock_resp

    mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    ).side_effect = mock_completion_side_effect

    call_command("translate", target_lang="nl")

    assert call_count > 1, "Expected multiple batches"
    assert len(translated_strings) == num_entries

    po = polib.pofile(str(po_path))
    translated_entries = [e for e in po if e.msgstr and not e.obsolete]
    assert len(translated_entries) == num_entries
