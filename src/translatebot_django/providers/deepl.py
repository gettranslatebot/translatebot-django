import html
import re

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


# Placeholder protection uses email-shaped tokens (ph0@tb.x, …) combined with
# tag_handling="html" to preserve both placeholders and real HTML.  Output is
# post-processed with html.unescape() to undo entity encoding — but only when
# the source text doesn't already contain HTML entities, since those are
# intentional and must be preserved.
#
# See: https://github.com/gettranslatebot/translatebot-django/issues/95
#      https://github.com/gettranslatebot/translatebot-django/issues/107

_HTML_ENTITY_RE = re.compile(r"&(?:#\d+|#x[\da-fA-F]+|[a-zA-Z]+);")

_EMAIL_PH_RE = re.compile(r"ph(\d+)@tb\.x")


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

    def translate(self, texts, target_lang, context=None, comments=None):
        deepl_lang = django_to_deepl_target(target_lang)

        prepared = []
        originals_per_text = []
        for t in texts:
            replaced, originals = _replace_placeholders_with_emails(t)
            prepared.append(replaced)
            originals_per_text.append(originals)

        try:
            results = self._translator.translate_text(
                prepared,
                target_lang=deepl_lang,
                preserve_formatting=True,
                tag_handling="html",
            )
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

        translations = []
        for r, orig, src in zip(results, originals_per_text, texts, strict=True):
            translated = _restore_email_placeholders(r.text, orig)
            # Only unescape entities that DeepL added (tag_handling="html"
            # encodes plain < > & as entities).  If the source already
            # contains HTML entities they are intentional and must stay.
            if not _HTML_ENTITY_RE.search(src):
                translated = html.unescape(translated)
            translations.append(translated)

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
