"""
Tests for translation validation: placeholder checks mirroring
``msgfmt --check-format``, index-marker detection, and the retry flow in
the translate command.
"""

import json
from unittest.mock import MagicMock

import polib
import pytest

from django.core.management import call_command

from translatebot_django.management.commands import translate as translate_module
from translatebot_django.validation import translation_problem

PY = frozenset({"python-format"})
BRACE = frozenset({"python-brace-format"})


# --- translation_problem ---


@pytest.mark.parametrize(
    ("source", "translation"),
    [
        ("TranslateBot is 100%% free.", "TranslateBot ist 100%% kostenlos."),
        ("Hello %(name)s", "Hallo %(name)s"),
        ("%(a)s and %(b)s", "%(b)s und %(a)s"),
        ("%s of %d", "%s von %d"),
        ("Width: %-5.2f%%", "Breite: %-5.2f%%"),
    ],
)
def test_python_format_ok(source, translation):
    assert translation_problem((source,), translation, PY) is None


@pytest.mark.parametrize(
    ("source", "translation", "expected"),
    [
        # Seen with DeepSeek V4 Pro: a literal %% written as a lone %
        ("TranslateBot is 100%% free.", "TranslateBot ist 100 % kostenlos.", "lone"),
        # ... or as "%i", which is a conversion of its own
        ("100%% test coverage", "100%ige Testabdeckung", "1 unnamed"),
        ("Hello %(name)s", "Hallo", "missing placeholders: name"),
        ("Hello %(name)s", "Hallo %(naam)s", "not in the source: naam"),
        ("%s of %d", "%s", "1 unnamed placeholders, the source has 2"),
    ],
)
def test_python_format_problems(source, translation, expected):
    assert expected in translation_problem((source,), translation, PY)


def test_unflagged_percent_is_plain_text():
    assert translation_problem(("100% free",), "100 % kostenlos") is None


def test_plural_form_may_omit_placeholders():
    """Arabic writes "one file" / "two files" without the count."""
    sources = ("%(count)d file", "%(count)d files")
    assert translation_problem(sources, "ملف واحد", PY, plural=True) is None
    assert "not in the source" in translation_problem(
        sources, "%(n)d ملف", PY, plural=True
    )
    assert "2 unnamed" in translation_problem(
        ("%d file", "%d files"), "%d %d", PY, plural=True
    )
    assert "lone" in translation_problem(sources, "100 % ملف", PY, plural=True)


@pytest.mark.parametrize(
    ("source", "translation", "expected"),
    [
        ("Hi {name}", "Hallo {name}", None),
        ("{0} of {1}", "{1} van {0}", None),
        ("Use {{braces}} and {x!r:>5}", "Gebruik {{accolades}} en {x!r:>5}", None),
        ("Hi {name}", "Hallo", "missing fields: {name}"),
        ("Hi {name}", "Hallo {naam}", "fields not in the source: {naam}"),
    ],
)
def test_brace_format(source, translation, expected):
    problem = translation_problem((source,), translation, BRACE)
    assert problem == expected if expected is None else expected in problem


def test_brace_format_plural_may_omit_fields():
    sources = ("{count} file", "{count} files")
    assert translation_problem(sources, "ملف واحد", BRACE, plural=True) is None


def test_index_marker_and_empty():
    # Seen with DeepSeek V4 Pro: all 125 Japanese entries came back as "#N"
    assert "index marker ('#16')" in translation_problem(("Read more",), "#16")
    assert translation_problem(("#1",), "#1") is None
    assert translation_problem(("Hello",), "  ") == "empty"
    assert translation_problem(("Hello",), 3) == "not a string"


def test_po_unit_problem_for_plural_text_and_two_strings():
    # Looked up at call time: test_translate_command reloads the module,
    # which replaces these classes
    POUnit, PluralText = translate_module.POUnit, translate_module.PluralText
    unit = POUnit(None, "%(n)d file", "%(n)d files", formats=PY, nplurals=3)
    plural = PluralText("%(n)d file", "%(n)d files", ("1", "2", "5"))
    assert unit.problem(plural, ["%(n)d plik", "%(n)d pliki", "%(n)d plików"]) is None
    assert "lone" in unit.problem(plural, ["%(n)d plik", "100 %", "x"])
    assert unit.problem(plural, "not a list") == "not a list of plural forms"
    # Two-string fallback: each string is checked as a plural form
    assert unit.problem("%(n)d file", "plik") is None


# --- translate command ---


def _write_po(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=UTF-8"}
    for entry in entries:
        po.append(entry)
    po.save(str(path))
    return path


def _responses(mocker, *payloads):
    responses = []
    for payload in payloads:
        response = MagicMock()
        response.choices[0].message.content = (
            payload if isinstance(payload, str) else json.dumps(payload)
        )
        responses.append(response)
    return mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        side_effect=responses,
    )


def _sent(mock, call):
    content = mock.call_args_list[call].kwargs["messages"][1]["content"]
    payload = json.loads(content[content.find("[") :])
    return [item if isinstance(item, str) else item["text"] for item in payload]


ENTRIES = [
    polib.POEntry(msgid="Read more", msgstr=""),
    polib.POEntry(msgid="100%% free", msgstr="", flags=["python-format"]),
    polib.POEntry(msgid="Hello %(name)s", msgstr="", flags=["python-format"]),
]


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_failing_translation_is_retried_alone(temp_locale_dir, mocker):
    po_path = _write_po(temp_locale_dir / "de" / "LC_MESSAGES" / "django.po", ENTRIES)
    mock = _responses(
        mocker,
        ["Weiterlesen", "100 % kostenlos", "Hallo %(name)s"],
        ["100%% kostenlos"],
    )

    call_command("translate", target_lang="de")

    assert _sent(mock, 1) == ["100%% free"]
    assert [e.msgstr for e in polib.pofile(str(po_path))] == [
        "Weiterlesen",
        "100%% kostenlos",
        "Hallo %(name)s",
    ]


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_index_markers_are_retried(temp_locale_dir, mocker):
    po_path = _write_po(temp_locale_dir / "ja" / "LC_MESSAGES" / "django.po", ENTRIES)
    mock = _responses(
        mocker,
        ["#1", "#2", "#3"],
        ["続きを読む", "100%% 無料", "こんにちは %(name)s"],
    )

    call_command("translate", target_lang="ja")

    assert _sent(mock, 1) == ["Read more", "100%% free", "Hello %(name)s"]
    assert [e.msgstr for e in polib.pofile(str(po_path))] == [
        "続きを読む",
        "100%% 無料",
        "こんにちは %(name)s",
    ]


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_still_failing_translation_is_left_untranslated(temp_locale_dir, mocker):
    po_path = _write_po(temp_locale_dir / "de" / "LC_MESSAGES" / "django.po", ENTRIES)
    _responses(
        mocker,
        ["Weiterlesen", "100 % kostenlos", "Hallo"],
        ["100 % kostenlos", "Hallo"],
    )
    from io import StringIO

    from translatebot_django import translate

    out = StringIO()
    mocker.patch(
        "translatebot_django.api.call_command",
        side_effect=lambda cmd, **kw: call_command(cmd, stdout=out, **kw),
    )
    result = translate(target_langs="de")

    assert [e.msgstr for e in polib.pofile(str(po_path))] == ["Weiterlesen", "", ""]
    assert result.strings_found == 3
    assert result.strings_translated == 1
    output = out.getvalue()
    assert "Left '100%% free' untranslated" in output
    assert "a lone '%'" in output
    assert "missing placeholders: name" in output


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_invalid_retry_response_leaves_entries_untranslated(temp_locale_dir, mocker):
    po_path = _write_po(temp_locale_dir / "de" / "LC_MESSAGES" / "django.po", ENTRIES)
    _responses(
        mocker,
        ["Weiterlesen", "100 % kostenlos", "Hallo %(name)s"],
        ["one", "too many"],
    )

    call_command("translate", target_lang="de")

    assert [e.msgstr for e in polib.pofile(str(po_path))] == [
        "Weiterlesen",
        "",
        "Hallo %(name)s",
    ]


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_invalid_batch_response_is_retried_once(temp_locale_dir, mocker):
    po_path = _write_po(temp_locale_dir / "nl" / "LC_MESSAGES" / "django.po", ENTRIES)
    mock = _responses(
        mocker,
        ["only one"],
        ["Lees meer", "100%% gratis", "Hallo %(name)s"],
    )

    call_command("translate", target_lang="nl")

    assert mock.call_count == 2
    assert polib.pofile(str(po_path))[1].msgstr == "100%% gratis"


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_invalid_batch_response_twice_stops_the_run(temp_locale_dir, mocker):
    from django.core.management.base import CommandError

    _write_po(temp_locale_dir / "nl" / "LC_MESSAGES" / "django.po", ENTRIES)
    mock = _responses(mocker, ["only one"], "not json")

    with pytest.raises(CommandError, match="Failed to parse JSON"):
        call_command("translate", target_lang="nl")
    assert mock.call_count == 2
