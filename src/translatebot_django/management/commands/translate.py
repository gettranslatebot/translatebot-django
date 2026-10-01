import dataclasses
import gettext
import json
import logging
import math
import re
import time
import warnings
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import polib

from django.core.management.base import BaseCommand, CommandError

try:
    import tiktoken
    from litellm import LITELLM_EXCEPTION_TYPES, completion, get_model_info
    from litellm.exceptions import (
        APIConnectionError,
        APIError,
        AuthenticationError,
        BadGatewayError,
        BadRequestError,
        InternalServerError,
        RateLimitError,
        ServiceUnavailableError,
        Timeout,
    )

    # Every error litellm raises; their only common base is openai's
    # APIError, which litellm doesn't re-export.
    _LITELLM_ERRORS = tuple(LITELLM_EXCEPTION_TYPES)
    # Errors worth retrying after a short wait: the request may succeed
    # once the network or the provider recovers. Timeout must be listed:
    # litellm's Timeout and APIConnectionError don't inherit from each other.
    # Generic APIErrors with a 5xx status are retried too (_is_transient).
    _TRANSIENT_ERRORS = (
        Timeout,
        APIConnectionError,
        InternalServerError,
        ServiceUnavailableError,
        BadGatewayError,
    )
    _has_litellm = True
except ImportError:
    _has_litellm = False
    completion = None  # type: ignore[assignment]
    get_model_info = None  # type: ignore[assignment]

    # Sentinel classes so except clauses are syntactically valid.
    # They can never be raised when litellm is absent.
    class AuthenticationError(Exception):  # type: ignore[no-redef]
        pass

    class BadRequestError(Exception):  # type: ignore[no-redef]
        pass

    class RateLimitError(Exception):  # type: ignore[no-redef]
        pass

    class Timeout(Exception):  # type: ignore[no-redef]
        pass

    class APIError(Exception):  # type: ignore[no-redef]
        pass

    _LITELLM_ERRORS = ()
    _TRANSIENT_ERRORS = ()


from translatebot_django.providers import get_provider
from translatebot_django.utils import (
    DEFAULT_TIMEOUT_SECONDS,
    combine_translation_contexts,
    get_all_po_paths,
    get_api_key,
    get_app_translation_context,
    get_translation_context,
    is_modeltranslation_available,
)

logger = logging.getLogger(__name__)

# Retry configuration for rate limit errors
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 60  # Start with 60 seconds since rate limit is per minute

# Retry configuration for transient errors (timeouts, connection errors, 5xx):
# one wait per retry, so len() is the number of retries.
TRANSIENT_BACKOFF_SECONDS = (5, 15)


def _is_transient(exc):
    """Whether a litellm error is worth retrying after a short wait."""
    if isinstance(exc, _TRANSIENT_ERRORS):
        return True
    # litellm raises a generic APIError for 5xx responses it doesn't map to
    # a specific class (e.g. Cloudflare's 520-524 in front of a provider)
    status = getattr(exc, "status_code", None)
    return isinstance(exc, APIError) and isinstance(status, int) and status >= 500


def _retry_after_seconds(exc):
    """The wait a rate-limit error asks for (Retry-After), or None."""
    candidates = [getattr(exc, "litellm_response_headers", None)]
    response = getattr(exc, "response", None)
    candidates.append(getattr(response, "headers", None))
    for headers in candidates:
        if not headers:
            continue
        try:
            if headers.get("retry-after-ms") is not None:
                return float(headers["retry-after-ms"]) / 1000
            if headers.get("retry-after") is not None:
                return float(headers["retry-after"])
        except (TypeError, ValueError):
            # e.g. an HTTP-date Retry-After; fall back to our own backoff
            continue
    return None


_LITELLM_MISSING_MSG = (
    "The 'litellm' package is required to use LLM translation providers.\n"
    "Install it with: pip install translatebot-django[litellm]"
)


class TranslationValidationError(ValueError):
    """Raised when the LLM response fails structural validation."""


def _require_litellm():
    """Raise CommandError if litellm is not installed."""
    if not _has_litellm:
        raise CommandError(_LITELLM_MISSING_MSG)


_PARTIAL_SAVE_NOTE = "Any translations completed before this error have been saved."


@contextmanager
def handle_api_errors():
    """Convert API and response-validation exceptions into CommandErrors."""
    try:
        yield
    except AuthenticationError as e:
        raise CommandError(
            f"Authentication failed: {str(e)}\n"
            "Please check your API key configuration.\n"
            "Set TRANSLATEBOT_API_KEY in settings or "
            "TRANSLATEBOT_API_KEY environment variable."
        ) from e
    except BadRequestError as e:
        error_str = str(e).lower()
        if "credit balance" in error_str or "billing" in error_str:
            raise CommandError(
                "Insufficient API credits. Your credit balance is too low "
                "to access the API.\n"
                "Please visit your API provider's billing page to add credits."
            ) from e
        raise CommandError(f"API request failed: {str(e)}") from e
    except RateLimitError as e:
        raise CommandError(
            f"Rate limit still exceeded after {MAX_RETRIES} attempts: {e}\n"
            f"{_PARTIAL_SAVE_NOTE}"
        ) from e
    except Timeout as e:
        raise CommandError(
            "The API did not respond in time, even after "
            f"{len(TRANSIENT_BACKOFF_SECONDS)} retries: {e}\n"
            "The provider may be overloaded or down. If your model is just slow, "
            "raise TRANSLATEBOT_TIMEOUT (seconds) in your settings.\n"
            f"{_PARTIAL_SAVE_NOTE}"
        ) from e
    except TranslationValidationError as e:
        raise CommandError(
            f"Translation response validation failed: {e}\n{_PARTIAL_SAVE_NOTE}"
        ) from e
    except _LITELLM_ERRORS as e:
        # Every remaining litellm error: connection failures, timeouts,
        # 5xx responses, unknown models (404), ...
        raise CommandError(
            f"API request failed: {type(e).__name__}: {e}\n{_PARTIAL_SAVE_NOTE}"
        ) from e


def get_token_count(text):
    """Get the token count for a given text and model."""
    encoding = tiktoken.get_encoding("cl100k_base")
    tokens = encoding.encode(text)
    return len(tokens)


BASE_SYSTEM_PROMPT = (
    "You are a professional software localization translator.\n"
    "Important rules:\n"
    "- The input is a JSON array of strings (or objects with 'text' and optional "
    "'comment' fields). The output MUST be a JSON array of translated strings.\n"
    "- CRITICAL: The output array MUST have EXACTLY the same number of elements "
    "as the input array. Each input string at index N must have its translation "
    "at index N in the output. Never skip, merge, or omit any strings.\n"
    "- When an input element has a 'comment' field, use it as context to "
    "disambiguate the meaning, but do NOT include the comment in the output.\n"
    "- When an input element has a 'plural' field, it is a pluralized message: "
    "'text' is the singular source and 'plural' the plural source. Its output "
    "element MUST be a JSON array with exactly one translated string per entry "
    "in its 'plural_forms' field, in the same order. Each 'plural_forms' entry "
    "lists example counts that use that grammatical form in the target "
    "language.\n"
    "- Preserve all placeholders like %(name)s, {name}, {0}, %s exactly as-is.\n"
    "- Preserve HTML tags exactly as they are.\n"
    "- Preserve line breaks (\\n) in the text.\n"
    "- Do not change the order of the strings.\n"
    "- Return ONLY the JSON array of translated strings, nothing else.\n"
    "- Do NOT wrap the JSON in markdown code blocks. Return raw JSON only."
)

# Alias for backward compatibility
SYSTEM_PROMPT = BASE_SYSTEM_PROMPT
SYSTEM_PROMPT_LENGTH = get_token_count(BASE_SYSTEM_PROMPT) if _has_litellm else 0


def build_system_prompt(context=None):
    """
    Build the full system prompt, optionally including user-provided context.

    Args:
        context: Optional string containing translation context from TRANSLATING.md

    Returns:
        str: The complete system prompt
    """
    if not context:
        return BASE_SYSTEM_PROMPT

    return (
        f"{BASE_SYSTEM_PROMPT}\n\n"
        "## Project Context\n"
        "The following context has been provided by the project maintainers "
        "to help you produce accurate translations:\n\n"
        f"{context}"
    )


def create_preamble(target_lang, count):
    return (
        f"Translate the following {count} strings to the language"
        f" with language code '{target_lang}'. "
        f"Return only a valid JSON array with exactly {count} translated strings:\n"
    )


@dataclasses.dataclass(frozen=True)
class PluralText:
    """A pluralized message, translated into all of the target's plural forms.

    Only sent to providers whose ``supports_plural_forms`` is true; their
    translation for it is a list with one string per entry in *forms*.

    Attributes:
        singular: The msgid.
        plural: The msgid_plural.
        forms: One description per target plural form, listing example
            counts that use it (e.g. ``"1"``, ``"2, 3, 4, 22"``).
    """

    singular: str
    plural: str
    forms: tuple[str, ...]


# Counts scanned for plural-form examples; Arabic's last form starts at 100.
_PLURAL_SAMPLE_RANGE = range(0, 1000)
_PLURAL_SAMPLES_PER_FORM = 5


def plural_forms_from_header(header):
    """Describe each plural form declared by a PO ``Plural-Forms`` header.

    Returns:
        A tuple with one string per form listing example counts using it,
        e.g. ``("1", "2, 3, 4, 22, 23", "0, 5, 6, 7, 8")`` for Polish, or
        None when the header is missing or can't be evaluated.
    """
    if not header:
        return None
    nplurals_match = re.search(r"nplurals\s*=\s*(\d+)", header)
    plural_match = re.search(r"plural\s*=\s*([^;]+)", header)
    if not nplurals_match or not plural_match:
        return None
    nplurals = int(nplurals_match.group(1))
    if nplurals < 1:
        return None
    try:
        # gettext.c2py safely compiles the C plural expression used in PO
        # headers (it rejects anything but arithmetic on n).
        plural = gettext.c2py(plural_match.group(1).strip())
        samples = [[] for _ in range(nplurals)]
        for n in _PLURAL_SAMPLE_RANGE:
            form = plural(n)
            if 0 <= form < nplurals and len(samples[form]) < _PLURAL_SAMPLES_PER_FORM:
                samples[form].append(str(n))
    except (ValueError, SyntaxError, RecursionError, TypeError, ZeroDivisionError):
        return None
    return tuple(", ".join(s) if s else "(unused)" for s in samples)


def _align_comments(texts, comments):
    """Return comments as a list aligned with *texts*, or None if there are none.

    *comments* may be a dict mapping source strings to comments, or a
    sequence with one comment (or None) per text.
    """
    if not comments:
        return None
    if isinstance(comments, dict):
        aligned = [
            comments.get(t) if isinstance(t, str) else comments.get(t.singular)
            for t in texts
        ]
    else:
        aligned = list(comments)
    return aligned if any(aligned) else None


def _build_input_payload(texts, comments=None):
    """Build the JSON-serialisable input payload for the LLM.

    When any text has a comment or is a :class:`PluralText`, the payload uses
    an object format (``{"text": …, "comment": …}``).  Otherwise a plain
    list of strings is returned for backward-compatibility and token
    efficiency.

    Args:
        texts: List of strings and/or :class:`PluralText` objects.
        comments: Dict mapping source strings to comments, or a list of
            comments aligned with *texts*.
    """
    aligned = _align_comments(texts, comments)
    if aligned is None and all(isinstance(t, str) for t in texts):
        return texts

    payload = []
    for i, t in enumerate(texts):
        if isinstance(t, PluralText):
            item = {"text": t.singular, "plural": t.plural}
        else:
            item = {"text": t}
        if aligned and aligned[i]:
            item["comment"] = aligned[i]
        if isinstance(t, PluralText):
            item["plural_forms"] = list(t.forms)
        payload.append(item)
    return payload


def _output_shape(texts):
    """Approximate the translated output, for estimating output tokens."""
    return [
        [t.plural] * len(t.forms) if isinstance(t, PluralText) else t for t in texts
    ]


def translate_text(
    text,
    target_lang,
    model,
    api_key,
    context=None,
    comments=None,
    timeout=DEFAULT_TIMEOUT_SECONDS,
):
    """Translate text by calling LiteLLM, retrying rate limits and transient errors.

    Args:
        text: List of strings and/or :class:`PluralText` objects to translate
        target_lang: Target language code (e.g., 'nl', 'de')
        model: LLM model to use
        api_key: API key for the LLM provider
        context: Optional translation context from TRANSLATING.md
        comments: Optional developer comments extracted from PO files (#.
                  lines): a dict mapping source strings to comments, or a
                  list aligned with *text*.
        timeout: Seconds to wait for each API request before giving up on
                 it (and retrying, see :data:`TRANSIENT_BACKOFF_SECONDS`).

    Returns:
        A list aligned with *text*: a string per plain text, and a list of
        strings (one per plural form) per :class:`PluralText`.
    """
    _require_litellm()
    preamble = create_preamble(target_lang, len(text))
    system_prompt = build_system_prompt(context)
    input_payload = _build_input_payload(text, comments)

    attempt = 0
    transient_attempt = 0
    while True:
        try:
            # Suppress Pydantic serialization warnings from litellm.
            # litellm's response Message model has optional fields that
            # are left unset by most providers, which causes pydantic-core
            # to emit harmless UserWarning messages during serialization.
            # Observed in litellm 1.82.4 — check if still needed on upgrade.
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="Pydantic serializer warnings",
                    category=UserWarning,
                )
                response = completion(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": system_prompt,
                        },
                        {
                            "role": "user",
                            "content": preamble
                            + json.dumps(input_payload, ensure_ascii=False),
                        },
                    ],
                    temperature=0.2,
                    reasoning_effort="low",
                    drop_params=True,
                    api_key=api_key,
                    timeout=timeout,
                    # Retries happen below, with logging; the client's own
                    # silent retries would multiply the timeout.
                    max_retries=0,
                )
            break  # Success, exit retry loop
        except RateLimitError as e:
            if attempt >= MAX_RETRIES - 1:
                # All retries exhausted, re-raise the exception
                raise e from None
            # Exponential backoff: 60s, 120s, 240s, 480s, or sooner when
            # the provider says when to retry (the client's own quick 429
            # retries are disabled, see max_retries above)
            backoff = INITIAL_BACKOFF_SECONDS * (2**attempt)
            retry_after = _retry_after_seconds(e)
            if retry_after is not None:
                backoff = min(backoff, max(1, math.ceil(retry_after)))
            logger.warning(
                "Rate limit hit, waiting %ds before retry (%d/%d)...",
                backoff,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(backoff)
            attempt += 1
        except _LITELLM_ERRORS as e:
            if not _is_transient(e) or transient_attempt >= len(
                TRANSIENT_BACKOFF_SECONDS
            ):
                raise
            backoff = TRANSIENT_BACKOFF_SECONDS[transient_attempt]
            logger.warning(
                "%s from %s, waiting %ds before retry (%d/%d)...",
                type(e).__name__,
                model,
                backoff,
                transient_attempt + 1,
                len(TRANSIENT_BACKOFF_SECONDS),
            )
            time.sleep(backoff)
            transient_attempt += 1

    content = response.choices[0].message.content
    if content is None:
        raise TranslationValidationError(
            f"API returned empty response. Model: {model}, Response: {response}"
        )

    content = content.strip()
    if not content:
        raise TranslationValidationError(
            f"API returned empty content after stripping. Model: {model}"
        )

    # Extract JSON array if LLM added preamble text or wrapped in code blocks
    start = content.find("[")
    end = content.rfind("]")
    if start != -1 and end != -1:
        content = content[start : end + 1]

    preview = content[:500]
    context_suffix = f"Model: {model}\nContent preview: {preview}"

    try:
        translated = json.loads(content)
    except json.JSONDecodeError as e:
        raise TranslationValidationError(
            f"Failed to parse JSON response from API.\n{context_suffix}\nError: {e}"
        ) from e

    if not isinstance(translated, list):
        raise TranslationValidationError(
            f"API returned {type(translated).__name__} instead of a JSON array.\n"
            f"{context_suffix}"
        )

    if len(translated) != len(text):
        raise TranslationValidationError(
            f"API returned {len(translated)} translations, expected {len(text)}.\n"
            f"{context_suffix}"
        )

    non_strings = [
        i
        for i, (src, v) in enumerate(zip(text, translated, strict=True))
        if not isinstance(src, PluralText) and not isinstance(v, str)
    ]
    if non_strings:
        raise TranslationValidationError(
            f"API returned non-string elements at indices {non_strings[:5]}.\n"
            f"{context_suffix}"
        )

    bad_plurals = [
        i
        for i, (src, v) in enumerate(zip(text, translated, strict=True))
        if isinstance(src, PluralText)
        and not (
            isinstance(v, list)
            and len(v) == len(src.forms)
            and all(isinstance(form, str) for form in v)
        )
    ]
    if bad_plurals:
        raise TranslationValidationError(
            "API returned malformed plural translations at indices "
            f"{bad_plurals[:5]} (expected one string per plural form).\n"
            f"{context_suffix}"
        )

    return translated


# Practical output-token ceiling per batch, regardless of the model's
# advertised max_output_tokens. Structured-output quality degrades well
# before the advertised cap on most models, so keeping batches under
# this ceiling improves reliability for JSON translation.
# See issue #156.
PRACTICAL_OUTPUT_BUDGET = 8000

# Conservative defaults when litellm doesn't recognize a model. The output
# fallback intentionally stays below PRACTICAL_OUTPUT_BUDGET so unknown
# models batch extra-cautiously; raising it above the practical budget
# would have no effect (the min() cap below would still win).
_FALLBACK_INPUT_TOKENS = 8192
_FALLBACK_OUTPUT_TOKENS = 4096


def _get_model_limits(model):
    """Return ``(max_input_tokens, effective_max_output_tokens)`` for a model.

    The output value is capped at :data:`PRACTICAL_OUTPUT_BUDGET` so batches
    stay in the reliable zone for structured output, even when the model
    advertises a much larger capacity.

    When litellm does not recognize the model, or when looking up its info
    raises any error, returns conservative fallbacks
    (:data:`_FALLBACK_INPUT_TOKENS` / :data:`_FALLBACK_OUTPUT_TOKENS`) and
    emits a warning. Users seeing unexpectedly small batches should verify
    the model name is correct and recognized by litellm.
    """
    _require_litellm()
    try:
        info = get_model_info(model)
        max_input = info.get("max_input_tokens")
        if max_input is None:
            max_input = _FALLBACK_INPUT_TOKENS
        max_output = info.get("max_output_tokens")
        if max_output is None:
            max_output = _FALLBACK_OUTPUT_TOKENS
    except Exception as exc:
        # litellm raises a bare Exception for unknown models, so we can't
        # narrow the catch. Surface the failure via the logger so users
        # notice when a typo in the model name is silently shrinking their
        # batches.
        logger.warning(
            "Could not look up token limits for model %r (%s). "
            "Falling back to conservative defaults "
            "(max_input=%d, max_output=%d). "
            "Verify that the model name is correct.",
            model,
            exc,
            _FALLBACK_INPUT_TOKENS,
            _FALLBACK_OUTPUT_TOKENS,
        )
        max_input = _FALLBACK_INPUT_TOKENS
        max_output = _FALLBACK_OUTPUT_TOKENS

    return max_input, min(max_output, PRACTICAL_OUTPUT_BUDGET)


def batch_by_tokens(texts, target_lang, model, comments=None):
    """Split texts into token-sized groups for LLM translation.

    Splits such that each batch stays within both the model's input budget
    (``max_input_tokens``) and a practical output budget
    (``min(max_output_tokens, PRACTICAL_OUTPUT_BUDGET)``). The latter
    protects quality on models with very large advertised output caps.

    Args:
        texts: List of strings and/or :class:`PluralText` objects to split
            into batches.
        target_lang: Target language code.
        model: LLM model name (used for token limit lookup).
        comments: Optional developer comments: a dict mapping source strings
            to comments, or a list aligned with *texts*.

    Returns:
        List of lists of texts: contiguous, order-preserving slices of
        *texts*.
    """
    _require_litellm()
    max_input, max_output = _get_model_limits(model)
    aligned = _align_comments(texts, comments)

    groups = []
    group_candidate = []
    group_start = 0
    for index, item in enumerate(texts):
        group_candidate += [item]
        group_comments = aligned[group_start : index + 1] if aligned else None

        input_payload = _build_input_payload(group_candidate, group_comments)
        input_tokens = get_token_count(json.dumps(input_payload, ensure_ascii=False))
        preamble_tokens = get_token_count(
            create_preamble(target_lang, len(group_candidate))
        )
        text_only_tokens = get_token_count(
            json.dumps(_output_shape(group_candidate), ensure_ascii=False)
        )
        output_estimate = text_only_tokens * 1.3

        input_total = input_tokens + preamble_tokens
        if input_total > max_input or output_estimate > max_output:
            if len(group_candidate) > 1:
                groups.append(group_candidate[:-1])
            group_candidate = [item]
            group_start = index

    groups.append(group_candidate)
    return groups


@dataclasses.dataclass
class POUnit:
    """One PO message to translate, identified by ``(msgctxt, msgid)``.

    Attributes:
        msgctxt: The message context (``pgettext``), or None.
        msgid: The source string.
        msgid_plural: The plural source string, for pluralized messages.
        comment: Hint for the translator, built from the msgctxt and the
            extracted developer comments (``#.`` lines), or None.
        plural_forms: Example counts per target plural form, from the PO
            file's ``Plural-Forms`` header (see
            :func:`plural_forms_from_header`), or None if unknown.
        nplurals: Number of plural forms to write.
    """

    msgctxt: str | None
    msgid: str
    msgid_plural: str | None = None
    comment: str | None = None
    plural_forms: tuple[str, ...] | None = None
    nplurals: int = 2

    @property
    def key(self):
        return (self.msgctxt, self.msgid)

    def absorb(self, other):
        """Merge in the same message gathered from another PO file.

        The last non-empty comment wins. A plural version of the message wins
        over a plain one, so that its plural is translated too, and known
        plural forms win over unknown ones (or fewer ones).
        """
        if other.comment:
            self.comment = other.comment
        if other.msgid_plural is None:
            return
        if self.msgid_plural is None:
            self.msgid_plural = other.msgid_plural
            self.plural_forms = other.plural_forms
            self.nplurals = other.nplurals
        elif other.plural_forms and len(other.plural_forms) > len(
            self.plural_forms or ()
        ):
            self.plural_forms = other.plural_forms
            self.nplurals = other.nplurals

    def provider_texts(self, plural_aware):
        """The texts to send to a provider for this message."""
        if self.msgid_plural is None:
            return [self.msgid]
        if plural_aware and self.plural_forms:
            return [PluralText(self.msgid, self.msgid_plural, self.plural_forms)]
        return [self.msgid, self.msgid_plural]

    def translation_from(self, results):
        """Combine the provider's results for :meth:`provider_texts`.

        Returns a string, or for pluralized messages a list of plural forms.
        """
        if self.msgid_plural is None:
            return results[0]
        if len(results) == 1:
            # A PluralText: the provider translated every form itself
            forms = results[0]
            return PluralForms(forms, singular=forms[self._singular_index()])
        # Singular and plural were translated as two plain strings; reuse
        # the plural translation for every form past the first.
        singular, plural = results
        return PluralForms(
            [singular] + [plural] * (self.nplurals - 1), singular=singular
        )

    def _singular_index(self):
        """Index of the plural form used for a count of 1 (not always 0:
        Arabic's form 0 is for zero)."""
        for index, examples in enumerate(self.plural_forms or ()):
            if "1" in examples.split(", "):
                return index
        return 0


class PluralForms(list):
    """The translated plural forms of a message, in ``msgstr[n]`` order.

    *singular* is the form for a count of 1, written to plain (non-plural)
    entries of the same message in other PO files.
    """

    def __init__(self, forms, singular):
        super().__init__(forms)
        self.singular = singular


def _entry_comment(entry):
    """Build the translator hint for a PO entry from its msgctxt and comments."""
    parts = []
    if entry.msgctxt:
        parts.append(f"Context: {entry.msgctxt}")
    if entry.comment and entry.comment.strip():
        parts.append(entry.comment.strip())
    return "\n".join(parts) or None


def _is_translated(entry):
    if entry.msgid_plural:
        return bool(entry.msgstr_plural) and all(entry.msgstr_plural.values())
    return bool(entry.msgstr)


def gather_entries(po_path, include_translated=False):
    """Gather the messages to translate from a PO file.

    Empty and fuzzy entries are always gathered; translated ones only when
    *include_translated* is true. Obsolete entries are skipped.

    Returns:
        A list of :class:`POUnit`, one per distinct ``(msgctxt, msgid)``.
    """
    po = polib.pofile(str(po_path), wrapwidth=79)
    plural_forms = plural_forms_from_header(po.metadata.get("Plural-Forms"))
    if plural_forms is None and any(e.msgid_plural for e in po):
        logger.warning(
            "%s has no usable Plural-Forms header (got %r); its plural "
            "entries get the singular and plural translation only. Set the "
            "header for the language, e.g. by re-running makemessages.",
            po_path,
            po.metadata.get("Plural-Forms"),
        )
    units = {}

    for entry in po:
        if not entry.msgid or entry.obsolete:
            continue
        if _is_translated(entry) and not include_translated and not entry.fuzzy:
            continue
        key = (entry.msgctxt, entry.msgid)
        if key in units:
            continue
        units[key] = POUnit(
            msgctxt=entry.msgctxt,
            msgid=entry.msgid,
            msgid_plural=entry.msgid_plural or None,
            comment=_entry_comment(entry),
            plural_forms=plural_forms,
            nplurals=(
                len(plural_forms) if plural_forms else len(entry.msgstr_plural) or 2
            ),
        )

    return list(units.values())


class Command(BaseCommand):
    help = "Automatically translate .po files and/or model fields using AI"

    def add_arguments(self, parser):
        from django.conf import settings

        # Check if LANGUAGES is defined in settings
        has_languages = hasattr(settings, "LANGUAGES") and settings.LANGUAGES

        parser.add_argument(
            "--target-lang",
            action="append",
            required=not has_languages,  # Optional if LANGUAGES is defined
            help="Target language code, e.g. de, fr, nl. "
            "Can be used multiple times to translate to multiple languages. "
            + (
                "Optional when LANGUAGES is defined in settings - "
                "will translate to all configured languages."
                if has_languages
                else ""
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not write changes, only show what would be translated.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Also re-translate entries that already have a msgstr.",
        )

        parser.add_argument(
            "--app",
            action="append",
            dest="apps",
            metavar="APP_LABEL",
            help="Only translate .po files for the specified Django app. "
            "Can be used multiple times to include multiple apps.",
        )

        parser.add_argument(
            "--llm-model",
            default=None,
            help="LLM model name to use for this run, overriding TRANSLATEBOT_MODEL "
            "(e.g. 'gpt-4o', 'claude-3-5-sonnet-20241022'). "
            "Not supported with the DeepL provider — raises an error if passed.",
        )

        # Only add modeltranslation-related arguments if it's available
        if is_modeltranslation_available():
            parser.add_argument(
                "--models",
                nargs="*",
                metavar="MODEL",
                help="Translate django-modeltranslation model fields. "
                "Optionally specify model names (e.g., Article Product). "
                "Requires django-modeltranslation to be installed.",
            )

    def handle(self, *args, **options):
        from django.conf import settings

        target_lang = options.get("target_lang")
        dry_run = options["dry_run"]
        overwrite = options["overwrite"]
        models_arg = options.get("models")
        app_labels = options.get("apps")

        # Determine target languages
        source_lang = getattr(settings, "LANGUAGE_CODE", "en-us")
        # Normalize source language: "en-us" -> "en" for comparison
        source_lang_base = source_lang.split("-")[0]

        target_langs = []
        if target_lang:
            # Normalize: action="append" gives a list from CLI, but
            # call_command(target_lang="nl") passes a plain string.
            if isinstance(target_lang, str):
                target_langs = [target_lang]
            else:
                target_langs = list(target_lang)
        elif hasattr(settings, "LANGUAGES") and settings.LANGUAGES:
            # Use all configured languages, excluding the source language.
            # Exclude exact matches and the bare base language (e.g.
            # LANGUAGE_CODE "en-us" excludes "en"), but keep other regional
            # variants (e.g. "en-gb" is kept).
            target_langs = [
                lang_code
                for lang_code, _ in settings.LANGUAGES
                if lang_code != source_lang and lang_code != source_lang_base
            ]
            if not target_langs:
                raise CommandError(
                    "No target languages to translate to after excluding "
                    f"source language '{source_lang}'."
                )
            self.stdout.write(
                f"ℹ️  No --target-lang specified. "
                f"Translating to all configured languages: {', '.join(target_langs)}"
            )
        else:
            raise CommandError(
                "--target-lang is required when LANGUAGES is not defined in settings."
            )

        # Determine what to translate
        translate_po = models_arg is None  # Default: translate .po files
        translate_models = models_arg is not None  # --models flag present

        # --app only applies to .po file translation, not model translation
        if app_labels and translate_models:
            raise CommandError(
                "--app cannot be used together with --models. "
                "The --app flag only filters .po file translation."
            )

        # If --models flag is used, check if modeltranslation is available
        if translate_models and not is_modeltranslation_available():
            raise CommandError(
                "django-modeltranslation is not installed or not in "
                "INSTALLED_APPS.\n"
                "Install it with: pip install django-modeltranslation\n"
                "See: https://github.com/deschler/django-modeltranslation"
            )

        api_key = get_api_key()
        llm_model = options.get("llm_model")
        provider = get_provider(api_key, model=llm_model)

        # Load translation context from TRANSLATING.md if available
        context = get_translation_context()
        if context:
            if provider.supports_context:
                self.stdout.write(
                    self.style.SUCCESS(
                        "📋 Found TRANSLATING.md - using project context"
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"📋 Found TRANSLATING.md but {provider.name} does not "
                        "support custom context - ignoring"
                    )
                )

        # Aggregate statistics across all languages
        total_strings_found = 0
        total_strings_translated = 0
        total_po_files = 0
        total_model_fields_found = 0
        total_model_fields_translated = 0

        # Process each target language
        for lang in target_langs:
            if len(target_langs) > 1:
                self.stdout.write("\n" + "=" * 60)
                self.stdout.write(f"🌍 Processing language: {lang}")
                self.stdout.write("=" * 60)

            # Handle .po file translation (existing logic)
            if translate_po:
                po_stats = self._translate_po_files(
                    lang,
                    dry_run,
                    overwrite,
                    provider,
                    context,
                    app_labels=app_labels,
                )
                total_strings_found += po_stats["strings_found"]
                total_strings_translated += po_stats["strings_translated"]
                total_po_files += po_stats["po_files"]

            # Handle model field translation (NEW)
            if translate_models:
                model_stats = self._translate_model_fields(
                    target_lang=lang,
                    dry_run=dry_run,
                    overwrite=overwrite,
                    provider=provider,
                    model_names=models_arg,
                    context=context,
                )
                total_model_fields_found += model_stats["model_fields_found"]
                total_model_fields_translated += model_stats["model_fields_translated"]

        if len(target_langs) > 1:
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write(
                self.style.SUCCESS(
                    f"✨ Completed translation for {len(target_langs)} languages: "
                    f"{', '.join(target_langs)}"
                )
            )
            self.stdout.write("=" * 60)

        # Read by api.translate() to build TranslateResult — keep in sync.
        self._translate_stats = {
            "strings_found": total_strings_found,
            "strings_translated": total_strings_translated,
            "po_files": total_po_files,
            "model_fields_found": total_model_fields_found,
            "model_fields_translated": total_model_fields_translated,
            "target_langs": target_langs,
        }

    @staticmethod
    def _save_po_translations(po_paths, translations, overwrite=False):
        """Write current translations to PO files on disk.

        Called after each successful batch so that translations are persisted
        incrementally and not lost if a later batch fails.

        Args:
            po_paths: PO files to update.
            translations: Dict mapping ``(msgctxt, msgid)`` to a translated
                string, or for pluralized messages a list of plural forms.
            overwrite: Also replace existing non-fuzzy translations.
        """
        for po_path in po_paths:
            po = polib.pofile(str(po_path), wrapwidth=79)
            changed = False

            for entry in po:
                key = (entry.msgctxt, entry.msgid)
                if key not in translations:
                    continue
                # Never replace existing non-fuzzy translations unless
                # the user explicitly requested --overwrite.
                if not overwrite and not entry.fuzzy and _is_translated(entry):
                    continue
                value = translations[key]
                forms = value if isinstance(value, list) else [value]
                if entry.msgid_plural:
                    count = len(entry.msgstr_plural) or len(forms)
                    # Repeat the last form if the file declares more plural
                    # forms than were translated.
                    entry.msgstr_plural = {
                        i: forms[min(i, len(forms) - 1)] for i in range(count)
                    }
                elif isinstance(value, PluralForms):
                    entry.msgstr = value.singular
                else:
                    entry.msgstr = forms[0]
                if entry.fuzzy:
                    entry.flags.remove("fuzzy")
                changed = True

            if changed:
                po.save(str(po_path))

    def _translate_po_files(
        self,
        target_lang,
        dry_run,
        overwrite,
        provider,
        context=None,
        app_labels=None,
    ):
        """Translate .po files (existing logic refactored into method).

        Returns:
            dict with ``strings_found``, ``strings_translated``, and
            ``po_files`` counts.
        """
        # Find all .po files for the target language
        po_paths = get_all_po_paths(target_lang, app_labels=app_labels)

        # Group po_paths by effective translation context
        context_groups = defaultdict(list)  # effective_context -> [po_paths]
        for po_path in po_paths:
            app_ctx = get_app_translation_context(po_path)
            if app_ctx:
                app_dir = Path(po_path).resolve().parent.parent.parent.parent
                if provider.supports_context:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"📋 Found TRANSLATING.md for {app_dir.name}"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"📋 Found TRANSLATING.md for {app_dir.name} "
                            f"but {provider.name} does not support custom "
                            "context - ignoring"
                        )
                    )
            effective = combine_translation_contexts(context, app_ctx)
            context_groups[effective].append(po_path)

        # Gather the messages of each context group; a message shared by
        # several files is translated once
        work = []  # (effective_context, group_po_paths, units)
        pending = {}  # po_path -> keys of the entries it needs translated
        for effective_context, group_po_paths in context_groups.items():
            units = {}
            for po_path in group_po_paths:
                file_units = gather_entries(po_path, include_translated=overwrite)
                pending[po_path] = {unit.key for unit in file_units}
                for unit in file_units:
                    known = units.setdefault(unit.key, unit)
                    if known is not unit:
                        known.absorb(unit)
            if units:
                work.append((effective_context, group_po_paths, list(units.values())))

        # Counted per file, like strings_translated: a message shared by
        # several files is sent once but written (and counted) per file
        total_msgids = sum(len(keys) for keys in pending.values())

        # Early return with minimal output if nothing to translate
        if total_msgids == 0:
            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✨ Language '{target_lang}': No untranslated entries found"
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✨ Language '{target_lang}': Already up to date"
                    )
                )
            return {
                "strings_found": 0,
                "strings_translated": 0,
                "po_files": len(po_paths),
            }

        self.stdout.write(f"ℹ️  Found {total_msgids} untranslated entries")

        # po_path -> keys of the entries translated (or, in a dry run, to be
        # translated) in that file
        done = {po_path: set() for po_path in po_paths}
        if dry_run:
            self.stdout.write("🔍 Dry run mode: skipping translation")
            done.update(pending)
        else:
            self.stdout.write(f"🔄 Translating with {provider.name}...")
            for effective_context, group_po_paths, units in work:
                # Per group, so one group's translation (made with its own
                # TRANSLATING.md) is never written into another group's files
                translations = {}
                self._translate_po_units(
                    units,
                    group_po_paths,
                    translations,
                    target_lang=target_lang,
                    provider=provider,
                    context=effective_context,
                    overwrite=overwrite,
                )
                for po_path in group_po_paths:
                    done[po_path] = pending[po_path] & translations.keys()

        # Report what was translated and save PO files for dry-run
        total_changed = 0
        for po_path in po_paths:
            self.stdout.write(self.style.NOTICE(f"\nProcessing: {po_path}"))
            po = polib.pofile(str(po_path), wrapwidth=79)
            changed = 0

            for entry in po:
                if (entry.msgctxt, entry.msgid) in done[po_path]:
                    if dry_run:
                        self.stdout.write(f"✓ Would translate '{entry.msgid[:50]}'")
                    else:
                        self.stdout.write(f"✓ Translated '{entry.msgid[:50]}'")
                    changed += 1

            if dry_run:
                self.stdout.write(
                    self.style.NOTICE(
                        f"Dry run: {changed} entries would be updated in {po_path}"
                    )
                )
            elif changed > 0:
                self.stdout.write(
                    self.style.SUCCESS(f"✨ Successfully updated {po_path}")
                )

            total_changed += changed

        self.stdout.write("\n" + "=" * 60)
        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✨ Successfully translated {total_changed} entries "
                    f"across {len(po_paths)} file(s)"
                )
            )
        else:
            self.stdout.write(
                self.style.NOTICE(
                    f"Dry run complete: {total_changed} entries would be "
                    f"translated across {len(po_paths)} file(s)"
                )
            )

        return {
            "strings_found": total_msgids,
            "strings_translated": total_changed,
            "po_files": len(po_paths),
        }

    def _translate_po_units(
        self, units, po_paths, translations, target_lang, provider, context, overwrite
    ):
        """Translate *units* in batches, saving *po_paths* after each batch.

        Adds each translation to *translations*, keyed by ``(msgctxt, msgid)``.
        """
        # Flatten to provider texts, remembering which slice of them
        # belongs to which message (a message may span two texts).
        texts = []
        text_comments = []
        spans = []
        for unit in units:
            start = len(texts)
            for text in unit.provider_texts(provider.supports_plural_forms):
                texts.append(text)
                text_comments.append(unit.comment)
            spans.append((start, len(texts)))

        groups = provider.batch(texts, target_lang, comments=text_comments)

        with handle_api_errors():
            results = []
            done = 0
            for batch_num, group in enumerate(groups, 1):
                start = len(results)
                batch_comments = text_comments[start : start + len(group)]
                translated = provider.translate(
                    texts=group,
                    target_lang=target_lang,
                    context=context,
                    comments=batch_comments if any(batch_comments) else None,
                )
                if len(translated) != len(group):
                    raise TranslationValidationError(
                        f"{provider.name} returned {len(translated)} "
                        f"translations, expected {len(group)}."
                    )
                results.extend(translated)

                # Record every message whose texts are now all translated
                while done < len(units) and spans[done][1] <= len(results):
                    unit_start, unit_end = spans[done]
                    translations[units[done].key] = units[done].translation_from(
                        results[unit_start:unit_end]
                    )
                    done += 1

                # Save PO files after each batch so translations
                # aren't lost if a later batch fails
                self._save_po_translations(po_paths, translations, overwrite=overwrite)
                self.stdout.write(f"  💾 Saved batch {batch_num}/{len(groups)}")

    def _translate_model_fields(
        self,
        target_lang,
        dry_run,
        overwrite,
        provider,
        model_names=None,
        context=None,
    ):
        """Translate django-modeltranslation model fields.

        Returns:
            dict with ``model_fields_found`` and ``model_fields_translated``
            counts.
        """
        from translatebot_django.backends.modeltranslation import (
            ModeltranslationBackend,
        )

        backend = ModeltranslationBackend(target_lang)

        # Parse model names if provided
        models_to_translate = None
        if model_names:
            try:
                models_to_translate = backend.parse_model_names(model_names)
            except ValueError as e:
                raise CommandError(str(e)) from e

        # Gather translatable content
        self.stdout.write("🔍 Gathering translatable model fields...")
        items = backend.gather_translatable_content(
            model_list=models_to_translate, only_empty=not overwrite
        )

        if not items:
            self.stdout.write(
                self.style.SUCCESS("✨ No untranslated model fields found")
            )
            return {"model_fields_found": 0, "model_fields_translated": 0}

        self.stdout.write(f"ℹ️  Found {len(items)} model fields to translate")

        # Group items by model for reporting
        by_model = {}
        for item in items:
            model_name = item["model"].__name__
            if model_name not in by_model:
                by_model[model_name] = 0
            by_model[model_name] += 1

        for model_name, count in by_model.items():
            self.stdout.write(f"  • {model_name}: {count} field(s)")

        # A batch is one provider request with a single source language, so
        # items are split per source language first (rows missing the
        # default-language text fall back to another language's column).
        items_by_source_lang = {}
        for item in items:
            items_by_source_lang.setdefault(item.get("source_lang"), []).append(item)

        groups = []
        for lang_items in items_by_source_lang.values():
            # Batch the source texts using the provider's batching strategy
            source_texts = [item["source_text"] for item in lang_items]
            text_batches = provider.batch(source_texts, target_lang)

            # Re-associate batched texts with their items
            item_idx = 0
            for text_batch in text_batches:
                batch_items = lang_items[item_idx : item_idx + len(text_batch)]
                groups.append((text_batch, batch_items))
                item_idx += len(text_batch)

        # Translate all groups
        if dry_run:
            self.stdout.write("🔍 Dry run mode: skipping translation")
            updated = sum(len(items_group) for _, items_group in groups)
        else:
            batch_count = len(groups)
            self.stdout.write(
                f"🔄 Translating model fields with {provider.name} "
                f"({batch_count} batches)..."
            )

            updated = 0
            with handle_api_errors():
                for batch_num, (texts_group, items_group) in enumerate(groups, 1):
                    translations = provider.translate(
                        texts_group,
                        target_lang,
                        context=context,
                        source_lang=items_group[0].get("source_lang"),
                    )

                    batch_items = []
                    pairs = zip(items_group, translations, strict=True)
                    for item, translation in pairs:
                        translation = backend.normalize_translation(
                            item["model"],
                            item["field"],
                            translation,
                            item["source_text"],
                        )
                        # Pass the gathered item through whole: apply needs
                        # field/source_text for original-column syncing on
                        # top of instance/target_field/backfill_field.
                        batch_items.append({**item, "translation": translation})

                        model_name = item["model"].__name__
                        field_name = item["field"]
                        source_preview = item["source_text"][:50]
                        translation_preview = translation[:50]

                        self.stdout.write(
                            f"✓ {model_name}.{field_name}: "
                            f"'{source_preview}' → '{translation_preview}'"
                        )

                    # Save after each batch so translations aren't lost if
                    # a later batch fails
                    updated += backend.apply_translations(batch_items, dry_run=dry_run)
                    self.stdout.write(f"  💾 Saved batch {batch_num}/{batch_count}")

        self.stdout.write("\n" + "=" * 60)
        if dry_run:
            self.stdout.write(
                self.style.NOTICE(
                    f"Dry run: {updated} model field(s) would be translated"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✨ Successfully translated {updated} model field(s)"
                )
            )

        return {
            "model_fields_found": len(items),
            "model_fields_translated": updated,
        }
