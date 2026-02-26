"""
Tests for the check_translations management command.
"""

from io import StringIO
from unittest.mock import MagicMock

import polib
import pytest

from django.core.management import call_command
from django.core.management.base import CommandError

from translatebot_django.utils import get_all_po_files


def _create_po_file(path, entries):
    """Helper to create a .po file with given entries.

    Each entry is a dict with msgid, msgstr, and optional fuzzy flag.
    """
    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    for entry_data in entries:
        entry = polib.POEntry(
            msgid=entry_data["msgid"],
            msgstr=entry_data.get("msgstr", ""),
        )
        if entry_data.get("fuzzy"):
            entry.flags.append("fuzzy")
        po.append(entry)
    po.save(str(path))


class TestCheckTranslationsCommand:
    def test_all_translations_complete(self, tmp_path, settings, mocker):
        """Test success when all translations are complete."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        _create_po_file(
            nl_dir / "django.po",
            [{"msgid": "Hello", "msgstr": "Hallo"}],
        )

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        stdout = StringIO()
        call_command("check_translations", stdout=stdout)

        output = stdout.getvalue()
        assert "OK" in output
        assert "All translations complete" in output

    def test_djangojs_po_checked(self, tmp_path, settings, mocker):
        """Test that djangojs.po files are also checked."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        _create_po_file(
            nl_dir / "django.po",
            [{"msgid": "Hello", "msgstr": "Hallo"}],
        )
        _create_po_file(
            nl_dir / "djangojs.po",
            [{"msgid": "Click here", "msgstr": ""}],
        )

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        stderr = StringIO()
        with pytest.raises(CommandError, match="Translation check failed"):
            call_command("check_translations", stderr=stderr)

        output = stderr.getvalue()
        assert "1 untranslated" in output

    def test_djangojs_po_complete(self, tmp_path, settings, mocker):
        """Test success when both django.po and djangojs.po are complete."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        _create_po_file(
            nl_dir / "django.po",
            [{"msgid": "Hello", "msgstr": "Hallo"}],
        )
        _create_po_file(
            nl_dir / "djangojs.po",
            [{"msgid": "Click here", "msgstr": "Klik hier"}],
        )

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        stdout = StringIO()
        call_command("check_translations", stdout=stdout)

        output = stdout.getvalue()
        assert output.count("OK") == 2
        assert "All translations complete" in output

    def test_untranslated_strings(self, tmp_path, settings, mocker):
        """Test failure when there are untranslated strings."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        _create_po_file(
            nl_dir / "django.po",
            [
                {"msgid": "Hello", "msgstr": ""},
                {"msgid": "World", "msgstr": ""},
                {"msgid": "Done", "msgstr": "Klaar"},
            ],
        )

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        stderr = StringIO()
        with pytest.raises(CommandError, match="Translation check failed"):
            call_command("check_translations", stderr=stderr)

        output = stderr.getvalue()
        assert "2 untranslated" in output

    def test_fuzzy_strings(self, tmp_path, settings, mocker):
        """Test failure when there are fuzzy strings."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        _create_po_file(
            nl_dir / "django.po",
            [
                {"msgid": "Hello", "msgstr": "Hallo", "fuzzy": True},
                {"msgid": "World", "msgstr": "Wereld"},
            ],
        )

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        stderr = StringIO()
        with pytest.raises(CommandError, match="Translation check failed"):
            call_command("check_translations", stderr=stderr)

        output = stderr.getvalue()
        assert "1 fuzzy" in output

    def test_mixed_issues(self, tmp_path, settings, mocker):
        """Test reporting both untranslated and fuzzy strings."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        _create_po_file(
            nl_dir / "django.po",
            [
                {"msgid": "Hello", "msgstr": ""},
                {"msgid": "World", "msgstr": ""},
                {"msgid": "Fuzzy one", "msgstr": "Vaag", "fuzzy": True},
            ],
        )

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        stderr = StringIO()
        with pytest.raises(CommandError, match="Translation check failed"):
            call_command("check_translations", stderr=stderr)

        output = stderr.getvalue()
        assert "2 untranslated" in output
        assert "1 fuzzy" in output

    def test_no_po_files_found(self, tmp_path, settings, mocker):
        """Test warning when no translation files are found."""
        settings.LOCALE_PATHS = [str(tmp_path / "empty_locale")]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        stdout = StringIO()
        call_command("check_translations", stdout=stdout)

        output = stdout.getvalue()
        assert "No translation files found" in output

    def test_multiple_locales(self, tmp_path, settings, mocker):
        """Test checking all locale directories."""
        locale_dir = tmp_path / "locale"
        for lang in ["de", "fr", "nl"]:
            lang_dir = locale_dir / lang / "LC_MESSAGES"
            lang_dir.mkdir(parents=True)
            _create_po_file(
                lang_dir / "django.po",
                [{"msgid": "Hello", "msgstr": f"Hello in {lang}"}],
            )

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        stdout = StringIO()
        call_command("check_translations", stdout=stdout)

        output = stdout.getvalue()
        assert output.count("OK") == 3
        assert "All translations complete" in output

    def test_multiple_locales_with_one_failing(self, tmp_path, settings, mocker):
        """Test that one failing locale causes overall failure."""
        locale_dir = tmp_path / "locale"

        # nl is complete
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        _create_po_file(
            nl_dir / "django.po",
            [{"msgid": "Hello", "msgstr": "Hallo"}],
        )

        # de has untranslated entries
        de_dir = locale_dir / "de" / "LC_MESSAGES"
        de_dir.mkdir(parents=True)
        _create_po_file(
            de_dir / "django.po",
            [{"msgid": "Hello", "msgstr": ""}],
        )

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        stderr = StringIO()
        stdout = StringIO()
        with pytest.raises(CommandError, match="Translation check failed"):
            call_command("check_translations", stdout=stdout, stderr=stderr)

        # nl should still show OK
        assert "OK" in stdout.getvalue()
        # de should show errors
        assert "1 untranslated" in stderr.getvalue()

    def test_finds_po_files_in_app_locale_dirs(self, tmp_path, settings, mocker):
        """Test that command finds .po files in app locale directories."""
        app_path = tmp_path / "myapp"
        locale_dir = app_path / "locale" / "nl" / "LC_MESSAGES"
        locale_dir.mkdir(parents=True)
        _create_po_file(
            locale_dir / "django.po",
            [{"msgid": "Hello", "msgstr": "Hallo"}],
        )

        settings.LOCALE_PATHS = []

        mock_app = MagicMock()
        mock_app.path = str(app_path)
        mocker.patch("django.apps.apps.get_app_configs", return_value=[mock_app])

        stdout = StringIO()
        call_command("check_translations", stdout=stdout)

        assert "OK" in stdout.getvalue()

    def test_makemessages_flag(self, tmp_path, settings, mocker):
        """Test that --makemessages runs makemessages before checking."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        _create_po_file(
            nl_dir / "django.po",
            [{"msgid": "Hello", "msgstr": "Hallo"}],
        )

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        mock_call_command = mocker.patch(
            "translatebot_django.management.commands.check_translations.call_command"
        )

        stdout = StringIO()
        # Call handle() directly since we're mocking call_command
        from translatebot_django.management.commands.check_translations import Command

        cmd = Command()
        cmd.stdout = stdout
        cmd.stderr = StringIO()
        cmd.handle(makemessages=True)

        mock_call_command.assert_called_once_with(
            "makemessages", all=True, no_obsolete=True
        )

    def test_makemessages_flag_not_called_by_default(self, tmp_path, settings, mocker):
        """Test that makemessages is not run when flag is not set."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        _create_po_file(
            nl_dir / "django.po",
            [{"msgid": "Hello", "msgstr": "Hallo"}],
        )

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        mock_call_command = mocker.patch(
            "translatebot_django.management.commands.check_translations.call_command"
        )

        stdout = StringIO()
        from translatebot_django.management.commands.check_translations import Command

        cmd = Command()
        cmd.stdout = stdout
        cmd.stderr = StringIO()
        cmd.handle(makemessages=False)

        mock_call_command.assert_not_called()


class TestGetAllPoFiles:
    def test_finds_files_in_locale_paths(self, tmp_path, settings, mocker):
        """Test finding .po files in LOCALE_PATHS."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        po_path = nl_dir / "django.po"
        po_path.touch()

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        files = get_all_po_files()
        assert len(files) == 1
        assert po_path.resolve() in files

    def test_finds_files_in_app_locale_dirs(self, tmp_path, settings, mocker):
        """Test finding .po files in app locale directories."""
        app_path = tmp_path / "myapp"
        locale_dir = app_path / "locale" / "nl" / "LC_MESSAGES"
        locale_dir.mkdir(parents=True)
        po_path = locale_dir / "django.po"
        po_path.touch()

        settings.LOCALE_PATHS = []

        mock_app = MagicMock()
        mock_app.path = str(app_path)
        mocker.patch("django.apps.apps.get_app_configs", return_value=[mock_app])

        files = get_all_po_files()
        assert po_path.resolve() in files

    def test_finds_files_in_default_locale(
        self, tmp_path, settings, mocker, monkeypatch
    ):
        """Test finding .po files in default locale/ directory."""
        monkeypatch.chdir(tmp_path)

        locale_dir = tmp_path / "locale" / "nl" / "LC_MESSAGES"
        locale_dir.mkdir(parents=True)
        po_path = locale_dir / "django.po"
        po_path.touch()

        settings.LOCALE_PATHS = []
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        files = get_all_po_files()
        assert po_path.resolve() in files

    def test_returns_empty_when_no_files(self, tmp_path, settings, mocker):
        """Test returns empty list when no .po files exist."""
        settings.LOCALE_PATHS = [str(tmp_path / "nonexistent")]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        files = get_all_po_files()
        assert files == []

    def test_deduplicates_results(self, tmp_path, settings, mocker):
        """Test that duplicate paths are removed."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        po_path = nl_dir / "django.po"
        po_path.touch()

        # Point two LOCALE_PATHS at the same directory
        settings.LOCALE_PATHS = [str(locale_dir), str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        files = get_all_po_files()
        assert files.count(po_path.resolve()) == 1

    def test_finds_multiple_languages(self, tmp_path, settings, mocker):
        """Test finding .po files across multiple language directories."""
        locale_dir = tmp_path / "locale"
        for lang in ["nl", "de", "fr"]:
            lang_dir = locale_dir / lang / "LC_MESSAGES"
            lang_dir.mkdir(parents=True)
            (lang_dir / "django.po").touch()

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        files = get_all_po_files()
        assert len(files) == 3

    def test_returns_sorted_results(self, tmp_path, settings, mocker):
        """Test that results are sorted."""
        locale_dir = tmp_path / "locale"
        for lang in ["nl", "de", "fr"]:
            lang_dir = locale_dir / lang / "LC_MESSAGES"
            lang_dir.mkdir(parents=True)
            (lang_dir / "django.po").touch()

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        files = get_all_po_files()
        assert files == sorted(files)

    def test_finds_djangojs_po_files(self, tmp_path, settings, mocker):
        """Test that get_all_po_files finds djangojs.po files."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        django_po = nl_dir / "django.po"
        django_po.touch()
        djangojs_po = nl_dir / "djangojs.po"
        djangojs_po.touch()

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        files = get_all_po_files()
        assert len(files) == 2
        assert django_po.resolve() in files
        assert djangojs_po.resolve() in files

    def test_finds_only_djangojs_po(self, tmp_path, settings, mocker):
        """Test that get_all_po_files finds djangojs.po even without django.po."""
        locale_dir = tmp_path / "locale"
        nl_dir = locale_dir / "nl" / "LC_MESSAGES"
        nl_dir.mkdir(parents=True)
        djangojs_po = nl_dir / "djangojs.po"
        djangojs_po.touch()

        settings.LOCALE_PATHS = [str(locale_dir)]
        mocker.patch("django.apps.apps.get_app_configs", return_value=[])

        files = get_all_po_files()
        assert len(files) == 1
        assert djangojs_po.resolve() in files
