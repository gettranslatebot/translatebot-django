from abc import ABC, abstractmethod

from django.core.management.base import CommandError


class TranslationProvider(ABC):
    """Abstract base class for translation providers."""

    @abstractmethod
    def translate(
        self, texts, target_lang, context=None, comments=None, source_lang=None
    ):
        """Translate a batch of texts to the target language.

        Args:
            texts: List of strings to translate.
            target_lang: Target language code (e.g., 'nl', 'de').
            context: Optional translation context from TRANSLATING.md.
            comments: Optional developer comments extracted from PO files
                      (#. lines, plus the msgctxt): a list aligned with
                      *texts* (None where a text has none), or a dict
                      mapping source strings to comments.
            source_lang: Optional language code the texts are written in,
                         when known for certain (e.g. a modeltranslation
                         language column). Providers may ignore it.

        Returns:
            List of translations, same length as texts: a string per plain
            text, and a list with one string per plural form for each
            :class:`~translatebot_django.management.commands.translate.PluralText`
            (only sent when :attr:`supports_plural_forms` is true).
        """

    @abstractmethod
    def batch(self, texts, target_lang, comments=None):
        """Split texts into batches suitable for this provider.

        Args:
            texts: List of strings to split into batches.
            target_lang: Target language code.
            comments: Optional developer comments, aligned with *texts* or
                      keyed by source string.

        Returns:
            List of lists of texts: contiguous, order-preserving slices of
            *texts*.
        """

    @property
    @abstractmethod
    def name(self):
        """Display name for this provider (e.g., 'gpt-4o-mini', 'DeepL')."""

    @property
    @abstractmethod
    def supports_context(self):
        """Whether this provider can use TRANSLATING.md context."""

    @property
    def supports_plural_forms(self):
        """Whether this provider can translate every plural form of a message.

        When true, pluralized PO messages are sent as ``PluralText`` objects
        describing the target language's plural forms. Otherwise their
        singular and plural are sent as two strings, and the plural
        translation is reused for every form past the first.
        """
        return False


def get_provider(api_key, model=None):
    """Create a translation provider based on Django settings.

    Reads TRANSLATEBOT_PROVIDER from settings. Defaults to 'litellm' if not set.

    Args:
        api_key: API key for the provider.
        model: Optional LLM model name to use instead of the value from
            ``settings.TRANSLATEBOT_MODEL``. Ignored for providers that do not
            use a model (e.g. DeepL — passing a model for DeepL raises an
            error).

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
        from translatebot_django.utils import get_model, get_timeout

        effective_model = model if model is not None else get_model()
        return LiteLLMProvider(
            model=effective_model, api_key=api_key, timeout=get_timeout()
        )

    if provider_name == "deepl":
        if model is not None:
            raise CommandError(
                "The 'model' parameter is not supported for the DeepL provider. "
                "DeepL does not use a language model."
            )
        from translatebot_django.providers.deepl import DeepLProvider

        return DeepLProvider(api_key=api_key)

    raise CommandError(
        f"Unknown translation provider: '{provider_name}'. "
        "Supported providers: 'litellm', 'deepl'."
    )
