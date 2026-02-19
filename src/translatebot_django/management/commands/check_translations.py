import polib  # type: ignore[import-untyped]

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from translatebot_django.utils import get_all_po_files


class Command(BaseCommand):
    help = "Check for missing or fuzzy translations in .po files"

    def add_arguments(self, parser):
        parser.add_argument(
            "--makemessages",
            action="store_true",
            help="Run makemessages -a --no-obsolete before checking. "
            "Requires gettext to be installed.",
        )

    def handle(self, *args, **options):
        if options["makemessages"]:
            self.stdout.write("Running makemessages -a --no-obsolete...")
            call_command("makemessages", all=True, no_obsolete=True)

        po_files = get_all_po_files()

        if not po_files:
            self.stdout.write(self.style.WARNING("No translation files found."))
            return

        has_issues = False
        for po_path in po_files:
            po = polib.pofile(str(po_path))
            untranslated = len(po.untranslated_entries())
            fuzzy = len(po.fuzzy_entries())

            if untranslated or fuzzy:
                self.stderr.write(
                    self.style.ERROR(
                        f"{po_path}: {untranslated} untranslated, {fuzzy} fuzzy"
                    )
                )
                has_issues = True
            else:
                self.stdout.write(self.style.SUCCESS(f"{po_path}: OK"))

        if has_issues:
            raise CommandError("Translation check failed")

        self.stdout.write(self.style.SUCCESS("All translations complete."))
