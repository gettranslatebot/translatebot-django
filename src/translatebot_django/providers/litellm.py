from translatebot_django.providers import TranslationProvider


class LiteLLMProvider(TranslationProvider):
    """Translation provider using LiteLLM (OpenAI, Anthropic, etc.)."""

    def __init__(self, model, api_key):
        self._model = model
        self._api_key = api_key

    def translate(self, texts, target_lang, context=None):
        from translatebot_django.management.commands.translate import translate_text

        return translate_text(
            text=texts,
            target_lang=target_lang,
            model=self._model,
            api_key=self._api_key,
            context=context,
        )

    def batch(self, texts, target_lang):
        from translatebot_django.management.commands.translate import batch_by_tokens

        return batch_by_tokens(texts, target_lang, self._model)

    @property
    def name(self):
        return self._model

    @property
    def supports_context(self):
        return True
