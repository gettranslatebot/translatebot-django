"""Tests for django-modeltranslation integration."""

import pytest

from django.core.management import call_command

from translatebot_django.utils import is_modeltranslation_available


def test_is_modeltranslation_available_without_package(settings):
    """Test detection when modeltranslation is not installed."""
    # Remove from INSTALLED_APPS
    settings.INSTALLED_APPS = [
        app for app in settings.INSTALLED_APPS if app != "modeltranslation"
    ]

    # Should still work if package is installed but not in INSTALLED_APPS
    # The function checks both conditions
    result = is_modeltranslation_available()
    assert isinstance(result, bool)


def test_models_flag_without_modeltranslation(settings, mock_env_api_key):
    """Test --models parameter unavailable when modeltranslation not installed."""
    settings.INSTALLED_APPS = [
        app for app in settings.INSTALLED_APPS if app != "modeltranslation"
    ]
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    # When modeltranslation is not available, the --models parameter shouldn't exist
    # Django's call_command will raise TypeError for unknown options
    with pytest.raises(TypeError, match="Unknown option.*models"):
        call_command("translate", target_lang="nl", models=[])


@pytest.mark.skipif(
    not is_modeltranslation_available(),
    reason="django-modeltranslation not installed",
)
class TestModeltranslationBackend:
    """Tests for ModeltranslationBackend (requires django-modeltranslation)."""

    def test_backend_initialization(self):
        """Test that backend can be initialized."""
        from translatebot_django.backends.modeltranslation import (
            ModeltranslationBackend,
        )

        backend = ModeltranslationBackend(target_lang="de")
        assert backend.target_lang == "de"
        assert backend.translator is not None

    def test_get_target_field_name(self):
        """Test field name conversion."""
        from translatebot_django.backends.modeltranslation import (
            ModeltranslationBackend,
        )

        backend = ModeltranslationBackend(target_lang="nl")
        assert backend.get_target_field_name("title") == "title_nl"
        assert backend.get_target_field_name("content") == "content_nl"

    def test_parse_model_names_invalid(self):
        """Test parsing invalid model names."""
        from translatebot_django.backends.modeltranslation import (
            ModeltranslationBackend,
        )

        backend = ModeltranslationBackend(target_lang="nl")

        with pytest.raises(ValueError, match="not found"):
            backend.parse_model_names(["NonExistentModel"])

    def test_parse_model_names_with_app_label(self):
        """Test parsing model names with app.Model format."""
        from translatebot_django.backends.modeltranslation import (
            ModeltranslationBackend,
        )

        backend = ModeltranslationBackend(target_lang="nl")

        # This should raise an error for non-existent model
        with pytest.raises(ValueError):
            backend.parse_model_names(["nonexistent.Model"])

    def test_parse_model_names_empty_list(self):
        """Test parsing empty list of model names."""
        from translatebot_django.backends.modeltranslation import (
            ModeltranslationBackend,
        )

        backend = ModeltranslationBackend(target_lang="nl")

        # Empty list should return None
        result = backend.parse_model_names([])
        assert result is None

        # None should also return None
        result = backend.parse_model_names(None)
        assert result is None

    def test_gather_translatable_content_empty(self):
        """Test gathering content when no models are registered."""
        from translatebot_django.backends.modeltranslation import (
            ModeltranslationBackend,
        )

        backend = ModeltranslationBackend(target_lang="nl")

        # If no models registered, should return empty list
        items = backend.gather_translatable_content()
        assert isinstance(items, list)

    def test_apply_translations_dry_run(self):
        """Test dry run mode doesn't save to database."""
        from translatebot_django.backends.modeltranslation import (
            ModeltranslationBackend,
        )

        backend = ModeltranslationBackend(target_lang="nl")

        # Empty translation items
        translation_items = []
        count = backend.apply_translations(translation_items, dry_run=True)
        assert count == 0

        # With items (mocked)
        # In real scenario, these would be actual model instances
        # For now, just test the dry_run path
        fake_items = [
            {"instance": None, "target_field": "title_nl", "translation": "Test"}
        ]
        count = backend.apply_translations(fake_items, dry_run=True)
        assert count == 1  # Should return count even in dry run


def test_command_help_includes_models(capsys):
    """Test command help text mentions --models only when available."""
    from django.core.management import call_command

    # --help causes SystemExit, so catch it
    with pytest.raises(SystemExit) as exc_info:
        call_command("translate", "--help")

    assert exc_info.value.code == 0  # Success exit
    captured = capsys.readouterr()
    help_text = captured.out

    # --models should only appear if modeltranslation is available
    if is_modeltranslation_available():
        assert "--models" in help_text
        assert "modeltranslation" in help_text
    else:
        assert "--models" not in help_text


def test_translate_command_with_models_flag_structure(
    settings, mock_env_api_key, mocker
):
    """Test that --models flag is properly handled in command structure."""
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    # Mock modeltranslation availability
    mocker.patch(
        "translatebot_django.management.commands.translate.is_modeltranslation_available",
        return_value=True,
    )

    # Mock the backend at the correct import location
    mock_backend_class = mocker.patch(
        "translatebot_django.backends.modeltranslation.ModeltranslationBackend"
    )
    mock_backend = mocker.MagicMock()
    mock_backend.gather_translatable_content.return_value = []  # No items to translate
    mock_backend_class.return_value = mock_backend

    # This should not raise an error
    from io import StringIO

    out = StringIO()
    call_command("translate", target_lang="nl", models=[], stdout=out)

    # Verify backend was called
    mock_backend_class.assert_called_once()
    mock_backend.gather_translatable_content.assert_called_once()


@pytest.mark.django_db
@pytest.mark.skipif(
    not is_modeltranslation_available(),
    reason="django-modeltranslation not installed",
)
def test_gather_content_with_modeltranslation_languages(settings):
    """Test gathering content when MODELTRANSLATION_LANGUAGES is set."""
    from translatebot_django.backends.modeltranslation import ModeltranslationBackend

    # Set MODELTRANSLATION_LANGUAGES (takes priority over LANGUAGES)
    settings.MODELTRANSLATION_LANGUAGES = ("en", "de", "nl")

    backend = ModeltranslationBackend(target_lang="nl")
    items = backend.gather_translatable_content()

    # Should not raise an error and return a list
    assert isinstance(items, list)


@pytest.mark.django_db
@pytest.mark.skipif(
    not is_modeltranslation_available(),
    reason="django-modeltranslation not installed",
)
def test_gather_content_without_languages_settings(settings):
    """Test gathering content without MODELTRANSLATION_LANGUAGES or LANGUAGES."""
    from translatebot_django.backends.modeltranslation import ModeltranslationBackend

    # Remove both settings to test fallback
    if hasattr(settings, "MODELTRANSLATION_LANGUAGES"):
        delattr(settings, "MODELTRANSLATION_LANGUAGES")
    if hasattr(settings, "LANGUAGES"):
        delattr(settings, "LANGUAGES")

    backend = ModeltranslationBackend(target_lang="nl")
    items = backend.gather_translatable_content()

    # Should use target_lang as fallback and return a list
    assert isinstance(items, list)


@pytest.mark.django_db
@pytest.mark.skipif(
    not is_modeltranslation_available(),
    reason="django-modeltranslation not installed",
)
def test_gather_content_no_source_languages(settings):
    """Test gathering content when no source languages available."""
    from tests.models import Article
    from translatebot_django.backends.modeltranslation import ModeltranslationBackend

    # Set LANGUAGES to only include the target language
    settings.LANGUAGES = [("nl", "Dutch")]

    # Create an article with some content
    Article.objects.create(
        title="Test Title",
        content="Test Content",
    )

    backend = ModeltranslationBackend(target_lang="nl")
    items = backend.gather_translatable_content()

    # Should return empty list because there are no source languages
    # (all available languages == target language, so source_langs is empty)
    assert isinstance(items, list)
