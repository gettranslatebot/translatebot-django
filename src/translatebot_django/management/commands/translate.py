import json

import polib
import tiktoken
from litellm import completion, get_max_tokens

from django.core.management.base import BaseCommand

from translatebot_django.utils import get_all_po_paths, get_api_key, get_model


def get_token_count(text):
    """Get the token count for a given text and model."""
    encoding = tiktoken.get_encoding("cl100k_base")
    tokens = encoding.encode(text)
    return len(tokens)


SYSTEM_PROMPT = (
    "You are a professional software localization translator.\n"
    "Important rules:\n"
    "- The input format is JSON. The output format must be JSON as well."
    "- Preserve all placeholders like %(name)s, {name}, {0}, %s.\n"
    "- Preserve HTML tags exactly as they are.\n"
    "- Preserve line breaks (\\n) in the text.\n"
    "- Do not change the order of the strings.\n"
    "- Return ONLY the translated strings as a JSON array."
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

    translated = json.loads(response.choices[0].message.content.strip())
    return translated


def gather_strings(po_path, only_empty=True):
    po = polib.pofile(str(po_path))
    ret = []

    for entry in po:
        if not entry.msgid or entry.obsolete:
            continue

        if entry.msgstr and not only_empty:
            continue

        ret.append(entry.msgid)
    return ret


class Command(BaseCommand):
    help = "Automatically translate .po files using AI"

    def add_arguments(self, parser):
        parser.add_argument(
            "--target-lang",
            required=True,
            help="Target language code, e.g. de, fr, nl",
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

    def handle(self, *args, **options):
        target_lang = options["target_lang"]
        dry_run = options["dry_run"]
        overwrite = options["overwrite"]

        model = get_model()
        api_key = get_api_key()

        # Find all .po files for the target language
        po_paths = get_all_po_paths(target_lang)

        all_msgids = []
        for po_path in po_paths:
            all_msgids.extend(gather_strings(po_path, only_empty=overwrite))

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
        msgid_to_translation = {}
        for group in groups:
            translated = translate_text(
                text=group, target_lang=target_lang, api_key=api_key, model=model
            )
            for msgid, translation in zip(group, translated):
                msgid_to_translation[msgid] = translation

        # Now we have all the msgid -> translation mappings, we can proceed
        # with putting them into the .po files
        total_changed = 0
        for po_path in po_paths:
            self.stdout.write(self.style.NOTICE(f"\nProcessing: {po_path}"))
            po = polib.pofile(str(po_path), wrapwidth=77)
            changed = 0

            for entry in po:
                if entry.msgid in msgid_to_translation:
                    translation = msgid_to_translation[entry.msgid]
                    self.stdout.write(f"  {entry.msgid[:50]}...")
                    self.stdout.write(self.style.SUCCESS(f"  → {translation[:50]}..."))
                    if not dry_run:
                        entry.msgstr = translation
                        changed += 1

            if not dry_run and changed > 0:
                po.save(str(po_path))
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Saved {changed} updated entries to {po_path}"
                    )
                )
            else:
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
                    f"✓ Successfully translated {total_changed} entries "
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
