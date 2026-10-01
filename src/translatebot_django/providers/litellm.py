from translatebot_django.providers import TranslationProvider


class LiteLLMProvider(TranslationProvider):
    """Translation provider using LiteLLM (OpenAI, Anthropic, etc.)."""

    def __init__(self, model, api_key, timeout=None):
        from translatebot_django.utils import DEFAULT_TIMEOUT_SECONDS

        self._model = model
        self._api_key = api_key
        self._timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS

    def translate(
        self, texts, target_lang, context=None, comments=None, source_lang=None
    ):
        from translatebot_django.management.commands.translate import translate_text

        return translate_text(
            text=texts,
            target_lang=target_lang,
            model=self._model,
            api_key=self._api_key,
            context=context,
            comments=comments,
            timeout=self._timeout,
        )

    def batch(self, texts, target_lang, comments=None):
        from translatebot_django.management.commands.translate import batch_by_tokens

        return batch_by_tokens(texts, target_lang, self._model, comments=comments)

    @property
    def name(self):
        return self._model

    @property
    def supports_context(self):
        return True

    @property
    def supports_plural_forms(self):
        return True
