"""Integration tests for modeltranslation backend with actual models."""

from io import StringIO

import pytest

from django.core.management import call_command
from django.core.management.base import CommandError

from tests.models import Article
from translatebot_django.backends.modeltranslation import ModeltranslationBackend


@pytest.fixture
def article_with_translation(db):
    """Create an article instance for testing."""
    article = Article.objects.create(
        title="Hello World", content="This is test content"
    )
    return article


def test_modeltranslation_backend_parse_model_names_error():
    """Test backend parse_model_names with invalid model."""
    backend = ModeltranslationBackend(target_lang="nl")

    with pytest.raises(ValueError, match="not found"):
        backend.parse_model_names(["InvalidModel"])


def test_modeltranslation_backend_gather_translatable_content_empty():
    """Test gather_translatable_content with no registered models."""
    backend = ModeltranslationBackend(target_lang="nl")

    # With no models registered, should return empty list
    items = backend.gather_translatable_content()
    assert items == []


def test_translate_command_with_models_no_content(settings, mock_env_api_key, mocker):
    """Test --models flag when no translatable content found."""
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    # Mock the backend
    mock_backend_class = mocker.patch(
        "translatebot_django.backends.modeltranslation.ModeltranslationBackend"
    )
    mock_backend = mocker.MagicMock()
    mock_backend.gather_translatable_content.return_value = []  # No items
    mock_backend_class.return_value = mock_backend

    out = StringIO()
    call_command("translate", target_lang="nl", models=[], stdout=out)

    output = out.getvalue()
    assert "No untranslated model fields found" in output


def test_translate_command_with_specific_models(settings, mock_env_api_key, mocker):
    """Test --models flag with specific model names."""
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    # Mock the backend
    mock_backend_class = mocker.patch(
        "translatebot_django.backends.modeltranslation.ModeltranslationBackend"
    )
    mock_backend = mocker.MagicMock()

    # Simulate parse_model_names being called
    mock_backend.parse_model_names.return_value = [Article]
    mock_backend.gather_translatable_content.return_value = []
    mock_backend_class.return_value = mock_backend

    out = StringIO()
    call_command("translate", target_lang="nl", models=["Article"], stdout=out)

    # Verify parse_model_names was called with the model names
    mock_backend.parse_model_names.assert_called_once_with(["Article"])


def test_translate_command_models_with_items(settings, mock_env_api_key, mocker):
    """Test --models flag with items to translate."""
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    # Mock the backend
    mock_backend_class = mocker.patch(
        "translatebot_django.backends.modeltranslation.ModeltranslationBackend"
    )
    mock_backend = mocker.MagicMock()

    # Create fake translatable items
    fake_item = {
        "model": Article,
        "instance": mocker.MagicMock(),
        "field": "title",
        "target_field": "title_nl",
        "source_text": "Hello World",
    }
    mock_backend.gather_translatable_content.return_value = [fake_item]
    mock_backend.apply_translations.return_value = 1
    mock_backend_class.return_value = mock_backend

    # Mock translate_text
    mocker.patch(
        "translatebot_django.management.commands.translate.translate_text",
        return_value=["Hallo Wereld"],
    )

    out = StringIO()
    call_command("translate", target_lang="nl", models=[], stdout=out)

    output = out.getvalue()
    assert "Found 1 model fields to translate" in output
    assert "Article" in output
    assert "Successfully translated 1 model field(s)" in output


def test_translate_command_models_dry_run(settings, mock_env_api_key, mocker):
    """Test --models flag with dry-run."""
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    # Mock the backend
    mock_backend_class = mocker.patch(
        "translatebot_django.backends.modeltranslation.ModeltranslationBackend"
    )
    mock_backend = mocker.MagicMock()

    # Create fake translatable items
    fake_item = {
        "model": Article,
        "instance": mocker.MagicMock(),
        "field": "title",
        "target_field": "title_nl",
        "source_text": "Hello World",
    }
    mock_backend.gather_translatable_content.return_value = [fake_item]
    mock_backend.apply_translations.return_value = 1
    mock_backend_class.return_value = mock_backend

    # Mock translate_text
    mocker.patch(
        "translatebot_django.management.commands.translate.translate_text",
        return_value=["Hallo Wereld"],
    )

    out = StringIO()
    call_command("translate", target_lang="nl", models=[], dry_run=True, stdout=out)

    output = out.getvalue()
    assert "Dry run" in output

    # Verify dry_run=True was passed to apply_translations
    mock_backend.apply_translations.assert_called_once()
    call_kwargs = mock_backend.apply_translations.call_args[1]
    assert call_kwargs["dry_run"] is True


def test_translate_command_models_parse_error(settings, mock_env_api_key, mocker):
    """Test --models flag with invalid model name."""
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    # Mock the backend to raise ValueError
    mock_backend_class = mocker.patch(
        "translatebot_django.backends.modeltranslation.ModeltranslationBackend"
    )
    mock_backend = mocker.MagicMock()
    mock_backend.parse_model_names.side_effect = ValueError("Model not found")
    mock_backend_class.return_value = mock_backend

    with pytest.raises(CommandError, match="Model not found"):
        call_command("translate", target_lang="nl", models=["InvalidModel"])


def test_translate_command_models_batching(settings, mock_env_api_key, mocker):
    """Test --models flag with batching when content exceeds token limits."""
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    # Mock the backend
    mock_backend_class = mocker.patch(
        "translatebot_django.backends.modeltranslation.ModeltranslationBackend"
    )
    mock_backend = mocker.MagicMock()

    # Create many fake translatable items that would exceed token limits
    fake_items = []
    for i in range(100):
        # Create very long content to trigger batching
        fake_item = {
            "model": Article,
            "instance": mocker.MagicMock(),
            "field": "content",
            "target_field": "content_nl",
            "source_text": f"Very long content string {i} " * 100,  # Make it long
        }
        fake_items.append(fake_item)

    mock_backend.gather_translatable_content.return_value = fake_items
    mock_backend.apply_translations.return_value = len(fake_items)
    mock_backend_class.return_value = mock_backend

    # Mock translate_text to return translations for each batch
    translate_mock = mocker.patch(
        "translatebot_django.management.commands.translate.translate_text",
        side_effect=lambda text, _target_lang, _model, _api_key: [
            f"Translated {i}" for i in range(len(text))
        ],
    )

    # Mock get_max_tokens to force batching with a low limit
    mocker.patch(
        "translatebot_django.management.commands.translate.get_max_tokens",
        return_value=1000,  # Low limit to force batching
    )

    out = StringIO()
    call_command("translate", target_lang="nl", models=[], stdout=out)

    output = out.getvalue()
    assert f"Found {len(fake_items)} model fields to translate" in output
    assert "batches" in output  # Should mention batches

    # Verify translate_text was called multiple times (batching occurred)
    assert translate_mock.call_count > 1


def test_translate_command_models_authentication_error(
    settings, mock_env_api_key, mocker
):
    """Test --models flag with authentication error."""
    from litellm.exceptions import AuthenticationError

    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    # Mock the backend
    mock_backend_class = mocker.patch(
        "translatebot_django.backends.modeltranslation.ModeltranslationBackend"
    )
    mock_backend = mocker.MagicMock()

    # Create fake translatable items
    fake_item = {
        "model": Article,
        "instance": mocker.MagicMock(),
        "field": "title",
        "target_field": "title_nl",
        "source_text": "Hello World",
    }
    mock_backend.gather_translatable_content.return_value = [fake_item]
    mock_backend_class.return_value = mock_backend

    # Mock translate_text to raise AuthenticationError
    mocker.patch(
        "translatebot_django.management.commands.translate.translate_text",
        side_effect=AuthenticationError(
            message="Invalid API key",
            llm_provider="openai",
            model="gpt-4o-mini",
        ),
    )

    with pytest.raises(CommandError, match="Authentication failed"):
        call_command("translate", target_lang="nl", models=[])
