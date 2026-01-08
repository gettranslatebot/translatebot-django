import json

import polib
import tiktoken
from litellm import completion, get_max_tokens
from litellm.exceptions import AuthenticationError

from django.core.management.base import BaseCommand, CommandError

from translatebot_django.utils import (
    get_all_po_paths,
    get_api_key,
    get_model,
    is_modeltranslation_available,
)


def get_token_count(text):
    """Get the token count for a given text and model."""
    encoding = tiktoken.get_encoding("cl100k_base")
    tokens = encoding.encode(text)
    return len(tokens)


SYSTEM_PROMPT = (
    "You are a professional software localization translator.\n"
    "Important rules:\n"
    "- The input format is JSON. The output format must be JSON as well.\n"
    "- Preserve all placeholders like %(name)s, {name}, {0}, %s.\n"
    "- Preserve HTML tags exactly as they are.\n"
    "- Preserve line breaks (\\n) in the text.\n"
    "- Do not change the order of the strings.\n"
    "- Return ONLY the translated strings as a JSON array.\n"
    "- Do NOT wrap the JSON in markdown code blocks (```json). Return raw JSON only."
)
SYSTEM_PROMPT_LENGTH = get_token_count(SYSTEM_PROMPT)


def create_preaamble(target_lang):
    return (
        f"Translate the following array of strings to the language {target_lang}"
        " and return ONLY a JSON array:\n"
    )


def translate_text(text, target_lang, model, api_key):
    """Translate text by calling LiteLLM."""
    # Preserve leading/trailing newlines for proper .po file formatting
    preamble = create_preaamble(target_lang)
    response = completion(
        model=model,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": preamble + json.dumps(text, ensure_ascii=False),
            },
        ],
        temperature=0.2,  # Low randomness for consistency
        api_key=api_key,
    )

    content = response.choices[0].message.content
    if content is None:
        raise ValueError(
            f"API returned empty response. Model: {model}, Response: {response}"
        )

    content = content.strip()
    if not content:
        raise ValueError(f"API returned empty content after stripping. Model: {model}")

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


def gather_strings(po_path, only_empty=True):
    po = polib.pofile(str(po_path), wrapwidth=79)
    ret = []

    for entry in po:
        if not entry.msgid or entry.obsolete:
            continue

        if entry.msgstr and not only_empty:
            continue

        ret.append(entry.msgid)
    return ret


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

        # If --models flag is used, check if modeltranslation is available
        if translate_models and not is_modeltranslation_available():
            raise CommandError(
                "django-modeltranslation is not installed or not in "
                "INSTALLED_APPS.\n"
                "Install it with: pip install django-modeltranslation\n"
                "See: https://github.com/deschler/django-modeltranslation"
            )

        model = get_model()
        api_key = get_api_key()

        # Process each target language
        for lang in target_langs:
            if len(target_langs) > 1:
                self.stdout.write("\n" + "=" * 60)
                self.stdout.write(f"🌍 Processing language: {lang}")
                self.stdout.write("=" * 60)

            # Handle .po file translation (existing logic)
            if translate_po:
                self._translate_po_files(lang, dry_run, overwrite, model, api_key)

            # Handle model field translation (NEW)
            if translate_models:
                self._translate_model_fields(
                    target_lang=lang,
                    dry_run=dry_run,
                    overwrite=overwrite,
                    model=model,
                    api_key=api_key,
                    model_names=models_arg,
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

    def _translate_po_files(self, target_lang, dry_run, overwrite, model, api_key):
        """Translate .po files (existing logic refactored into method)."""
        # Find all .po files for the target language
        po_paths = get_all_po_paths(target_lang)

        all_msgids = []
        for po_path in po_paths:
            all_msgids.extend(gather_strings(po_path, only_empty=overwrite))

        # Early return with minimal output if nothing to translate
        if len(all_msgids) == 0:
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

        groups = []
        group_candidate = []
        for item in all_msgids:
            group_candidate += [item]

            total = get_token_count(json.dumps(group_candidate, ensure_ascii=False))
            output_tokens_estimate = total * 1.3
            preamble_length = get_token_count(create_preaamble(target_lang))

            if total + preamble_length + output_tokens_estimate > get_max_tokens(model):
                groups.append(group_candidate)
                group_candidate = []

        if group_candidate:
            groups.append(group_candidate)

        self.stdout.write(f"ℹ️  Found {len(all_msgids)} untranslated entries")

        if dry_run:
            self.stdout.write("🔍 Dry run mode: skipping LLM translation")
        else:
            self.stdout.write(f"🔄 Translating with {model}...")

        msgid_to_translation = {}
        if dry_run:
            # In dry run, just map all msgids to empty strings for counting
            for group in groups:
                for msgid in group:
                    msgid_to_translation[msgid] = ""
        else:
            try:
                for group in groups:
                    translated = translate_text(
                        text=group,
                        target_lang=target_lang,
                        api_key=api_key,
                        model=model,
                    )
                    for msgid, translation in zip(group, translated, strict=True):
                        msgid_to_translation[msgid] = translation
            except AuthenticationError as e:
                raise CommandError(
                    f"Authentication failed: {str(e)}\n"
                    "Please check your API key configuration.\n"
                    "Set TRANSLATEBOT_API_KEY in settings or "
                    "TRANSLATEBOT_API_KEY environment variable."
                ) from e

        # Now we have all the msgid -> translation mappings, we can proceed
        # with putting them into the .po files
        total_changed = 0
        for po_path in po_paths:
            self.stdout.write(self.style.NOTICE(f"\nProcessing: {po_path}"))
            po = polib.pofile(str(po_path), wrapwidth=79)
            changed = 0

            for entry in po:
                if entry.msgid in msgid_to_translation:
                    translation = msgid_to_translation[entry.msgid]
                    if dry_run:
                        self.stdout.write(f"✓ Would translate '{entry.msgid[:50]}'")
                    else:
                        self.stdout.write(f"✓ Translated '{entry.msgid[:50]}'")
                        entry.msgstr = translation
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
        model,
        api_key,
        model_names=None,
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

        # Group items by token limits (same strategy as PO files)
        groups = []
        group_candidate = []
        group_items = []

        for item in items:
            group_candidate.append(item["source_text"])
            group_items.append(item)

            total = get_token_count(json.dumps(group_candidate, ensure_ascii=False))
            output_tokens_estimate = total * 1.3
            preamble_length = get_token_count(create_preaamble(target_lang))

            if total + preamble_length + output_tokens_estimate > get_max_tokens(model):
                # Group is full, save it and start a new one
                groups.append((group_candidate[:-1], group_items[:-1]))
                group_candidate = [item["source_text"]]
                group_items = [item]

        # Add the last group if it has content
        if group_candidate:
            groups.append((group_candidate, group_items))

        # Translate all groups
        if dry_run:
            self.stdout.write("🔍 Dry run mode: skipping LLM translation")
            # In dry run, create placeholder translation items for counting
            translation_items = []
            for _texts_group, items_group in groups:
                for item in items_group:
                    translation_items.append(
                        {
                            "instance": item["instance"],
                            "target_field": item["target_field"],
                            "translation": "",  # Placeholder for dry run
                        }
                    )
        else:
            batch_count = len(groups)
            self.stdout.write(
                f"🔄 Translating model fields with {model} ({batch_count} batches)..."
            )

            translation_items = []
            try:
                for texts_group, items_group in groups:
                    translations = translate_text(
                        texts_group, target_lang, model, api_key
                    )

                    # Prepare translation items for this group
                    pairs = zip(items_group, translations, strict=True)
                    for item, translation in pairs:
                        translation_items.append(
                            {
                                "instance": item["instance"],
                                "target_field": item["target_field"],
                                "translation": translation,
                            }
                        )

                        # Show sample translations
                        model_name = item["model"].__name__
                        field_name = item["field"]
                        source_preview = item["source_text"][:50]
                        translation_preview = translation[:50]

                        self.stdout.write(
                            f"✓ {model_name}.{field_name}: "
                            f"'{source_preview}' → '{translation_preview}'"
                        )
            except AuthenticationError as e:
                raise CommandError(
                    f"Authentication failed: {str(e)}\n"
                    "Please check your API key configuration.\n"
                    "Set TRANSLATEBOT_API_KEY in settings or "
                    "TRANSLATEBOT_API_KEY environment variable."
                ) from e

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
