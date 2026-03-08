import html
import logging
import re

from django.core.management.base import CommandError

from translatebot_django.providers import TranslationProvider

logger = logging.getLogger(__name__)

# DeepL API limits
MAX_TEXTS_PER_REQUEST = 50
MAX_REQUEST_SIZE_BYTES = 128 * 1024  # 128 KB

# Language codes that need region suffixes for DeepL
# DeepL requires specific regional variants for these target languages
_DEEPL_REGIONAL_TARGETS = {
    "en": "EN-US",
    "pt": "PT-BR",
}

# Matches Python/Django format placeholders:
#   %(name)s  %(count)d  %s  %d  %i  %f  %%
#   {name}  {0}  {name!r}  {0:.2f}  {}
_PLACEHOLDER_RE = re.compile(
    r"""
    %\( [^)]+? \) [diouxXeEfFgGcrsab%]       # %(name)s style
    | %[%diouxXeEfFgGcrsab]                   # %s / %d / %% style
    | (?<!\{) \{ \w* (?:[!:][^}]*)? \} (?!\}) # {name} / {0} / {} style
    """,
    re.VERBOSE,
)


# Two placeholder-protection strategies depending on the DeepL language model:
#
# 1. Standard (<x> tags): Placeholders are wrapped in <x>…</x> tags with no
#    tag_handling set.  DeepL treats these as opaque text.
#
# 2. Newer models (email tokens + tag_handling="html"): Languages whose models
#    don't reliably preserve <x> tags use email-shaped tokens (ph0@tb.x, …)
#    combined with tag_handling="html" to preserve both placeholders and real
#    HTML.  Output is post-processed with html.unescape() to undo any entity
#    encoding of special characters.  These languages are detected dynamically
#    via the DeepL API: they are the ones that lack formality support.
#
# See: https://github.com/gettranslatebot/translatebot-django/issues/95

_EMAIL_PH_RE = re.compile(r"ph(\d+)@tb\.x")


def _wrap_placeholders(text):
    """Wrap format placeholders in <x> tags so DeepL leaves them alone."""
    return _PLACEHOLDER_RE.sub(lambda m: f"<x>{m.group()}</x>", text)


def _unwrap_placeholders(text):
    """Remove <x> tags that were wrapped around placeholders."""
    return text.replace("<x>", "").replace("</x>", "")


def _replace_placeholders_with_emails(text):
    """Replace format placeholders with email-shaped tokens.

    Returns (replaced_text, list_of_originals) so they can be restored later.
    """
    originals = []

    def _sub(m):
        idx = len(originals)
        originals.append(m.group())
        return f"ph{idx}@tb.x"

    replaced = _PLACEHOLDER_RE.sub(_sub, text)
    return replaced, originals


def _restore_email_placeholders(text, originals):
    """Swap email-shaped tokens back to the original placeholders."""

    def _sub(m):
        idx = int(m.group(1))
        if idx < len(originals):
            return originals[idx]
        return m.group()  # defensive: leave unknown tokens as-is

    return _EMAIL_PH_RE.sub(_sub, text)


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
        self._no_formality_langs = None  # lazy-loaded via _lacks_formality_support

    def _lacks_formality_support(self, deepl_lang):
        """Check if a language lacks formality support (newer DeepL model).

        Newer models mangle <x> tags in plain-text mode, so these languages
        need email-shaped placeholder tokens and tag_handling="html".
        Results are cached after the first API call.

        NOTE: This uses supports_formality as a heuristic for model version.
        If DeepL adds formality support to newer models (or adds languages on
        older models without formality), this correlation may break.
        """
        if self._no_formality_langs is None:
            try:
                langs = self._translator.get_target_languages()
                self._no_formality_langs = frozenset(
                    lang.code.split("-")[0]
                    for lang in langs
                    if not lang.supports_formality
                )
            except self._deepl.DeepLException:
                logger.warning(
                    "Failed to fetch DeepL target languages; "
                    "falling back to <x> tag placeholders for all languages."
                )
                self._no_formality_langs = frozenset()
        return deepl_lang.split("-")[0] in self._no_formality_langs

    def translate(self, texts, target_lang, context=None, comments=None):
        deepl_lang = django_to_deepl_target(target_lang)
        use_email_ph = self._lacks_formality_support(deepl_lang)

        if use_email_ph:
            prepared = []
            originals_per_text = []
            for t in texts:
                replaced, originals = _replace_placeholders_with_emails(t)
                prepared.append(replaced)
                originals_per_text.append(originals)
        else:
            prepared = [_wrap_placeholders(t) for t in texts]

        translate_kwargs = {
            "target_lang": deepl_lang,
            "preserve_formatting": True,
        }
        if use_email_ph:
            translate_kwargs["tag_handling"] = "html"

        try:
            results = self._translator.translate_text(prepared, **translate_kwargs)
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

        if use_email_ph:
            translations = [
                html.unescape(_restore_email_placeholders(r.text, orig))
                for r, orig in zip(results, originals_per_text, strict=True)
            ]
        else:
            translations = [_unwrap_placeholders(r.text) for r in results]

        # DeepL sometimes adds a trailing dot to translations even when the
        # source string does not end with one.  Strip it in that case.
        for i, (src, trl) in enumerate(zip(texts, translations, strict=True)):
            if not src.endswith(".") and trl.endswith("."):
                translations[i] = trl[:-1]

        return translations

    def batch(self, texts, target_lang, comments=None):
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
