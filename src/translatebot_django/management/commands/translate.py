import json
import logging
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import polib

from django.core.management.base import BaseCommand, CommandError

try:
    import tiktoken
    from litellm import completion, get_max_tokens
    from litellm.exceptions import (
        AuthenticationError,
        BadRequestError,
        RateLimitError,
    )

    _has_litellm = True
except ImportError:
    _has_litellm = False
    completion = None  # type: ignore[assignment]
    get_max_tokens = None  # type: ignore[assignment]

    # Sentinel classes so except clauses are syntactically valid.
    # They can never be raised when litellm is absent.
    class AuthenticationError(Exception):  # type: ignore[no-redef]
        pass

    class BadRequestError(Exception):  # type: ignore[no-redef]
        pass

    class RateLimitError(Exception):  # type: ignore[no-redef]
        pass


from translatebot_django.providers import get_provider
from translatebot_django.utils import (
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


_LITELLM_MISSING_MSG = (
    "The 'litellm' package is required to use LLM translation providers.\n"
    "Install it with: pip install translatebot-django[litellm]"
)


def _require_litellm():
    """Raise CommandError if litellm is not installed."""
    if not _has_litellm:
        raise CommandError(_LITELLM_MISSING_MSG)


@contextmanager
def handle_api_errors():
    """Context manager for handling API errors with user-friendly messages."""
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
        f"Translate the following {count} strings to {target_lang}. "
        f"Return a JSON array with exactly {count} translated strings:\n"
    )


def _build_input_payload(texts, comments=None):
    """Build the JSON-serialisable input payload for the LLM.

    When *comments* contains entries for any of the *texts*, the payload uses
    an object format (``{"text": …, "comment": …}``).  Otherwise a plain
    list of strings is returned for backward-compatibility and token
    efficiency.
    """
    if comments:
        payload = []
        for s in texts:
            if s in comments:
                payload.append({"text": s, "comment": comments[s]})
            else:
                payload.append({"text": s})
        return payload
    return texts


def translate_text(text, target_lang, model, api_key, context=None, comments=None):
    """Translate text by calling LiteLLM with retry logic for rate limits.

    Args:
        text: List of strings to translate
        target_lang: Target language code (e.g., 'nl', 'de')
        model: LLM model to use
        api_key: API key for the LLM provider
        context: Optional translation context from TRANSLATING.md
        comments: Optional dict mapping source strings to developer comments
                  extracted from PO files (#. lines).
    """
    _require_litellm()
    preamble = create_preamble(target_lang, len(text))
    system_prompt = build_system_prompt(context)
    input_payload = _build_input_payload(text, comments)

    attempt = 0
    while True:
        try:
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
                temperature=0.2,  # Low randomness for consistency
                api_key=api_key,
            )
            break  # Success, exit retry loop
        except RateLimitError as e:
            if attempt >= MAX_RETRIES - 1:
                # All retries exhausted, re-raise the exception
                raise e from None
            # Exponential backoff: 60s, 120s, 240s, 480s
            backoff = INITIAL_BACKOFF_SECONDS * (2**attempt)
            logger.warning(
                "Rate limit hit, waiting %ds before retry (%d/%d)...",
                backoff,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(backoff)
            attempt += 1

    content = response.choices[0].message.content
    if content is None:
        raise ValueError(
            f"API returned empty response. Model: {model}, Response: {response}"
        )

    content = content.strip()
    if not content:
        raise ValueError(f"API returned empty content after stripping. Model: {model}")

    # Extract JSON array if LLM added preamble text or wrapped in code blocks
    start = content.find("[")
    end = content.rfind("]")
    if start != -1 and end != -1:
        content = content[start : end + 1]

    try:
        translated = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Failed to parse JSON response from API.\n"
            f"Model: {model}\n"
            f"Content preview (first 500 chars): {content[:500]}\n"
            f"Error: {e}"
        ) from e

    return translated


def batch_by_tokens(texts, target_lang, model, comments=None):
    """Split texts into token-sized groups for LLM translation.

    Args:
        texts: List of strings to split into batches.
        target_lang: Target language code.
        model: LLM model name (used for token limit lookup).
        comments: Optional dict mapping source strings to developer comments.

    Returns:
        List of lists of strings.
    """
    _require_litellm()
    groups = []
    group_candidate = []
    for item in texts:
        group_candidate += [item]

        # Use the actual payload (with comments) for input token counting
        input_payload = _build_input_payload(group_candidate, comments)
        total = get_token_count(json.dumps(input_payload, ensure_ascii=False))
        # Output estimate based on text-only tokens (comments don't appear in output)
        text_only_tokens = get_token_count(
            json.dumps(group_candidate, ensure_ascii=False)
        )
        output_tokens_estimate = text_only_tokens * 1.3
        preamble = create_preamble(target_lang, len(group_candidate))
        preamble_length = get_token_count(preamble)

        if total + preamble_length + output_tokens_estimate > get_max_tokens(model):
            if len(group_candidate) > 1:
                groups.append(group_candidate[:-1])
            group_candidate = [item]

    groups.append(group_candidate)
    return groups


def gather_strings(po_path, only_empty=True):
    """Gather translatable strings and developer comments from a PO file.

    Returns:
        A tuple of (strings, comments) where *strings* is a list of msgid
        values to translate and *comments* is a dict mapping msgid strings
        to their extracted developer comments (the ``#.`` lines in PO files).
    """
    po = polib.pofile(str(po_path), wrapwidth=79)
    ret = []
    seen = set()
    comments = {}

    for entry in po:
        if not entry.msgid or entry.obsolete:
            continue

        if entry.msgid_plural:
            # Plural entry: check msgstr_plural values instead of msgstr
            has_translation = entry.msgstr_plural and all(entry.msgstr_plural.values())
            if has_translation and not only_empty and not entry.fuzzy:
                continue
            for s in (entry.msgid, entry.msgid_plural):
                if s not in seen:
                    seen.add(s)
                    ret.append(s)
        else:
            # Skip entries with translations unless they're fuzzy or only_empty is True
            if entry.msgstr and not only_empty and not entry.fuzzy:
                continue
            ret.append(entry.msgid)

        # Capture extracted comment only for entries that will be translated
        if entry.comment and entry.comment.strip():
            stripped = entry.comment.strip()
            comments[entry.msgid] = stripped
            if entry.msgid_plural:
                comments[entry.msgid_plural] = stripped

    return ret, comments


class Command(BaseCommand):
    help = "Automatically translate .po files and/or model fields using AI"

    def add_arguments(self, parser):
        from django.conf import settings

        # Check if LANGUAGES is defined in settings
        has_languages = hasattr(settings, "LANGUAGES") and settings.LANGUAGES

        parser.add_argument(
            "--target-lang",
            required=not has_languages,  # Optional if LANGUAGES is defined
            help="Target language code, e.g. de, fr, nl. "
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
        target_langs = []
        if target_lang:
            # Specific language provided
            target_langs = [target_lang]
        elif hasattr(settings, "LANGUAGES") and settings.LANGUAGES:
            # Use all configured languages
            target_langs = [lang_code for lang_code, _ in settings.LANGUAGES]
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
        provider = get_provider(api_key)

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

        # Process each target language
        for lang in target_langs:
            if len(target_langs) > 1:
                self.stdout.write("\n" + "=" * 60)
                self.stdout.write(f"🌍 Processing language: {lang}")
                self.stdout.write("=" * 60)

            # Handle .po file translation (existing logic)
            if translate_po:
                self._translate_po_files(
                    lang,
                    dry_run,
                    overwrite,
                    provider,
                    context,
                    app_labels=app_labels,
                )

            # Handle model field translation (NEW)
            if translate_models:
                self._translate_model_fields(
                    target_lang=lang,
                    dry_run=dry_run,
                    overwrite=overwrite,
                    provider=provider,
                    model_names=models_arg,
                    context=context,
                )

        if len(target_langs) > 1:
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write(
                self.style.SUCCESS(
                    f"✨ Completed translation for {len(target_langs)} languages: "
                    f"{', '.join(target_langs)}"
                )
            )
            self.stdout.write("=" * 60)

    def _translate_po_files(
        self,
        target_lang,
        dry_run,
        overwrite,
        provider,
        context=None,
        app_labels=None,
    ):
        """Translate .po files (existing logic refactored into method)."""
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

        # Process each context group
        msgid_to_translation = {}
        total_msgids = 0

        for effective_context, group_po_paths in context_groups.items():
            all_msgids = []
            all_comments = {}
            for po_path in group_po_paths:
                strings, comments = gather_strings(po_path, only_empty=overwrite)
                all_msgids.extend(strings)
                all_comments.update(comments)

            total_msgids += len(all_msgids)

            if not all_msgids:
                continue

            groups = provider.batch(all_msgids, target_lang, comments=all_comments)

            if dry_run:
                for group in groups:
                    for msgid in group:
                        msgid_to_translation[msgid] = ""
            else:
                with handle_api_errors():
                    for group in groups:
                        batch_comments = (
                            {t: all_comments[t] for t in group if t in all_comments}
                            or None
                        )
                        translated = provider.translate(
                            texts=group,
                            target_lang=target_lang,
                            context=effective_context,
                            comments=batch_comments,
                        )
                        for msgid, translation in zip(group, translated, strict=True):
                            msgid_to_translation[msgid] = translation

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
            return

        self.stdout.write(f"ℹ️  Found {total_msgids} untranslated entries")

        if dry_run:
            self.stdout.write("🔍 Dry run mode: skipping translation")
        else:
            self.stdout.write(f"🔄 Translating with {provider.name}...")

        # Now we have all the msgid -> translation mappings, we can proceed
        # with putting them into the .po files
        total_changed = 0
        for po_path in po_paths:
            self.stdout.write(self.style.NOTICE(f"\nProcessing: {po_path}"))
            po = polib.pofile(str(po_path), wrapwidth=79)
            changed = 0

            for entry in po:
                if entry.msgid in msgid_to_translation:
                    if dry_run:
                        self.stdout.write(f"✓ Would translate '{entry.msgid[:50]}'")
                    else:
                        self.stdout.write(f"✓ Translated '{entry.msgid[:50]}'")
                        if entry.msgid_plural:
                            singular = msgid_to_translation[entry.msgid]
                            plural = msgid_to_translation.get(
                                entry.msgid_plural, singular
                            )
                            for i in entry.msgstr_plural:
                                entry.msgstr_plural[i] = singular if i == 0 else plural
                        else:
                            entry.msgstr = msgid_to_translation[entry.msgid]
                        # Clear fuzzy flag since we have a fresh translation
                        if entry.fuzzy:
                            entry.flags.remove("fuzzy")
                    changed += 1

            if not dry_run and changed > 0:
                po.save(str(po_path))
                self.stdout.write(
                    self.style.SUCCESS(f"✨ Successfully updated {po_path}")
                )
            elif dry_run:
                self.stdout.write(
                    self.style.NOTICE(
                        f"Dry run: {changed} entries would be updated in {po_path}"
                    )
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

    def _translate_model_fields(
        self,
        target_lang,
        dry_run,
        overwrite,
        provider,
        model_names=None,
        context=None,
    ):
        """Translate django-modeltranslation model fields."""
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
            return

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

        # Batch the source texts using the provider's batching strategy
        source_texts = [item["source_text"] for item in items]
        text_batches = provider.batch(source_texts, target_lang)

        # Re-associate batched texts with their items
        groups = []
        item_idx = 0
        for text_batch in text_batches:
            batch_items = items[item_idx : item_idx + len(text_batch)]
            groups.append((text_batch, batch_items))
            item_idx += len(text_batch)

        # Translate all groups
        if dry_run:
            self.stdout.write("🔍 Dry run mode: skipping translation")
            translation_items = []
            for _texts_group, items_group in groups:
                for item in items_group:
                    translation_items.append(
                        {
                            "instance": item["instance"],
                            "target_field": item["target_field"],
                            "translation": "",
                        }
                    )
        else:
            batch_count = len(groups)
            self.stdout.write(
                f"🔄 Translating model fields with {provider.name} "
                f"({batch_count} batches)..."
            )

            translation_items = []
            with handle_api_errors():
                for texts_group, items_group in groups:
                    translations = provider.translate(
                        texts_group, target_lang, context=context
                    )

                    pairs = zip(items_group, translations, strict=True)
                    for item, translation in pairs:
                        translation_items.append(
                            {
                                "instance": item["instance"],
                                "target_field": item["target_field"],
                                "translation": translation,
                            }
                        )

                        model_name = item["model"].__name__
                        field_name = item["field"]
                        source_preview = item["source_text"][:50]
                        translation_preview = translation[:50]

                        self.stdout.write(
                            f"✓ {model_name}.{field_name}: "
                            f"'{source_preview}' → '{translation_preview}'"
                        )

        # Apply translations
        updated = backend.apply_translations(translation_items, dry_run=dry_run)

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
