from django.core.management.base import CommandError

from translatebot_django.providers import TranslationProvider

# DeepL API limits
MAX_TEXTS_PER_REQUEST = 50
MAX_REQUEST_SIZE_BYTES = 128 * 1024  # 128 KB

# Language codes that need region suffixes for DeepL
# DeepL requires specific regional variants for these target languages
_DEEPL_REGIONAL_TARGETS = {
    "en": "EN-US",
    "pt": "PT-BR",
}


def django_to_deepl_target(lang_code):
    """Convert a Django language code to a DeepL target language code.

    Examples:
        'en' -> 'EN-US'
        'de' -> 'DE'
        'pt-br' -> 'PT-BR'
        'zh-hans' -> 'ZH-HANS'
    """
    # Check if this is a base language that needs a regional default
    lower = lang_code.lower()
    if lower in _DEEPL_REGIONAL_TARGETS:
        return _DEEPL_REGIONAL_TARGETS[lower]

    # Django uses lowercase with hyphens (e.g. 'pt-br', 'zh-hans')
    # DeepL uses uppercase with hyphens (e.g. 'PT-BR', 'ZH-HANS')
    return lang_code.upper()


def _get_deepl_module():
    """Import and return the deepl module, raising CommandError if not installed."""
    try:
        import deepl

        return deepl
    except ImportError as e:
        raise CommandError(
            "The 'deepl' package is required to use the DeepL provider.\n"
            "Install it with: pip install translatebot-django[deepl]"
        ) from e


class DeepLProvider(TranslationProvider):
    """Translation provider using the DeepL API."""

    def __init__(self, api_key):
        self._deepl = _get_deepl_module()
        self._translator = self._deepl.Translator(api_key)

    def translate(self, texts, target_lang, context=None):
        deepl_lang = django_to_deepl_target(target_lang)

        try:
            results = self._translator.translate_text(texts, target_lang=deepl_lang)
        except self._deepl.AuthorizationException as e:
            raise CommandError(
                f"DeepL authentication failed: {e}\n"
                "Please check your API key configuration.\n"
                "Set TRANSLATEBOT_API_KEY in settings or "
                "TRANSLATEBOT_API_KEY environment variable."
            ) from e
        except self._deepl.QuotaExceededException as e:
            raise CommandError(
                "DeepL character quota exceeded. "
                "Please check your DeepL plan usage at "
                "https://www.deepl.com/account/usage"
            ) from e
        except self._deepl.TooManyRequestsException as e:
            raise CommandError(
                "DeepL rate limit exceeded. Please wait and try again."
            ) from e
        except self._deepl.DeepLException as e:
            raise CommandError(f"DeepL API error: {e}") from e

        translations = [r.text for r in results]

        # DeepL sometimes adds a trailing dot to translations even when the
        # source string does not end with one.  Strip it in that case.
        for i, (src, trl) in enumerate(zip(texts, translations, strict=True)):
            if not src.endswith(".") and trl.endswith("."):
                translations[i] = trl[:-1]

        return translations

    def batch(self, texts, target_lang):
        """Split texts into batches respecting DeepL API limits.

        DeepL limits: max 50 texts per request, max 128KB total request size.
        """
        groups = []
        current_group = []
        current_size = 0

        for text in texts:
            text_size = len(text.encode("utf-8"))

            would_exceed_count = len(current_group) >= MAX_TEXTS_PER_REQUEST
            would_exceed_size = (
                current_group and current_size + text_size > MAX_REQUEST_SIZE_BYTES
            )

            if would_exceed_count or would_exceed_size:
                groups.append(current_group)
                current_group = []
                current_size = 0

            current_group.append(text)
            current_size += text_size

        if current_group:
            groups.append(current_group)

        return groups

    @property
    def name(self):
        return "DeepL"

    @property
    def supports_context(self):
        return False
