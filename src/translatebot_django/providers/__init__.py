from abc import ABC, abstractmethod

from django.core.management.base import CommandError


class TranslationProvider(ABC):
    """Abstract base class for translation providers."""

    @abstractmethod
    def translate(self, texts, target_lang, context=None):
        """Translate a batch of texts to the target language.

        Args:
            texts: List of strings to translate.
            target_lang: Target language code (e.g., 'nl', 'de').
            context: Optional translation context from TRANSLATING.md.

        Returns:
            List of translated strings, same length as texts.
        """

    @abstractmethod
    def batch(self, texts, target_lang):
        """Split texts into batches suitable for this provider.

        Args:
            texts: List of strings to split into batches.
            target_lang: Target language code.

        Returns:
            List of lists of strings.
        """

    @property
    @abstractmethod
    def name(self):
        """Display name for this provider (e.g., 'gpt-4o-mini', 'DeepL')."""

    @property
    @abstractmethod
    def supports_context(self):
        """Whether this provider can use TRANSLATING.md context."""


def get_provider(api_key):
    """Create a translation provider based on Django settings.

    Reads TRANSLATEBOT_PROVIDER from settings. Defaults to 'litellm' if not set.

    Args:
        api_key: API key for the provider.

    Returns:
        A TranslationProvider instance.
    """
    from django.conf import settings

    provider_name = getattr(settings, "TRANSLATEBOT_PROVIDER", "litellm")

    if provider_name == "litellm":
        from translatebot_django.management.commands.translate import (
            _LITELLM_MISSING_MSG,
            _has_litellm,
        )

        if not _has_litellm:
            raise CommandError(_LITELLM_MISSING_MSG)
        from translatebot_django.providers.litellm import LiteLLMProvider
        from translatebot_django.utils import get_model

        model = get_model()
        return LiteLLMProvider(model=model, api_key=api_key)

    if provider_name == "deepl":
        from translatebot_django.providers.deepl import DeepLProvider

        return DeepLProvider(api_key=api_key)

    raise CommandError(
        f"Unknown translation provider: '{provider_name}'. "
        "Supported providers: 'litellm', 'deepl'."
    )
