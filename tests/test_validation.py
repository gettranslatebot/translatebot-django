"""
Tests for translation validation: placeholder checks mirroring
``msgfmt --check-format``, index-marker detection, and the retry flow in
the translate command.
"""

import json
import os
import shutil
import subprocess
from unittest.mock import MagicMock

import polib
import pytest

from django.core.management import call_command

from translatebot_django.management.commands import translate as translate_module
from translatebot_django.validation import translation_problem

PY = frozenset({"python-format"})
BRACE = frozenset({"python-brace-format"})


# --- translation_problem, cross-checked against msgfmt ---

LANGS = {
    "en": "nplurals=2; plural=(n != 1);",
    "fr": "nplurals=2; plural=(n > 1);",
    "ja": "nplurals=1; plural=0;",
    "ar": (
        "nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : "
        "n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5;"
    ),
}

# (description, language, flag, msgid, msgid_plural or None, msgstr(s), valid)
CASES = [
    # Seen with DeepSeek V4 Pro: a literal %% written as a lone % ...
    (
        "lone %",
        "en",
        PY,
        "TranslateBot is 100%% free.",
        None,
        "TranslateBot ist 100 % kostenlos.",
        False,
    ),
    ("lone % at end", "en", PY, "100%%", None, "100%", False),
    # ... or as "%i", which is a conversion of its own
    ("%% -> %i", "en", PY, "100%% test coverage", None, "100%ige Testabdeckung", False),
    ("%% kept", "en", PY, "100%% free", None, "100%% frei", True),
    ("named kept", "en", PY, "Hello %(name)s", None, "Hallo %(name)s", True),
    ("named reordered", "en", PY, "%(a)s and %(b)s", None, "%(b)s und %(a)s", True),
    ("named missing", "en", PY, "Hello %(name)s", None, "Hallo", False),
    ("named renamed", "en", PY, "Hello %(name)s", None, "Hallo %(naam)s", False),
    ("named width", "en", PY, "%(n)d file", None, "%(n)5d Datei", True),
    ("named d -> i", "en", PY, "%(n)d file", None, "%(n)i Datei", True),
    ("named d -> x", "en", PY, "%(n)d file", None, "%(n)x Datei", True),
    ("named d -> s", "en", PY, "%(n)d file", None, "%(n)s fichier", False),
    ("named d -> f", "en", PY, "%(n)d file", None, "%(n)f fichier", False),
    ("named f -> g", "en", PY, "%(n)f x", None, "%(n)g y", True),
    ("named s -> r", "en", PY, "%(n)s file", None, "%(n)r fichier", True),
    ("named c -> s", "en", PY, "%(n)c x", None, "%(n)s y", False),
    ("%F is invalid", "en", PY, "%(n)f x", None, "%(n)F y", False),
    ("unnamed kept", "en", PY, "%s of %d", None, "%s von %d", True),
    ("unnamed compatible", "en", PY, "%d of %s", None, "%i von %r", True),
    ("unnamed swapped", "en", PY, "%d of %s", None, "%s von %d", False),
    ("unnamed missing", "en", PY, "%s of %d", None, "%s", False),
    ("unnamed * width", "en", PY, "%*d x", None, "%*d y", True),
    ("mixed named and unnamed", "en", PY, "%(n)d and %s", None, "%(n)d und %s", True),
    ("unflagged % is text", "en", frozenset(), "100% free", None, "100 % frei", True),
    # Plural forms are compared with msgid_plural. Named placeholders may be
    # left out when the language has more than one form; unnamed may not.
    (
        "en plural omits named",
        "en",
        PY,
        "%(n)d file",
        "%(n)d files",
        ["one file", "%(n)d files"],
        True,
    ),
    (
        "en plural omits named everywhere",
        "en",
        PY,
        "%(n)d file",
        "%(n)d files",
        ["one file", "files"],
        True,
    ),
    (
        "fr form 0 (0 and 1) omits named",
        "fr",
        PY,
        "%(n)d file",
        "%(n)d files",
        ["un fichier", "%(n)d fichiers"],
        True,
    ),
    (
        "ar omits named",
        "ar",
        PY,
        "%(n)d file",
        "%(n)d files",
        ["لا ملفات", "ملف واحد", "ملفان", "%(n)d ملفات", "%(n)d ملفًا", "%(n)d ملف"],
        True,
    ),
    (
        "ja only form omits named",
        "ja",
        PY,
        "%(n)d file",
        "%(n)d files",
        ["ファイル"],
        False,
    ),
    (
        "ja only form keeps named",
        "ja",
        PY,
        "%(n)d file",
        "%(n)d files",
        ["%(n)d ファイル"],
        True,
    ),
    (
        "en plural omits unnamed",
        "en",
        PY,
        "%d file",
        "%d files",
        ["one file", "%d files"],
        False,
    ),
    (
        "fr plural omits unnamed",
        "fr",
        PY,
        "%d file",
        "%d files",
        ["un fichier", "%d fichiers"],
        False,
    ),
    (
        "plural adds named",
        "en",
        PY,
        "%(n)d file",
        "%(n)d files",
        ["%(n)d %(x)s", "%(n)d files"],
        False,
    ),
    (
        "plural changes type",
        "en",
        PY,
        "%(n)d file",
        "%(n)d files",
        ["%(n)s file", "%(n)d files"],
        False,
    ),
    (
        "plural form uses msgid-only name",
        "en",
        PY,
        "%(a)s file",
        "files",
        ["%(a)s Datei", "Dateien"],
        False,
    ),
    (
        "plural without placeholder in msgid",
        "en",
        PY,
        "One file",
        "%(n)d files",
        ["Eine Datei", "%(n)d Dateien"],
        True,
    ),
    # python-brace-format
    ("brace kept", "en", BRACE, "Hi {name}", None, "Hallo {name}", True),
    ("brace reordered", "en", BRACE, "{0} and {1}", None, "{1} et {0}", True),
    (
        "brace literal braces",
        "en",
        BRACE,
        "Use {{x}} and {y!r:>5}",
        None,
        "Gebruik {{x}} en {y!r:>5}",
        True,
    ),
    ("brace missing", "en", BRACE, "Hi {name}", None, "Salut", False),
    ("brace renamed", "en", BRACE, "Hi {name}", None, "Salut {nom}", False),
    ("brace auto kept", "en", BRACE, "{} and {}", None, "{} et {}", True),
    ("brace auto dropped", "en", BRACE, "{} and {}", None, "{} et", False),
    ("brace unterminated", "en", BRACE, "Hi {name}", None, "Salut {name", False),
    (
        "brace plural omits",
        "en",
        BRACE,
        "{n} file",
        "{n} files",
        ["one file", "{n} files"],
        True,
    ),
    (
        "brace plural omits auto",
        "en",
        BRACE,
        "{} file",
        "{} files",
        ["one file", "{} files"],
        True,
    ),
    ("brace ja omits", "ja", BRACE, "{n} file", "{n} files", ["ファイル"], False),
]


def _validator_accepts(lang, formats, msgid, msgid_plural, msgstr):
    if msgid_plural is None:
        return translation_problem(msgid, msgstr, formats) is None
    nplurals = int(LANGS[lang].split("nplurals=")[1].split(";")[0])
    return all(
        translation_problem(msgid_plural, form, formats, may_omit=nplurals > 1) is None
        for form in msgstr
    )


def _msgfmt_accepts(tmp_path, lang, formats, msgid, msgid_plural, msgstr):
    po = polib.POFile()
    po.metadata = {
        "Content-Type": "text/plain; charset=UTF-8",
        "Plural-Forms": LANGS[lang],
    }
    entry = polib.POEntry(msgid=msgid, flags=sorted(formats))
    if msgid_plural is None:
        entry.msgstr = msgstr
    else:
        entry.msgid_plural = msgid_plural
        entry.msgstr_plural = dict(enumerate(msgstr))
    po.append(entry)
    path = tmp_path / "case.po"
    po.save(str(path))
    result = subprocess.run(
        ["msgfmt", "--check-format", "-o", os.devnull, str(path)],
        capture_output=True,
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    ("lang", "formats", "msgid", "msgid_plural", "msgstr", "valid"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_translation_problem(lang, formats, msgid, msgid_plural, msgstr, valid):
    assert _validator_accepts(lang, formats, msgid, msgid_plural, msgstr) == valid


@pytest.mark.skipif(shutil.which("msgfmt") is None, reason="gettext not installed")
@pytest.mark.parametrize(
    ("lang", "formats", "msgid", "msgid_plural", "msgstr", "valid"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_cases_match_msgfmt(
    tmp_path, lang, formats, msgid, msgid_plural, msgstr, valid
):
    """Every case above is what msgfmt --check-format itself decides."""
    assert _msgfmt_accepts(tmp_path, lang, formats, msgid, msgid_plural, msgstr) == (
        valid
    )


def test_problem_descriptions():
    assert "lone '%'" in translation_problem("100%% free", "100 % kostenlos", PY)
    # "% f" is itself a conversion (space flag + f), as msgfmt sees it too
    assert "1 unnamed placeholders" in translation_problem(
        "100%% free", "100 % frei", PY
    )
    assert "missing placeholders: name" in translation_problem(
        "Hi %(name)s", "Hallo", PY
    )
    assert "not in the source: naam" in translation_problem(
        "Hi %(name)s", "Hallo %(naam)s", PY
    )
    assert "different type: n" in translation_problem("%(n)d", "%(n)s", PY)
    assert "1 unnamed placeholders, the source has 2" in translation_problem(
        "%s %d", "%s", PY
    )
    assert "different type or order" in translation_problem("%d %s", "%s %d", PY)
    assert "missing fields: {name}" in translation_problem("Hi {name}", "Hi", BRACE)
    assert "fields not in the source: {x}" in translation_problem(
        "Hi {name}", "Hi {name} {x}", BRACE
    )
    assert "invalid {field}" in translation_problem("Hi {name}", "Hi {name", BRACE)


def test_invalid_source_is_not_compared():
    """A source that isn't a valid format string itself can't be compared."""
    assert translation_problem("Save 50%", "50%% sparen", PY) is None
    assert translation_problem("Hi {name", "Hallo {name}", BRACE) is None


def test_index_marker_and_empty():
    # Seen with DeepSeek V4 Pro: all 125 Japanese entries came back as "#N"
    assert "index marker ('#16')" in translation_problem("Read more", "#16")
    assert translation_problem("#1", "#1") is None
    assert translation_problem("Hello", "  ") == "empty"
    assert translation_problem("Hello", 3) == "not a string"


def test_po_unit_problem():
    # Looked up at call time: test_translate_command reloads the module,
    # which replaces these classes
    POUnit = translate_module.POUnit
    plain = POUnit(None, "Hello %(name)s", formats=PY)
    assert plain.problem(["Hallo %(name)s"]) is None
    assert "missing placeholders" in plain.problem(["Hallo"])

    plural = POUnit(
        None,
        "%(n)d file",
        "%(n)d files",
        formats=PY,
        nplurals=3,
        plural_forms=("1", "2, 3, 4", "0, 5, 6"),
    )
    assert plural.problem([["plik", "%(n)d pliki", "%(n)d plików"]]) is None
    assert plural.problem(["not a list"]) == "not a list of plural forms"
    assert "(plural form 1)" in plural.problem([["plik", "100 %", "x"]])

    # The singular goes to plain entries of the same message: strict there
    plural.has_plain = True
    assert "(singular, for a non-plural entry)" in plural.problem(
        [["plik", "%(n)d pliki", "%(n)d plików"]]
    )
    assert plural.problem([["%(n)d plik", "%(n)d pliki", "%(n)d plików"]]) is None


def test_two_string_mode_single_form_language_gets_the_plural():
    """ja/zh have one form for every count; it must keep the placeholder."""
    unit = translate_module.POUnit(
        None, "One file", "%(n)d files", formats=PY, nplurals=1
    )
    forms = unit.translation_from(["ファイル1つ", "%(n)d ファイル"])
    assert list(forms) == ["%(n)d ファイル"]
    assert forms.singular == "ファイル1つ"
    assert unit.problem(["ファイル1つ", "%(n)d ファイル"]) is None


def test_absorb_tracks_plain_duplicates():
    POUnit = translate_module.POUnit
    plain_first = POUnit(None, "Item")
    plain_first.absorb(POUnit(None, "Item", "Items", formats=PY))
    assert plain_first.has_plain and plain_first.formats == PY

    plural_first = POUnit(None, "Item", "Items")
    plural_first.absorb(POUnit(None, "Item"))
    assert plural_first.has_plain

    only_plural = POUnit(None, "Item", "Items")
    only_plural.absorb(POUnit(None, "Item", "Items"))
    assert not only_plural.has_plain


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
