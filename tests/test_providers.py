"""Tests for translation providers."""

import sys
from unittest.mock import MagicMock

import pytest

from django.core.management.base import CommandError

# --- Factory tests ---


def test_get_provider_default_is_litellm(settings, monkeypatch):
    """Default provider (no setting) returns LiteLLMProvider."""
    monkeypatch.setenv("TRANSLATEBOT_API_KEY", "test-key")
    if hasattr(settings, "TRANSLATEBOT_PROVIDER"):
        delattr(settings, "TRANSLATEBOT_PROVIDER")
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    from translatebot_django.providers import get_provider
    from translatebot_django.providers.litellm import LiteLLMProvider

    provider = get_provider("test-key")
    assert isinstance(provider, LiteLLMProvider)


def test_get_provider_explicit_litellm(settings):
    """Explicit litellm setting returns LiteLLMProvider."""
    settings.TRANSLATEBOT_PROVIDER = "litellm"
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    from translatebot_django.providers import get_provider
    from translatebot_django.providers.litellm import LiteLLMProvider

    provider = get_provider("test-key")
    assert isinstance(provider, LiteLLMProvider)


def test_get_provider_deepl(settings):
    """DeepL setting returns DeepLProvider."""
    settings.TRANSLATEBOT_PROVIDER = "deepl"

    from translatebot_django.providers import get_provider
    from translatebot_django.providers.deepl import DeepLProvider

    provider = get_provider("test-key")
    assert isinstance(provider, DeepLProvider)


def test_get_provider_unknown_raises_error(settings):
    """Unknown provider name raises CommandError."""
    settings.TRANSLATEBOT_PROVIDER = "unknown_provider"

    from translatebot_django.providers import get_provider

    with pytest.raises(CommandError, match="Unknown translation provider"):
        get_provider("test-key")


# --- LiteLLM provider property tests ---


def test_litellm_provider_name(settings):
    """LiteLLMProvider.name returns the model name."""
    settings.TRANSLATEBOT_MODEL = "claude-3-sonnet"

    from translatebot_django.providers.litellm import LiteLLMProvider

    provider = LiteLLMProvider(model="claude-3-sonnet", api_key="test-key")
    assert provider.name == "claude-3-sonnet"


def test_litellm_provider_supports_context():
    """LiteLLMProvider supports TRANSLATING.md context."""
    from translatebot_django.providers.litellm import LiteLLMProvider

    provider = LiteLLMProvider(model="gpt-4o-mini", api_key="test-key")
    assert provider.supports_context is True


# --- DeepL provider property tests ---


def test_deepl_provider_name():
    """DeepLProvider.name returns 'DeepL'."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    assert provider.name == "DeepL"


def test_deepl_provider_supports_context():
    """DeepLProvider does not support TRANSLATING.md context."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    assert provider.supports_context is False


# --- DeepL language code mapping tests ---


def test_deepl_lang_mapping_simple():
    """Simple language codes are uppercased."""
    from translatebot_django.providers.deepl import django_to_deepl_target

    assert django_to_deepl_target("de") == "DE"
    assert django_to_deepl_target("fr") == "FR"
    assert django_to_deepl_target("nl") == "NL"
    assert django_to_deepl_target("ja") == "JA"


def test_deepl_lang_mapping_english_default():
    """'en' maps to 'EN-US' (DeepL requires a regional variant)."""
    from translatebot_django.providers.deepl import django_to_deepl_target

    assert django_to_deepl_target("en") == "EN-US"


def test_deepl_lang_mapping_portuguese_default():
    """'pt' maps to 'PT-BR' (DeepL requires a regional variant)."""
    from translatebot_django.providers.deepl import django_to_deepl_target

    assert django_to_deepl_target("pt") == "PT-BR"


def test_deepl_lang_mapping_regional_variant():
    """Regional variants like 'pt-br' are uppercased as-is."""
    from translatebot_django.providers.deepl import django_to_deepl_target

    assert django_to_deepl_target("pt-br") == "PT-BR"
    assert django_to_deepl_target("zh-hans") == "ZH-HANS"
    assert django_to_deepl_target("en-gb") == "EN-GB"


# --- DeepL batching tests ---


def test_deepl_batch_under_limit():
    """Texts under both limits stay in a single batch."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    texts = ["Hello", "World", "Test"]
    batches = provider.batch(texts, "de")
    assert batches == [["Hello", "World", "Test"]]


def test_deepl_batch_over_50_texts():
    """More than 50 texts are split into multiple batches."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    texts = [f"String {i}" for i in range(120)]
    batches = provider.batch(texts, "de")

    assert len(batches) == 3  # 50 + 50 + 20
    assert len(batches[0]) == 50
    assert len(batches[1]) == 50
    assert len(batches[2]) == 20

    # All texts accounted for
    flat = [t for batch in batches for t in batch]
    assert flat == texts


def test_deepl_batch_large_texts():
    """Texts exceeding 128KB are split by size."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    # Each text is ~10KB, so 128KB limit fits ~12 texts
    large_text = "A" * 10_000
    texts = [large_text] * 20
    batches = provider.batch(texts, "de")

    assert len(batches) > 1
    # Each batch should be at most 128KB
    for batch in batches:
        total_size = sum(len(t.encode("utf-8")) for t in batch)
        assert total_size <= 128 * 1024


def test_deepl_batch_empty():
    """Empty input returns empty list."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    batches = provider.batch([], "de")
    assert batches == []


def test_deepl_batch_single_oversized_text():
    """A single text larger than 128KB goes into its own batch."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    huge_text = "B" * (200 * 1024)  # 200KB
    texts = ["small", huge_text, "also small"]
    batches = provider.batch(texts, "de")

    # "small" in first batch, huge_text overflows to second, "also small" in third
    assert len(batches) == 3
    assert batches[0] == ["small"]
    assert batches[1] == [huge_text]
    assert batches[2] == ["also small"]


# --- DeepL translate tests ---


def test_deepl_translate_basic(mocker):
    """DeepLProvider.translate returns translated texts."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")

    mock_result_1 = MagicMock()
    mock_result_1.text = "Hallo"
    mock_result_2 = MagicMock()
    mock_result_2.text = "Welt"

    provider._translator.translate_text = MagicMock(
        return_value=[mock_result_1, mock_result_2]
    )

    result = provider.translate(["Hello", "World"], "de")
    assert result == ["Hallo", "Welt"]

    provider._translator.translate_text.assert_called_once_with(
        ["Hello", "World"], target_lang="DE"
    )


def test_deepl_translate_uses_mapped_lang(mocker):
    """DeepLProvider.translate converts language codes for the API."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")

    mock_result = MagicMock()
    mock_result.text = "Hello"

    provider._translator.translate_text = MagicMock(return_value=[mock_result])

    provider.translate(["Hi"], "en")

    provider._translator.translate_text.assert_called_once_with(
        ["Hi"], target_lang="EN-US"
    )


# --- DeepL error handling tests ---


def test_deepl_translate_auth_error():
    """DeepL auth error raises CommandError."""
    import deepl

    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="bad-key")
    provider._translator.translate_text = MagicMock(
        side_effect=deepl.AuthorizationException("Invalid auth key")
    )

    with pytest.raises(CommandError, match="DeepL authentication failed"):
        provider.translate(["Hello"], "de")


def test_deepl_translate_quota_error():
    """DeepL quota error raises CommandError."""
    import deepl

    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._translator.translate_text = MagicMock(
        side_effect=deepl.QuotaExceededException("Quota exceeded")
    )

    with pytest.raises(CommandError, match="DeepL character quota exceeded"):
        provider.translate(["Hello"], "de")


def test_deepl_translate_rate_limit_error():
    """DeepL rate limit error raises CommandError."""
    import deepl

    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._translator.translate_text = MagicMock(
        side_effect=deepl.TooManyRequestsException("Too many requests")
    )

    with pytest.raises(CommandError, match="DeepL rate limit exceeded"):
        provider.translate(["Hello"], "de")


def test_deepl_translate_generic_error():
    """Generic DeepL API error raises CommandError."""
    import deepl

    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._translator.translate_text = MagicMock(
        side_effect=deepl.DeepLException("Something went wrong")
    )

    with pytest.raises(CommandError, match="DeepL API error"):
        provider.translate(["Hello"], "de")


# --- DeepL import guard test ---


def test_deepl_import_guard_raises_error(monkeypatch):
    """Missing deepl package raises CommandError with install instructions."""
    # Temporarily remove deepl from sys.modules
    original = sys.modules.pop("deepl", None)
    # Also remove our provider module so it re-imports
    sys.modules.pop("translatebot_django.providers.deepl", None)

    import builtins

    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "deepl":
            raise ImportError("No module named 'deepl'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    try:
        from translatebot_django.providers.deepl import _get_deepl_module

        with pytest.raises(CommandError, match="pip install translatebot-django"):
            _get_deepl_module()
    finally:
        # Restore
        if original:
            sys.modules["deepl"] = original
        sys.modules.pop("translatebot_django.providers.deepl", None)
