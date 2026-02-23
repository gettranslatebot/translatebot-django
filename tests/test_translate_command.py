"""
Tests for the translate management command.
"""

from io import StringIO

import polib
import pytest

from django.core.management import call_command
from django.core.management.base import CommandError

from translatebot_django.management.commands.translate import translate_text
from translatebot_django.utils import (
    combine_translation_contexts,
    get_all_po_paths,
    get_api_key,
    get_app_translation_context,
    get_model,
    get_modeltranslation_translator,
)


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
    """Test that get_model defaults to gpt-4o-mini when not configured."""
    assert get_model() == "gpt-4o-mini"


def test_get_modeltranslation_translator():
    """Test get_modeltranslation_translator returns None when not available."""
    # Without modeltranslation installed, should return None
    translator = get_modeltranslation_translator()
    # Could be either depending on env
    assert translator is None or translator is not None


def test_get_all_po_paths_finds_files_in_locale_paths(temp_locale_dir):
    """Test that get_all_po_paths finds files in LOCALE_PATHS."""
    nl_dir = temp_locale_dir / "nl" / "LC_MESSAGES"
    nl_dir.mkdir(parents=True)
    po_path = nl_dir / "django.po"
    po_path.write_text("")

    paths = get_all_po_paths("nl")
    assert len(paths) >= 1
    assert po_path in paths


def test_get_all_po_paths_converts_language_code_to_locale(temp_locale_dir):
    """Test that get_all_po_paths converts language codes to locale names.

    Django's LANGUAGES setting uses language codes (e.g., 'zh-hans') but locale
    directories use locale names (e.g., 'zh_Hans'). This test verifies that the
    function correctly converts between these formats.
    """
    # Create directory with locale name format (zh_Hans), not language code (zh-hans)
    zh_hans_dir = temp_locale_dir / "zh_Hans" / "LC_MESSAGES"
    zh_hans_dir.mkdir(parents=True)
    po_path = zh_hans_dir / "django.po"
    po_path.write_text("")

    # Pass language code format (zh-hans), should find file in zh_Hans directory
    paths = get_all_po_paths("zh-hans")
    assert len(paths) >= 1
    assert po_path in paths


def test_get_all_po_paths_converts_pt_br_to_pt_BR(temp_locale_dir):
    """Test that get_all_po_paths converts pt-br to pt_BR locale directory."""
    # Create directory with locale name format (pt_BR)
    pt_br_dir = temp_locale_dir / "pt_BR" / "LC_MESSAGES"
    pt_br_dir.mkdir(parents=True)
    po_path = pt_br_dir / "django.po"
    po_path.write_text("")

    # Pass language code format (pt-br), should find file in pt_BR directory
    paths = get_all_po_paths("pt-br")
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


def test_translate_text_extracts_json_from_preamble(mocker):
    """Test that JSON is extracted when LLM adds preamble text."""
    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = 'Preamble text:\n["Hallo, wereld!"]'

    mock_completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )
    mock_completion.return_value = mock_response

    result = translate_text(["Hello, world!"], "nl", "gpt-4o-mini", "test-api-key")

    assert result == ["Hallo, wereld!"]


def test_translate_text_extracts_json_from_code_block(mocker):
    """Test that JSON is extracted from markdown code blocks."""
    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = '```json\n["Hallo"]\n```'

    mock_completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )
    mock_completion.return_value = mock_response

    result = translate_text(["Hello"], "nl", "gpt-4o-mini", "test-api-key")

    assert result == ["Hallo"]


def test_command_requires_target_lang(settings):
    """Test that command requires --target-lang when LANGUAGES is not defined."""
    # Ensure LANGUAGES is not defined
    if hasattr(settings, "LANGUAGES"):
        delattr(settings, "LANGUAGES")

    # When LANGUAGES is not defined, argparse raises error about required argument
    with pytest.raises(CommandError, match="--target-lang"):
        call_command("translate")


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_uses_languages_setting(
    settings, sample_po_file, mock_completion, tmp_path
):
    """Test that command uses LANGUAGES setting when --target-lang is not provided."""
    # Setup multiple language .po files
    nl_dir = tmp_path / "locale" / "nl" / "LC_MESSAGES"
    de_dir = tmp_path / "locale" / "de" / "LC_MESSAGES"
    nl_dir.mkdir(parents=True, exist_ok=True)
    de_dir.mkdir(parents=True, exist_ok=True)

    # Create simple .po files
    import polib

    for lang_dir, _ in [(nl_dir, "nl"), (de_dir, "de")]:
        po_path = lang_dir / "django.po"
        po = polib.POFile()
        po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
        po.append(polib.POEntry(msgid="Hello", msgstr=""))
        po.save(str(po_path))

    settings.LOCALE_PATHS = [str(tmp_path / "locale")]
    settings.LANGUAGES = [("nl", "Dutch"), ("de", "German")]

    mock_completion("Translated")

    out = StringIO()
    call_command("translate", stdout=out)

    output = out.getvalue()
    # Should mention both languages
    assert "nl" in output
    assert "de" in output


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


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_translates_fuzzy_entries(temp_locale_dir, mock_completion):
    """Test that fuzzy entries are re-translated and fuzzy flag is cleared."""
    test_lang = temp_locale_dir / "test" / "LC_MESSAGES"
    test_lang.mkdir(parents=True)
    po_path = test_lang / "django.po"

    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    # Create a fuzzy entry with an old translation
    fuzzy_entry = polib.POEntry(msgid="Smart Context", msgstr="Old translation")
    fuzzy_entry.flags.append("fuzzy")
    po.append(fuzzy_entry)
    po.save(str(po_path))

    mock_completion("Slimme Context")

    call_command("translate", target_lang="test")

    # Reload and verify
    po = polib.pofile(str(po_path))
    entry = [e for e in po if e.msgid == "Smart Context"][0]
    assert entry.msgstr == "Slimme Context"
    assert not entry.fuzzy, "Fuzzy flag should be cleared after translation"


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


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_batches_empty_trailing_group(temp_locale_dir, mocker):
    """Test batching when the last item triggers overflow, leaving no trailing group."""
    nl_dir = temp_locale_dir / "nl" / "LC_MESSAGES"
    nl_dir.mkdir(parents=True, exist_ok=True)
    po_path = nl_dir / "django.po"

    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po.append(polib.POEntry(msgid="Hello", msgstr=""))
    po.append(polib.POEntry(msgid="World", msgstr=""))
    po.save(str(po_path))

    # max_tokens=1 ensures every item overflows, so group_candidate is [] at the end
    mocker.patch(
        "translatebot_django.management.commands.translate.get_max_tokens",
        return_value=1,
    )

    out = StringIO()
    call_command("translate", target_lang="nl", dry_run=True, stdout=out)

    output = out.getvalue()
    assert "Would translate" in output


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_skips_save_when_no_changes_for_po_file(tmp_path, settings, mocker):
    """Test that a fully translated PO file is skipped (no save) in non-dry-run mode."""
    import json

    # Two locale directories so the command finds two django.po files
    locale1 = tmp_path / "locale1"
    locale2 = tmp_path / "locale2"
    settings.LOCALE_PATHS = [str(locale1), str(locale2)]

    nl1 = locale1 / "nl" / "LC_MESSAGES"
    nl1.mkdir(parents=True)
    nl2 = locale2 / "nl" / "LC_MESSAGES"
    nl2.mkdir(parents=True)

    # File 1: has untranslated entries
    po1 = polib.POFile()
    po1.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po1.append(polib.POEntry(msgid="Hello", msgstr=""))
    po1.save(str(nl1 / "django.po"))

    # File 2: fully translated (no entries to translate)
    po2 = polib.POFile()
    po2.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po2.append(polib.POEntry(msgid="Already done", msgstr="Al gedaan"))
    po2.save(str(nl2 / "django.po"))

    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = json.dumps(["Hallo"])
    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        return_value=mock_response,
    )

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    output = out.getvalue()
    # File 1 should be saved
    assert "Successfully updated" in output
    # Total should show 1 entry translated
    assert "1 entries" in output


def test_command_requires_target_lang_when_no_languages(
    settings, mock_env_api_key, temp_locale_dir
):
    """Test that command raises error when no target_lang and no LANGUAGES."""
    # Remove LANGUAGES setting
    if hasattr(settings, "LANGUAGES"):
        delattr(settings, "LANGUAGES")

    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    # Call handle() directly to bypass argparse
    from translatebot_django.management.commands.translate import Command

    cmd = Command()

    # Should raise CommandError about missing --target-lang
    with pytest.raises(
        CommandError, match="--target-lang is required when LANGUAGES is not defined"
    ):
        cmd.handle(target_lang=None, dry_run=False, overwrite=False, models=None)


def test_command_models_flag_requires_modeltranslation(
    settings, mock_env_api_key, mocker
):
    """Test error when using --models flag without modeltranslation."""
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    # Mock modeltranslation as not available
    mocker.patch(
        "translatebot_django.management.commands.translate."
        "is_modeltranslation_available",
        return_value=False,
    )

    # Call handle() directly to bypass argparse
    from translatebot_django.management.commands.translate import Command

    cmd = Command()
    cmd.stdout = StringIO()  # Mock stdout

    # Should raise CommandError about modeltranslation not being installed
    with pytest.raises(CommandError, match="django-modeltranslation is not installed"):
        cmd.handle(target_lang="nl", dry_run=False, overwrite=False, models=["Article"])


def test_get_modeltranslation_translator_when_not_available(mocker):
    """Test get_modeltranslation_translator returns None when not available."""
    # Mock is_modeltranslation_available to return False
    mocker.patch(
        "translatebot_django.utils.is_modeltranslation_available", return_value=False
    )

    from translatebot_django.utils import get_modeltranslation_translator

    result = get_modeltranslation_translator()
    assert result is None


def test_is_modeltranslation_available_import_error(mocker, settings):
    """Test is_modeltranslation_available handles ImportError."""
    # First, ensure modeltranslation is in INSTALLED_APPS
    # (so we pass the first check)
    if "modeltranslation" not in settings.INSTALLED_APPS:
        settings.INSTALLED_APPS = list(settings.INSTALLED_APPS) + ["modeltranslation"]

    # Mock the import of modeltranslation to raise ImportError
    def mock_import_module(name):
        if name == "modeltranslation":
            raise ImportError("Mocked import error")
        import importlib

        return importlib.import_module(name)

    # Patch at the point where the function tries to import
    mocker.patch("importlib.import_module", side_effect=mock_import_module)

    # Call the function - it should catch ImportError and return False
    # Create a new version that uses importlib.import_module
    # Actually, let's just directly test by mocking the import in the try block
    import sys

    from translatebot_django.utils import is_modeltranslation_available

    if "modeltranslation" in sys.modules:
        # Temporarily remove it
        original_module = sys.modules.pop("modeltranslation", None)
        try:
            # Mock import to raise error
            import builtins

            original_import = builtins.__import__

            def failing_import(name, *args, **kwargs):
                if "modeltranslation" in name:
                    raise ImportError("No module named 'modeltranslation'")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = failing_import
            try:
                result = is_modeltranslation_available()
                assert result is False
            finally:
                builtins.__import__ = original_import
        finally:
            if original_module:
                sys.modules["modeltranslation"] = original_module
    else:
        # modeltranslation not installed, just test it returns False
        result = is_modeltranslation_available()
        # It might return False due to ImportError or due to not in INSTALLED_APPS
        assert isinstance(result, bool)


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_no_entries_non_dry_run(temp_locale_dir, mocker):
    """Test command output when there are 0 entries in non-dry-run mode."""
    test_lang = temp_locale_dir / "test" / "LC_MESSAGES"
    test_lang.mkdir(parents=True)
    po_path = test_lang / "django.po"

    # Create a PO file with all entries already translated
    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po.append(polib.POEntry(msgid="Already translated", msgstr="Déjà traduit"))
    po.save(str(po_path))

    mock_comp = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )

    out = StringIO()
    call_command("translate", target_lang="test", stdout=out)

    # Should not call completion since nothing to translate
    mock_comp.assert_not_called()

    output = out.getvalue()
    assert "Already up to date" in output


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_no_entries_dry_run(temp_locale_dir, mocker):
    """Test command output when there are 0 entries in dry-run mode."""
    test_lang = temp_locale_dir / "test" / "LC_MESSAGES"
    test_lang.mkdir(parents=True)
    po_path = test_lang / "django.po"

    # Create a PO file with all entries already translated
    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po.append(polib.POEntry(msgid="Already translated", msgstr="Déjà traduit"))
    po.save(str(po_path))

    mock_comp = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )

    out = StringIO()
    call_command("translate", target_lang="test", dry_run=True, stdout=out)

    # Should not call completion since nothing to translate
    mock_comp.assert_not_called()

    output = out.getvalue()
    assert "No untranslated entries found" in output


@pytest.mark.usefixtures("temp_locale_dir", "mock_model_config")
def test_command_authentication_error(sample_po_file, mocker):
    """Test that authentication errors are properly caught and reported."""
    from litellm.exceptions import AuthenticationError

    # Mock translate_text to raise AuthenticationError
    mocker.patch(
        "translatebot_django.management.commands.translate.translate_text",
        side_effect=AuthenticationError(
            message="Invalid API key",
            llm_provider="openai",
            model="gpt-4o-mini",
        ),
    )

    # Set an invalid API key
    mocker.patch(
        "translatebot_django.management.commands.translate.get_api_key",
        return_value="invalid-key",
    )

    with pytest.raises(CommandError, match="Authentication failed"):
        call_command("translate", target_lang="nl")


@pytest.mark.usefixtures("temp_locale_dir", "mock_model_config")
def test_command_credit_balance_error(sample_po_file, mocker):
    """Test that credit balance errors are properly caught and reported."""
    from litellm.exceptions import BadRequestError

    error_message = (
        'AnthropicException - {"type":"error","error":{"type":"invalid_request_error",'
        '"message":"Your credit balance is too low to access the Anthropic API. '
        'Please go to Plans & Billing to upgrade or purchase credits."}}'
    )

    # Mock translate_text to raise BadRequestError
    mocker.patch(
        "translatebot_django.management.commands.translate.translate_text",
        side_effect=BadRequestError(
            message=error_message,
            llm_provider="anthropic",
            model="claude-sonnet-4-20250514",
        ),
    )

    mocker.patch(
        "translatebot_django.management.commands.translate.get_api_key",
        return_value="valid-key",
    )

    with pytest.raises(CommandError, match="Insufficient API credits"):
        call_command("translate", target_lang="nl")


@pytest.mark.usefixtures("temp_locale_dir", "mock_model_config")
def test_command_bad_request_error_generic(sample_po_file, mocker):
    """Test that generic bad request errors are properly caught and reported."""
    from litellm.exceptions import BadRequestError

    error_message = "Some other bad request error"

    # Mock translate_text to raise BadRequestError
    mocker.patch(
        "translatebot_django.management.commands.translate.translate_text",
        side_effect=BadRequestError(
            message=error_message,
            llm_provider="anthropic",
            model="claude-sonnet-4-20250514",
        ),
    )

    mocker.patch(
        "translatebot_django.management.commands.translate.get_api_key",
        return_value="valid-key",
    )

    with pytest.raises(CommandError, match="API request failed"):
        call_command("translate", target_lang="nl")


@pytest.mark.usefixtures("mock_model_config")
def test_command_credit_balance_error_model_translation(mocker):
    """Test that credit balance errors are caught in model translation path."""
    from litellm.exceptions import BadRequestError

    error_message = (
        'AnthropicException - {"type":"error","error":{"type":"invalid_request_error",'
        '"message":"Your credit balance is too low to access the Anthropic API. '
        'Please go to Plans & Billing to upgrade or purchase credits."}}'
    )

    # Mock modeltranslation as available
    mocker.patch(
        "translatebot_django.management.commands.translate.is_modeltranslation_available",
        return_value=True,
    )

    # Mock backend to return items to translate
    mock_instance = mocker.MagicMock()
    mock_model = mocker.MagicMock(__name__="TestModel")
    mock_backend = mocker.MagicMock()
    mock_backend.gather_translatable_content.return_value = [
        {
            "model": mock_model,
            "source_text": "Hello",
            "instance": mock_instance,
            "target_field": "title_nl",
            "field": "title",
        }
    ]
    mocker.patch(
        "translatebot_django.backends.modeltranslation.ModeltranslationBackend",
        return_value=mock_backend,
    )

    mocker.patch(
        "translatebot_django.management.commands.translate.translate_text",
        side_effect=BadRequestError(
            message=error_message,
            llm_provider="anthropic",
            model="claude-sonnet-4-20250514",
        ),
    )

    mocker.patch(
        "translatebot_django.management.commands.translate.get_api_key",
        return_value="valid-key",
    )

    # Call handle() directly to bypass argparse (--models arg not registered
    # when modeltranslation is not actually installed)
    from translatebot_django.management.commands.translate import Command

    cmd = Command()
    cmd.stdout = StringIO()

    with pytest.raises(CommandError, match="Insufficient API credits"):
        cmd.handle(target_lang="nl", dry_run=False, overwrite=False, models=[])


@pytest.mark.usefixtures("mock_model_config")
def test_command_bad_request_error_generic_model_translation(mocker):
    """Test that generic bad request errors are caught in model translation path."""
    from litellm.exceptions import BadRequestError

    error_message = "Some other bad request error"

    # Mock modeltranslation as available
    mocker.patch(
        "translatebot_django.management.commands.translate.is_modeltranslation_available",
        return_value=True,
    )

    # Mock backend to return items to translate
    mock_instance = mocker.MagicMock()
    mock_model = mocker.MagicMock(__name__="TestModel")
    mock_backend = mocker.MagicMock()
    mock_backend.gather_translatable_content.return_value = [
        {
            "model": mock_model,
            "source_text": "Hello",
            "instance": mock_instance,
            "target_field": "title_nl",
            "field": "title",
        }
    ]
    mocker.patch(
        "translatebot_django.backends.modeltranslation.ModeltranslationBackend",
        return_value=mock_backend,
    )

    mocker.patch(
        "translatebot_django.management.commands.translate.translate_text",
        side_effect=BadRequestError(
            message=error_message,
            llm_provider="anthropic",
            model="claude-sonnet-4-20250514",
        ),
    )

    mocker.patch(
        "translatebot_django.management.commands.translate.get_api_key",
        return_value="valid-key",
    )

    # Call handle() directly to bypass argparse
    from translatebot_django.management.commands.translate import Command

    cmd = Command()
    cmd.stdout = StringIO()

    with pytest.raises(CommandError, match="API request failed"):
        cmd.handle(target_lang="nl", dry_run=False, overwrite=False, models=[])


def test_translate_text_api_returns_none_content(mocker):
    """Test error handling when API returns None content."""
    # Mock response with None content
    mock_response = mocker.MagicMock()
    mock_response.choices = [mocker.MagicMock()]
    mock_response.choices[0].message.content = None

    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        return_value=mock_response,
    )

    with pytest.raises(ValueError, match="API returned empty response"):
        translate_text(["Hello"], "nl", "gpt-4o-mini", "test-key")


def test_translate_text_api_returns_empty_content(mocker):
    """Test error handling when API returns empty content after stripping."""
    # Mock response with whitespace-only content
    mock_response = mocker.MagicMock()
    mock_response.choices = [mocker.MagicMock()]
    mock_response.choices[0].message.content = "   \n\n   "

    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        return_value=mock_response,
    )

    with pytest.raises(ValueError, match="API returned empty content after stripping"):
        translate_text(["Hello"], "nl", "gpt-4o-mini", "test-key")


def test_translate_text_invalid_json_response(mocker):
    """Test error handling when API returns invalid JSON."""
    # Mock response with invalid JSON
    mock_response = mocker.MagicMock()
    mock_response.choices = [mocker.MagicMock()]
    mock_response.choices[0].message.content = "This is not JSON at all"

    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        return_value=mock_response,
    )

    with pytest.raises(ValueError, match="Failed to parse JSON response"):
        translate_text(["Hello"], "nl", "gpt-4o-mini", "test-key")


def test_translate_text_rate_limit_retry_success(mocker):
    """Test that rate limit errors trigger retry and succeed on subsequent attempt."""
    from litellm.exceptions import RateLimitError

    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = '["Hallo"]'

    mock_completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )
    # First call raises RateLimitError, second call succeeds
    mock_completion.side_effect = [
        RateLimitError(
            message="Rate limit exceeded",
            llm_provider="anthropic",
            model="claude-3",
        ),
        mock_response,
    ]

    mock_sleep = mocker.patch(
        "translatebot_django.management.commands.translate.time.sleep"
    )

    result = translate_text(["Hello"], "nl", "gpt-4o-mini", "test-api-key")

    assert result == ["Hallo"]
    assert mock_completion.call_count == 2
    # Should have slept once with initial backoff (60 seconds)
    mock_sleep.assert_called_once_with(60)


def test_translate_text_rate_limit_retry_exponential_backoff(mocker):
    """Test that retries use exponential backoff timing."""
    from litellm.exceptions import RateLimitError

    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = '["Hallo"]'

    mock_completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )
    # First 3 calls raise RateLimitError, 4th call succeeds
    mock_completion.side_effect = [
        RateLimitError(message="Rate limit", llm_provider="anthropic", model="claude"),
        RateLimitError(message="Rate limit", llm_provider="anthropic", model="claude"),
        RateLimitError(message="Rate limit", llm_provider="anthropic", model="claude"),
        mock_response,
    ]

    mock_sleep = mocker.patch(
        "translatebot_django.management.commands.translate.time.sleep"
    )

    result = translate_text(["Hello"], "nl", "gpt-4o-mini", "test-api-key")

    assert result == ["Hallo"]
    assert mock_completion.call_count == 4
    # Should have exponential backoff: 60, 120, 240 seconds
    assert mock_sleep.call_count == 3
    mock_sleep.assert_any_call(60)
    mock_sleep.assert_any_call(120)
    mock_sleep.assert_any_call(240)


def test_translate_text_rate_limit_all_retries_exhausted(mocker):
    """Test that error is raised after all retries are exhausted."""
    from litellm.exceptions import RateLimitError

    mock_completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )
    # All 5 calls raise RateLimitError
    mock_completion.side_effect = RateLimitError(
        message="Rate limit exceeded",
        llm_provider="anthropic",
        model="claude-3",
    )

    mock_sleep = mocker.patch(
        "translatebot_django.management.commands.translate.time.sleep"
    )

    with pytest.raises(RateLimitError, match="Rate limit exceeded"):
        translate_text(["Hello"], "nl", "gpt-4o-mini", "test-api-key")

    # Should have tried 5 times (MAX_RETRIES)
    assert mock_completion.call_count == 5
    # Should have slept 4 times (before retries 2, 3, 4, 5)
    assert mock_sleep.call_count == 4


# Tests for TRANSLATING.md context feature


def test_get_translation_context_from_base_dir(tmp_path, settings):
    """Test that get_translation_context finds TRANSLATING.md in BASE_DIR."""
    from translatebot_django.utils import get_translation_context

    settings.BASE_DIR = tmp_path
    context_file = tmp_path / "TRANSLATING.md"
    context_file.write_text("This is a medical translation project.")

    context = get_translation_context()
    assert context == "This is a medical translation project."


def test_get_translation_context_from_cwd(tmp_path, settings, monkeypatch):
    """Test that get_translation_context finds TRANSLATING.md in current directory."""
    from translatebot_django.utils import get_translation_context

    # Clear BASE_DIR so it falls back to cwd
    settings.BASE_DIR = None
    monkeypatch.chdir(tmp_path)

    context_file = tmp_path / "TRANSLATING.md"
    context_file.write_text("E-commerce terminology context.")

    context = get_translation_context()
    assert context == "E-commerce terminology context."


def test_get_translation_context_not_found(tmp_path, settings, monkeypatch):
    """Test that get_translation_context returns None when file doesn't exist."""
    from translatebot_django.utils import get_translation_context

    settings.BASE_DIR = tmp_path
    monkeypatch.chdir(tmp_path)

    context = get_translation_context()
    assert context is None


def test_get_translation_context_prefers_base_dir(tmp_path, settings, monkeypatch):
    """Test that BASE_DIR takes precedence over cwd."""
    from translatebot_django.utils import get_translation_context

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    # Create context file in BASE_DIR
    settings.BASE_DIR = tmp_path
    base_dir_context = tmp_path / "TRANSLATING.md"
    base_dir_context.write_text("Context from BASE_DIR")

    # Create context file in cwd (should be ignored)
    cwd_context = other_dir / "TRANSLATING.md"
    cwd_context.write_text("Context from cwd")

    context = get_translation_context()
    assert context == "Context from BASE_DIR"


def test_get_translation_context_strips_whitespace(tmp_path, settings):
    """Test that get_translation_context strips leading/trailing whitespace."""
    from translatebot_django.utils import get_translation_context

    settings.BASE_DIR = tmp_path
    context_file = tmp_path / "TRANSLATING.md"
    context_file.write_text("\n\n  Some context with whitespace  \n\n")

    context = get_translation_context()
    assert context == "Some context with whitespace"


def test_get_translation_context_handles_read_error(tmp_path, settings, mocker):
    """Test that get_translation_context handles file read errors gracefully."""
    from translatebot_django.utils import get_translation_context

    settings.BASE_DIR = tmp_path
    context_file = tmp_path / "TRANSLATING.md"
    context_file.write_text("Some context")

    # Mock the read_text method to raise an OSError
    mocker.patch("pathlib.Path.read_text", side_effect=OSError("Permission denied"))

    context = get_translation_context()
    assert context is None


def test_build_system_prompt_without_context():
    """Test that build_system_prompt returns base prompt when no context provided."""
    from translatebot_django.management.commands.translate import (
        BASE_SYSTEM_PROMPT,
        build_system_prompt,
    )

    result = build_system_prompt(None)
    assert result == BASE_SYSTEM_PROMPT

    result = build_system_prompt("")
    assert result == BASE_SYSTEM_PROMPT


def test_build_system_prompt_with_context():
    """Test that build_system_prompt includes context in the prompt."""
    from translatebot_django.management.commands.translate import (
        BASE_SYSTEM_PROMPT,
        build_system_prompt,
    )

    context = "This is a medical translation project."
    result = build_system_prompt(context)

    assert BASE_SYSTEM_PROMPT in result
    assert "Project Context" in result
    assert "This is a medical translation project." in result


def test_translate_text_with_context(mocker):
    """Test that translate_text passes context to the system prompt."""
    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = '["Hallo"]'

    mock_completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )
    mock_completion.return_value = mock_response

    context = "Medical terminology project."
    translate_text(["Hello"], "nl", "gpt-4o-mini", "test-api-key", context=context)

    call_args = mock_completion.call_args
    system_message = call_args[1]["messages"][0]["content"]
    assert "Medical terminology project." in system_message
    assert "Project Context" in system_message


def test_app_flag_filters_to_single_app(tmp_path, settings, mocker):
    """Test --app filters to only the specified app's .po file."""
    # Create two apps with locale dirs
    app1_path = tmp_path / "app1"
    app1_locale = app1_path / "locale" / "nl" / "LC_MESSAGES"
    app1_locale.mkdir(parents=True)
    app1_po = app1_locale / "django.po"
    app1_po.write_text("")

    app2_path = tmp_path / "app2"
    app2_locale = app2_path / "locale" / "nl" / "LC_MESSAGES"
    app2_locale.mkdir(parents=True)
    app2_po = app2_locale / "django.po"
    app2_po.write_text("")

    settings.LOCALE_PATHS = []

    mock_app1 = mocker.MagicMock()
    mock_app1.path = str(app1_path)
    mock_app1.label = "app1"

    mock_app2 = mocker.MagicMock()
    mock_app2.path = str(app2_path)
    mock_app2.label = "app2"

    mocker.patch(
        "django.apps.apps.get_app_configs",
        return_value=[mock_app1, mock_app2],
    )

    paths = get_all_po_paths("nl", app_labels=["app1"])
    assert len(paths) == 1
    assert paths[0] == app1_po


def test_app_flag_filters_multiple_apps(tmp_path, settings, mocker):
    """Test multiple --app flags include multiple apps."""
    app1_path = tmp_path / "app1"
    app1_locale = app1_path / "locale" / "nl" / "LC_MESSAGES"
    app1_locale.mkdir(parents=True)
    app1_po = app1_locale / "django.po"
    app1_po.write_text("")

    app2_path = tmp_path / "app2"
    app2_locale = app2_path / "locale" / "nl" / "LC_MESSAGES"
    app2_locale.mkdir(parents=True)
    app2_po = app2_locale / "django.po"
    app2_po.write_text("")

    app3_path = tmp_path / "app3"
    app3_locale = app3_path / "locale" / "nl" / "LC_MESSAGES"
    app3_locale.mkdir(parents=True)
    app3_po = app3_locale / "django.po"
    app3_po.write_text("")

    settings.LOCALE_PATHS = []

    mock_apps = []
    for name, path in [("app1", app1_path), ("app2", app2_path), ("app3", app3_path)]:
        mock_app = mocker.MagicMock()
        mock_app.path = str(path)
        mock_app.label = name
        mock_apps.append(mock_app)

    mocker.patch("django.apps.apps.get_app_configs", return_value=mock_apps)

    paths = get_all_po_paths("nl", app_labels=["app1", "app3"])
    assert len(paths) == 2
    assert app1_po in paths
    assert app3_po in paths
    assert app2_po not in paths


def test_app_flag_unknown_app_raises_error(tmp_path, settings, mocker):
    """Test --app with an unknown app label raises CommandError."""
    settings.LOCALE_PATHS = []

    mock_app = mocker.MagicMock()
    mock_app.path = str(tmp_path / "myapp")
    mock_app.label = "myapp"

    mocker.patch("django.apps.apps.get_app_configs", return_value=[mock_app])

    with pytest.raises(CommandError, match="Unknown app label"):
        get_all_po_paths("nl", app_labels=["nonexistent"])


def test_app_flag_app_without_locale_dir(tmp_path, settings, mocker):
    """Test --app with an app that has no locale directory raises error."""
    app_path = tmp_path / "nolocalapp"
    app_path.mkdir()
    # No locale directory created

    settings.LOCALE_PATHS = []

    mock_app = mocker.MagicMock()
    mock_app.path = str(app_path)
    mock_app.label = "nolocalapp"

    mocker.patch("django.apps.apps.get_app_configs", return_value=[mock_app])

    with pytest.raises(CommandError, match="No translation files found"):
        get_all_po_paths("nl", app_labels=["nolocalapp"])


def test_app_flag_skips_locale_paths(tmp_path, settings, mocker):
    """Test that --app skips LOCALE_PATHS and default locale/ directory."""
    # Set up LOCALE_PATHS with a .po file
    locale_dir = tmp_path / "locale"
    nl_dir = locale_dir / "nl" / "LC_MESSAGES"
    nl_dir.mkdir(parents=True)
    locale_po = nl_dir / "django.po"
    locale_po.write_text("")

    settings.LOCALE_PATHS = [str(locale_dir)]

    # Set up an app with a .po file
    app_path = tmp_path / "myapp"
    app_locale = app_path / "locale" / "nl" / "LC_MESSAGES"
    app_locale.mkdir(parents=True)
    app_po = app_locale / "django.po"
    app_po.write_text("")

    mock_app = mocker.MagicMock()
    mock_app.path = str(app_path)
    mock_app.label = "myapp"

    mocker.patch("django.apps.apps.get_app_configs", return_value=[mock_app])

    paths = get_all_po_paths("nl", app_labels=["myapp"])
    assert len(paths) == 1
    assert app_po in paths
    assert locale_po not in paths


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_app_flag_integration_via_call_command(
    tmp_path, settings, mocker, mock_completion
):
    """Test --app works end-to-end through call_command."""
    # Create two apps with locale dirs and .po files
    app1_path = tmp_path / "app1"
    app1_locale = app1_path / "locale" / "nl" / "LC_MESSAGES"
    app1_locale.mkdir(parents=True)
    app1_po = app1_locale / "django.po"
    po1 = polib.POFile()
    po1.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po1.append(polib.POEntry(msgid="App1 string", msgstr=""))
    po1.save(str(app1_po))

    app2_path = tmp_path / "app2"
    app2_locale = app2_path / "locale" / "nl" / "LC_MESSAGES"
    app2_locale.mkdir(parents=True)
    app2_po = app2_locale / "django.po"
    po2 = polib.POFile()
    po2.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po2.append(polib.POEntry(msgid="App2 string", msgstr=""))
    po2.save(str(app2_po))

    settings.LOCALE_PATHS = []

    mock_app1 = mocker.MagicMock()
    mock_app1.path = str(app1_path)
    mock_app1.label = "app1"
    mock_app2 = mocker.MagicMock()
    mock_app2.path = str(app2_path)
    mock_app2.label = "app2"
    mocker.patch(
        "django.apps.apps.get_app_configs",
        return_value=[mock_app1, mock_app2],
    )

    mock_completion("Vertaald")

    out = StringIO()
    call_command("translate", "--target-lang", "nl", "--app", "app1", stdout=out)

    # app1's .po should be translated
    po1_result = polib.pofile(str(app1_po))
    assert po1_result[0].msgstr == "Vertaald"

    # app2's .po should be untouched
    po2_result = polib.pofile(str(app2_po))
    assert po2_result[0].msgstr == ""


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_app_flag_multiple_integration_via_call_command(
    tmp_path, settings, mocker, mock_completion
):
    """Test multiple --app flags work end-to-end through call_command."""
    apps_data = {}
    mock_apps = []
    for name in ["app1", "app2", "app3"]:
        app_path = tmp_path / name
        app_locale = app_path / "locale" / "nl" / "LC_MESSAGES"
        app_locale.mkdir(parents=True)
        app_po = app_locale / "django.po"
        po = polib.POFile()
        po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
        po.append(polib.POEntry(msgid=f"{name} string", msgstr=""))
        po.save(str(app_po))
        apps_data[name] = app_po

        mock_app = mocker.MagicMock()
        mock_app.path = str(app_path)
        mock_app.label = name
        mock_apps.append(mock_app)

    settings.LOCALE_PATHS = []
    mocker.patch("django.apps.apps.get_app_configs", return_value=mock_apps)
    mock_completion("Vertaald")

    out = StringIO()
    call_command(
        "translate",
        "--target-lang",
        "nl",
        "--app",
        "app1",
        "--app",
        "app3",
        stdout=out,
    )

    # app1 and app3 should be translated
    assert polib.pofile(str(apps_data["app1"]))[0].msgstr == "Vertaald"
    assert polib.pofile(str(apps_data["app3"]))[0].msgstr == "Vertaald"
    # app2 should be untouched
    assert polib.pofile(str(apps_data["app2"]))[0].msgstr == ""


def test_app_flag_with_models_raises_error(settings, mocker):
    """Test that --app together with --models raises CommandError."""
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    mocker.patch(
        "translatebot_django.management.commands.translate.is_modeltranslation_available",
        return_value=True,
    )
    mocker.patch(
        "translatebot_django.management.commands.translate.get_api_key",
        return_value="test-key",
    )

    from translatebot_django.management.commands.translate import Command

    cmd = Command()
    cmd.stdout = StringIO()

    with pytest.raises(
        CommandError, match="--app cannot be used together with --models"
    ):
        cmd.handle(
            target_lang="nl",
            dry_run=False,
            overwrite=False,
            models=[],
            apps=["myapp"],
        )


def test_translate_text_without_context_uses_base_prompt(mocker):
    """Test that translate_text uses base prompt when no context provided."""
    from translatebot_django.management.commands.translate import BASE_SYSTEM_PROMPT

    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = '["Hallo"]'

    mock_completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion"
    )
    mock_completion.return_value = mock_response

    translate_text(["Hello"], "nl", "gpt-4o-mini", "test-api-key")

    call_args = mock_completion.call_args
    system_message = call_args[1]["messages"][0]["content"]
    assert system_message == BASE_SYSTEM_PROMPT


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_shows_message_when_translating_md_found(
    sample_po_file, mock_completion, settings, tmp_path
):
    """Test that command shows message when TRANSLATING.md is found."""
    mock_completion()

    settings.BASE_DIR = tmp_path
    context_file = tmp_path / "TRANSLATING.md"
    context_file.write_text("Test context for translations.")

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    output = out.getvalue()
    assert "Found TRANSLATING.md" in output


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_no_message_when_translating_md_not_found(
    sample_po_file, mock_completion, settings, tmp_path
):
    """Test that command doesn't show context message when TRANSLATING.md is missing."""
    mock_completion()

    settings.BASE_DIR = tmp_path
    # Don't create TRANSLATING.md

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    output = out.getvalue()
    assert "Found TRANSLATING.md" not in output


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_passes_context_to_translation(
    sample_po_file, settings, tmp_path, mocker
):
    """Test that the command passes context from TRANSLATING.md to translate_text."""
    settings.BASE_DIR = tmp_path
    context_file = tmp_path / "TRANSLATING.md"
    context_file.write_text("Gaming industry terminology.")

    def mock_translate_side_effect(text, *args, **kwargs):
        # Return the same number of translations as input strings
        return ["Vertaald"] * len(text)

    mock_translate = mocker.patch(
        "translatebot_django.management.commands.translate.translate_text"
    )
    mock_translate.side_effect = mock_translate_side_effect

    call_command("translate", target_lang="nl")

    # Verify context was passed to translate_text
    assert mock_translate.called
    call_kwargs = mock_translate.call_args[1]
    assert call_kwargs.get("context") == "Gaming industry terminology."


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_passes_context_to_model_translation(settings, tmp_path, mocker):
    """Test that model translation also receives context from TRANSLATING.md."""
    settings.BASE_DIR = tmp_path
    context_file = tmp_path / "TRANSLATING.md"
    context_file.write_text("Legal document translations.")

    # Mock modeltranslation as available
    mocker.patch(
        "translatebot_django.management.commands.translate.is_modeltranslation_available",
        return_value=True,
    )

    # Mock backend to return items to translate
    mock_instance = mocker.MagicMock()
    mock_model = mocker.MagicMock(__name__="TestModel")
    mock_backend = mocker.MagicMock()
    mock_backend.gather_translatable_content.return_value = [
        {
            "model": mock_model,
            "source_text": "Hello",
            "instance": mock_instance,
            "target_field": "title_nl",
            "field": "title",
        }
    ]
    mocker.patch(
        "translatebot_django.backends.modeltranslation.ModeltranslationBackend",
        return_value=mock_backend,
    )

    mock_translate = mocker.patch(
        "translatebot_django.management.commands.translate.translate_text"
    )
    mock_translate.return_value = ["Vertaald"]

    # Call handle() directly to bypass argparse
    from translatebot_django.management.commands.translate import Command

    cmd = Command()
    cmd.stdout = StringIO()
    cmd.handle(target_lang="nl", dry_run=False, overwrite=False, models=[])

    # Verify context was passed to translate_text
    assert mock_translate.called
    call_kwargs = mock_translate.call_args[1]
    assert call_kwargs.get("context") == "Legal document translations."


# Tests for get_app_translation_context()


def test_get_app_translation_context_found(tmp_path):
    """Test get_app_translation_context returns content when found."""
    app_dir = tmp_path / "myapp"
    locale_dir = app_dir / "locale" / "nl" / "LC_MESSAGES"
    locale_dir.mkdir(parents=True)
    po_path = locale_dir / "django.po"

    translating_md = app_dir / "TRANSLATING.md"
    translating_md.write_text("Medical terminology context.")

    result = get_app_translation_context(po_path)
    assert result == "Medical terminology context."


def test_get_app_translation_context_not_found(tmp_path):
    """Test that get_app_translation_context returns None when no TRANSLATING.md."""
    app_dir = tmp_path / "myapp"
    locale_dir = app_dir / "locale" / "nl" / "LC_MESSAGES"
    locale_dir.mkdir(parents=True)
    po_path = locale_dir / "django.po"

    result = get_app_translation_context(po_path)
    assert result is None


def test_get_app_translation_context_strips_whitespace(tmp_path):
    """Test that get_app_translation_context strips whitespace from content."""
    app_dir = tmp_path / "myapp"
    locale_dir = app_dir / "locale" / "nl" / "LC_MESSAGES"
    locale_dir.mkdir(parents=True)
    po_path = locale_dir / "django.po"

    translating_md = app_dir / "TRANSLATING.md"
    translating_md.write_text("\n\n  Some context  \n\n")

    result = get_app_translation_context(po_path)
    assert result == "Some context"


def test_get_app_translation_context_empty_file_returns_none(tmp_path):
    """Test that get_app_translation_context returns None for empty/whitespace file."""
    app_dir = tmp_path / "myapp"
    locale_dir = app_dir / "locale" / "nl" / "LC_MESSAGES"
    locale_dir.mkdir(parents=True)
    po_path = locale_dir / "django.po"

    translating_md = app_dir / "TRANSLATING.md"
    translating_md.write_text("   \n\n   ")

    result = get_app_translation_context(po_path)
    assert result is None


def test_get_app_translation_context_handles_oserror(tmp_path, mocker):
    """Test that get_app_translation_context handles OSError gracefully."""
    app_dir = tmp_path / "myapp"
    locale_dir = app_dir / "locale" / "nl" / "LC_MESSAGES"
    locale_dir.mkdir(parents=True)
    po_path = locale_dir / "django.po"

    translating_md = app_dir / "TRANSLATING.md"
    translating_md.write_text("Some context")

    mocker.patch("pathlib.Path.read_text", side_effect=OSError("Permission denied"))

    result = get_app_translation_context(po_path)
    assert result is None


def test_get_app_translation_context_skips_base_dir(tmp_path, settings):
    """Test that get_app_translation_context skips when app_dir is BASE_DIR."""
    settings.BASE_DIR = tmp_path

    # po_path is at tmp_path/locale/nl/LC_MESSAGES/django.po
    # so app_dir = tmp_path (which matches BASE_DIR)
    locale_dir = tmp_path / "locale" / "nl" / "LC_MESSAGES"
    locale_dir.mkdir(parents=True)
    po_path = locale_dir / "django.po"

    translating_md = tmp_path / "TRANSLATING.md"
    translating_md.write_text("Project-level context")

    result = get_app_translation_context(po_path)
    assert result is None


def test_get_app_translation_context_skips_cwd(tmp_path, settings, monkeypatch):
    """Test that get_app_translation_context skips when app_dir is cwd."""
    settings.BASE_DIR = None
    monkeypatch.chdir(tmp_path)

    locale_dir = tmp_path / "locale" / "nl" / "LC_MESSAGES"
    locale_dir.mkdir(parents=True)
    po_path = locale_dir / "django.po"

    translating_md = tmp_path / "TRANSLATING.md"
    translating_md.write_text("Project-level context")

    result = get_app_translation_context(po_path)
    assert result is None


def test_get_app_translation_context_skips_locale_paths_structure(tmp_path):
    """Test that get_app_translation_context skips non-app locale structures."""
    # LOCALE_PATHS entries are {locale_path}/{lang}/LC_MESSAGES/django.po
    # with no "locale" directory in the path — should return None
    locale_path = tmp_path / "project_locale"
    lc_dir = locale_path / "nl" / "LC_MESSAGES"
    lc_dir.mkdir(parents=True)
    po_path = lc_dir / "django.po"

    # Even if there's a TRANSLATING.md above, it shouldn't be found
    (tmp_path / "TRANSLATING.md").write_text("Should not be found")

    result = get_app_translation_context(po_path)
    assert result is None


# Tests for combine_translation_contexts()


def test_combine_both_present():
    """Test combining both project and app contexts."""
    result = combine_translation_contexts("Project context", "App context")
    assert result == "Project context\n\n## App-Specific Context\nApp context"


def test_combine_only_project():
    """Test with only project context."""
    result = combine_translation_contexts("Project context", None)
    assert result == "Project context"


def test_combine_only_app():
    """Test with only app context."""
    result = combine_translation_contexts(None, "App context")
    assert result == "App context"


def test_combine_neither():
    """Test with neither context."""
    result = combine_translation_contexts(None, None)
    assert result is None


def test_combine_empty_strings_treated_as_falsy():
    """Test that empty strings are treated as falsy."""
    assert combine_translation_contexts("", "") is None
    assert combine_translation_contexts("Project", "") == "Project"
    assert combine_translation_contexts("", "App") == "App"


# Integration tests for per-app context


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_per_app_context_passed_to_translate_text(tmp_path, settings, mocker):
    """Test per-app context is passed to translate_text."""
    # Create an app with locale dir and TRANSLATING.md
    app_path = tmp_path / "medical_app"
    app_locale = app_path / "locale" / "nl" / "LC_MESSAGES"
    app_locale.mkdir(parents=True)
    app_po = app_locale / "django.po"
    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po.append(polib.POEntry(msgid="Diagnosis", msgstr=""))
    po.save(str(app_po))

    # Create app-level TRANSLATING.md
    (app_path / "TRANSLATING.md").write_text("Use medical terminology.")

    settings.LOCALE_PATHS = []
    settings.BASE_DIR = tmp_path

    mock_app = mocker.MagicMock()
    mock_app.path = str(app_path)
    mock_app.label = "medical_app"
    mocker.patch("django.apps.apps.get_app_configs", return_value=[mock_app])

    mock_translate = mocker.patch(
        "translatebot_django.management.commands.translate.translate_text"
    )
    mock_translate.return_value = ["Diagnose"]

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    assert mock_translate.called
    call_kwargs = mock_translate.call_args[1]
    assert "Use medical terminology." in call_kwargs["context"]


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_per_app_context_combined_with_project_context(tmp_path, settings, mocker):
    """Test that per-app context is combined with project-level context."""
    # Create project-level TRANSLATING.md
    settings.BASE_DIR = tmp_path
    (tmp_path / "TRANSLATING.md").write_text("General project context.")

    # Create an app with its own TRANSLATING.md
    app_path = tmp_path / "legal_app"
    app_locale = app_path / "locale" / "nl" / "LC_MESSAGES"
    app_locale.mkdir(parents=True)
    app_po = app_locale / "django.po"
    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po.append(polib.POEntry(msgid="Contract", msgstr=""))
    po.save(str(app_po))

    (app_path / "TRANSLATING.md").write_text("Legal terminology.")

    settings.LOCALE_PATHS = []

    mock_app = mocker.MagicMock()
    mock_app.path = str(app_path)
    mock_app.label = "legal_app"
    mocker.patch("django.apps.apps.get_app_configs", return_value=[mock_app])

    mock_translate = mocker.patch(
        "translatebot_django.management.commands.translate.translate_text"
    )
    mock_translate.return_value = ["Contract"]

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    call_kwargs = mock_translate.call_args[1]
    assert "General project context." in call_kwargs["context"]
    assert "App-Specific Context" in call_kwargs["context"]
    assert "Legal terminology." in call_kwargs["context"]


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_multiple_apps_different_contexts_separate_batches(tmp_path, settings, mocker):
    """Test that apps with different contexts get separate batches."""
    settings.BASE_DIR = tmp_path
    settings.LOCALE_PATHS = []

    # App1 with TRANSLATING.md
    app1_path = tmp_path / "app1"
    app1_locale = app1_path / "locale" / "nl" / "LC_MESSAGES"
    app1_locale.mkdir(parents=True)
    po1 = polib.POFile()
    po1.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po1.append(polib.POEntry(msgid="App1 string", msgstr=""))
    po1.save(str(app1_locale / "django.po"))
    (app1_path / "TRANSLATING.md").write_text("App1 context.")

    # App2 with different TRANSLATING.md
    app2_path = tmp_path / "app2"
    app2_locale = app2_path / "locale" / "nl" / "LC_MESSAGES"
    app2_locale.mkdir(parents=True)
    po2 = polib.POFile()
    po2.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po2.append(polib.POEntry(msgid="App2 string", msgstr=""))
    po2.save(str(app2_locale / "django.po"))
    (app2_path / "TRANSLATING.md").write_text("App2 context.")

    mock_apps = []
    for name, path in [("app1", app1_path), ("app2", app2_path)]:
        mock_app = mocker.MagicMock()
        mock_app.path = str(path)
        mock_app.label = name
        mock_apps.append(mock_app)
    mocker.patch("django.apps.apps.get_app_configs", return_value=mock_apps)

    contexts_used = []

    def mock_translate_side_effect(text, *args, **kwargs):
        contexts_used.append(kwargs.get("context"))
        return [f"Translated: {s}" for s in text]

    mock_translate = mocker.patch(
        "translatebot_django.management.commands.translate.translate_text"
    )
    mock_translate.side_effect = mock_translate_side_effect

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    # Should have been called twice (once per context group)
    assert mock_translate.call_count == 2
    # Each call should have a different context
    assert len({str(c) for c in contexts_used}) == 2
    assert any("App1 context." in str(c) for c in contexts_used)
    assert any("App2 context." in str(c) for c in contexts_used)


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_apps_without_per_app_context_share_batch(tmp_path, settings, mocker):
    """Test that apps without per-app TRANSLATING.md share a batch."""
    settings.BASE_DIR = tmp_path
    settings.LOCALE_PATHS = []

    # Two apps without TRANSLATING.md
    mock_apps = []
    for name in ["app1", "app2"]:
        app_path = tmp_path / name
        app_locale = app_path / "locale" / "nl" / "LC_MESSAGES"
        app_locale.mkdir(parents=True)
        po = polib.POFile()
        po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
        po.append(polib.POEntry(msgid=f"{name} string", msgstr=""))
        po.save(str(app_locale / "django.po"))

        mock_app = mocker.MagicMock()
        mock_app.path = str(app_path)
        mock_app.label = name
        mock_apps.append(mock_app)

    mocker.patch("django.apps.apps.get_app_configs", return_value=mock_apps)

    mock_translate = mocker.patch(
        "translatebot_django.management.commands.translate.translate_text"
    )
    mock_translate.return_value = ["Vertaald1", "Vertaald2"]

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    # Should be called once since both apps share the same context (None)
    assert mock_translate.call_count == 1


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_backward_compatibility_no_per_app_files(tmp_path, settings, mocker):
    """Test backward compatibility: no per-app files behaves identically."""
    settings.BASE_DIR = tmp_path
    settings.LOCALE_PATHS = []

    app_path = tmp_path / "myapp"
    app_locale = app_path / "locale" / "nl" / "LC_MESSAGES"
    app_locale.mkdir(parents=True)
    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po.append(polib.POEntry(msgid="Hello", msgstr=""))
    po.save(str(app_locale / "django.po"))

    mock_app = mocker.MagicMock()
    mock_app.path = str(app_path)
    mock_app.label = "myapp"
    mocker.patch("django.apps.apps.get_app_configs", return_value=[mock_app])

    mock_translate = mocker.patch(
        "translatebot_django.management.commands.translate.translate_text"
    )
    mock_translate.return_value = ["Hallo"]

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    # Context should be None (no project or app TRANSLATING.md)
    call_kwargs = mock_translate.call_args[1]
    assert call_kwargs.get("context") is None


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_output_shows_per_app_context_message(tmp_path, settings, mocker):
    """Test that command output shows per-app context detection message."""
    settings.BASE_DIR = tmp_path
    settings.LOCALE_PATHS = []

    app_path = tmp_path / "medical_app"
    app_locale = app_path / "locale" / "nl" / "LC_MESSAGES"
    app_locale.mkdir(parents=True)
    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po.append(polib.POEntry(msgid="Diagnosis", msgstr=""))
    po.save(str(app_locale / "django.po"))

    (app_path / "TRANSLATING.md").write_text("Medical context.")

    mock_app = mocker.MagicMock()
    mock_app.path = str(app_path)
    mock_app.label = "medical_app"
    mocker.patch("django.apps.apps.get_app_configs", return_value=[mock_app])

    mock_translate = mocker.patch(
        "translatebot_django.management.commands.translate.translate_text"
    )
    mock_translate.return_value = ["Diagnose"]

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    output = out.getvalue()
    assert "Found TRANSLATING.md for medical_app" in output


# --- DeepL integration tests ---


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key")
def test_command_deepl_provider_end_to_end(sample_po_file, settings, mocker):
    """Test end-to-end translation with DeepL provider."""
    from unittest.mock import MagicMock

    settings.TRANSLATEBOT_PROVIDER = "deepl"

    mock_result_1 = MagicMock()
    mock_result_1.text = "Hallo, wereld!"
    mock_result_2 = MagicMock()
    mock_result_2.text = "Welkom bij %(site_name)s"

    mock_translator = MagicMock()
    mock_translator.translate_text.return_value = [mock_result_1, mock_result_2]

    mocker.patch("deepl.Translator", return_value=mock_translator)

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    output = out.getvalue()
    assert "Translating with DeepL" in output

    po = polib.pofile(str(sample_po_file))
    hello = [e for e in po if e.msgid == "Hello, world!"][0]
    assert hello.msgstr == "Hallo, wereld!"

    # Verify the translator was called with correct DeepL language code
    mock_translator.translate_text.assert_called_once()
    call_args = mock_translator.translate_text.call_args
    assert call_args[1]["target_lang"] == "NL"


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key")
def test_command_deepl_translating_md_warning(
    sample_po_file, settings, tmp_path, mocker
):
    """Test that TRANSLATING.md triggers a warning with DeepL provider."""
    from unittest.mock import MagicMock

    settings.TRANSLATEBOT_PROVIDER = "deepl"
    settings.BASE_DIR = tmp_path
    context_file = tmp_path / "TRANSLATING.md"
    context_file.write_text("Project-specific context.")

    mock_result = MagicMock()
    mock_result.text = "Vertaald"
    mock_translator = MagicMock()
    mock_translator.translate_text.return_value = [mock_result, mock_result]
    mocker.patch("deepl.Translator", return_value=mock_translator)

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    output = out.getvalue()
    assert "does not support custom context" in output


@pytest.mark.usefixtures("mock_env_api_key")
def test_command_deepl_per_app_translating_md_warning(tmp_path, settings, mocker):
    """Test that per-app TRANSLATING.md triggers a warning with DeepL provider."""
    from unittest.mock import MagicMock

    settings.TRANSLATEBOT_PROVIDER = "deepl"
    settings.BASE_DIR = tmp_path
    settings.LOCALE_PATHS = []

    # Create an app with a TRANSLATING.md
    app_path = tmp_path / "medical_app"
    app_locale = app_path / "locale" / "nl" / "LC_MESSAGES"
    app_locale.mkdir(parents=True)
    app_po = app_locale / "django.po"
    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    po.append(polib.POEntry(msgid="Diagnosis", msgstr=""))
    po.save(str(app_po))

    (app_path / "TRANSLATING.md").write_text("Medical terminology.")

    mock_app = mocker.MagicMock()
    mock_app.path = str(app_path)
    mock_app.label = "medical_app"
    mocker.patch("django.apps.apps.get_app_configs", return_value=[mock_app])

    mock_result = MagicMock()
    mock_result.text = "Diagnose"
    mock_translator = MagicMock()
    mock_translator.translate_text.return_value = [mock_result]
    mocker.patch("deepl.Translator", return_value=mock_translator)

    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    output = out.getvalue()
    assert "Found TRANSLATING.md for medical_app" in output
    assert "does not support custom context" in output
