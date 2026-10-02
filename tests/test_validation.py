"""
Tests for translation validation: placeholder checks mirroring
``msgfmt --check-format``, index-marker detection, and the retry flow in
the translate command.
"""

import json
import os
import re
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
    # Second review round: cases the first parser got wrong
    ("brace attr dropped", "en", BRACE, "{a.b} {c}", None, "{a} {c}", False),
    ("brace attr added", "en", BRACE, "{a} {c}", None, "{a.b} {c}", False),
    ("brace index changed", "en", BRACE, "{a[0]} {c}", None, "{a[1]} {c}", False),
    ("brace attr changed", "en", BRACE, "{a.b} {c}", None, "{a.c} {c}", False),
    ("brace attr kept", "en", BRACE, "{a.b} {c[0]}", None, "{c[0]} {a.b}", True),
    ("brace !r in translation", "en", BRACE, "{a}", None, "{a!r}", False),
    ("brace !r in source", "en", BRACE, "{a!r} {b}", None, "{a!r}", True),
    ("brace lone }", "en", BRACE, "{a}", None, "{a} }", True),
    ("brace format spec", "en", BRACE, "{a:>10}", None, "{a:<5}", False),
    ("star width added", "en", PY, "%d x", None, "%*d y", False),
    ("star width counts as argument", "en", PY, "%d %d x", None, "%*d y", True),
    ("star width in named", "en", PY, "%(n)d x", None, "%(n)*d y", False),
    ("repeated name, two types", "en", PY, "%(n)d file", None, "%(n)s %(n)d", False),
    ("repeated name, same type", "en", PY, "%(n)d file", None, "%(n)d %(n)i", True),
    ("repeated name in source", "en", PY, "%(n)d %(n)s", None, "%(n)d", True),
    ("named %% conversion", "en", PY, "%s", None, "%s %(x)%", False),
    ("%a kept", "en", PY, "%a", None, "%a", True),
    ("mixed source: named dropped", "en", PY, "%(n)d and %s", None, "%(n)d", True),
    ("mixed source: unnamed dropped", "en", PY, "%(x)s %s", None, "%(x)s", True),
    ("mixed translation", "en", PY, "%(n)d", None, "%(n)d %s", False),
    ("precision star kept", "en", PY, "%.*f x", None, "%.*f y", True),
    ("precision star dropped", "en", PY, "%.*f x", None, "%f y", False),
    ("width and precision", "en", PY, "%5.2f x", None, "%-8.3f y", True),
    ("length modifier", "en", PY, "%ld x", None, "%d y", True),
    ("unterminated name", "en", PY, "%(n)d", None, "%(n", False),
    ("brace nested spec", "en", BRACE, "{a:{w}}", None, "{a:{w}}", True),
    ("brace nested spec dropped", "en", BRACE, "{a:{w}} {b}", None, "{a} {b}", False),
    ("brace nested unterminated", "en", BRACE, "{a}", None, "{a:{w}", False),
    ("brace auto with attr", "en", BRACE, "{} {.x}", None, "{.x} {}", True),
    ("brace auto attr dropped", "en", BRACE, "{} {.x}", None, "{} {}", True),
    ("brace auto dropped (2 -> 1)", "en", BRACE, "{} {}", None, "{}", False),
    ("brace numbered attr dropped", "en", BRACE, "{0.x}", None, "{0}", False),
    ("brace numbered -> auto", "en", BRACE, "{0} {1}", None, "{} {}", False),
    ("brace auto -> numbered", "en", BRACE, "{} {}", None, "{1} {0}", True),
    ("brace field repeated", "en", BRACE, "{a.b}", None, "{a.b} {a.b}", True),
    # Third review round
    ("%a is invalid in source", "en", PY, "%a", None, "%s", True),
    ("%a is invalid in translation", "en", PY, "%s", None, "%a", False),
    ("literal %5%", "en", PY, "%s", None, "%s %5%", True),
    ("literal % %", "en", PY, "%s", None, "%s % %", True),
    ("literal %.%", "en", PY, "%s", None, "%s %.%", True),
    ("literal %5% first", "en", PY, "%d", None, "%5% %d", True),
    ("%*% takes an argument (dropped)", "en", PY, "%s %*%", None, "%s", False),
    ("%*% takes an argument (added)", "en", PY, "%s", None, "%s %*%", False),
    ("arabic-indic digit width", "en", PY, "%s", None, "%٣s", False),
    ("brace spec precision changed", "en", BRACE, "{a:.2f}", None, "{a:.3f}", False),
    ("brace spec dropped", "en", BRACE, "{a:.2f}", None, "{a}", False),
    ("brace spec added", "en", BRACE, "{a}", None, "{a:>5}", False),
    ("brace spec grouping ,", "en", BRACE, "{a:.2f}", None, "{a:,.2f}", False),
    ("brace spec grouping _", "en", BRACE, "{a}", None, "{a:_}", False),
    ("brace spec z", "en", BRACE, "{a}", None, "{a:z}", False),
    ("brace spec s type", "en", BRACE, "{a}", None, "{a:s}", False),
    ("brace spec %s", "en", BRACE, "{a}", None, "{a:%s}", False),
    ("brace spec x!r", "en", BRACE, "{a}", None, "{a:x!r}", False),
    ("brace spec full", "en", BRACE, "{a}", None, "{a:=+08.2f}", False),
    ("brace spec fill", "en", BRACE, "{a}", None, "{a:*^5}", False),
    ("brace spec F", "en", BRACE, "{a}", None, "{a:F}", False),
    ("brace spec double dot", "en", BRACE, "{a}", None, "{a:..2}", False),
    ("brace spec dot without digits", "en", BRACE, "{a}", None, "{a:5.f}", False),
    ("brace nested field with extra", "en", BRACE, "{a}", None, "{a:{w}x}", False),
    ("brace doubly nested", "en", BRACE, "{a}", None, "{a:{b:c}}", False),
    ("brace nested whole spec", "en", BRACE, "{a}", None, "{a:{w}}", False),
    ("brace nested in source", "en", BRACE, "{a:>{w}}", None, "{a}", True),
    # Fourth review round
    ("brace auto added to numbered", "en", BRACE, "{0} {b}", None, "{0} {b} {}", False),
    ("brace numbered -> auto (one)", "en", BRACE, "{0}", None, "{}", False),
    (
        "brace repeated field, spec dropped",
        "en",
        BRACE,
        "{a:.2f} ({a})",
        None,
        "{a}",
        False,
    ),
    (
        "brace repeated field kept",
        "en",
        BRACE,
        "{a:.2f} ({a})",
        None,
        "({a}) {a:.2f}",
        True,
    ),
    (
        "brace plural spec added",
        "en",
        BRACE,
        "{n} file",
        "{n} files",
        ["{n:>3} Datei", "{n:>3} Dateien"],
        True,
    ),
    ("%.0s is its own type", "en", PY, "%.0s%s x", None, "%s%s y", False),
    ("%.0s kept", "en", PY, "%.0s%s x", None, "%.0s%s y", True),
    (
        "plural %(n).0s for %(n)d",
        "en",
        PY,
        "%(n)d file",
        "%(n)d files",
        ["%(n).0seine Datei", "%(n)d Dateien"],
        True,
    ),
    ("named %(x)% in source", "en", PY, "%(x)% %(y)s", None, "%(x)%", False),
    ("nested parens in name", "en", PY, "%(a(b))s %(c)s", None, "%(a(b))s", False),
    (
        "nested parens in name kept",
        "en",
        PY,
        "%(a(b))s %(c)s",
        None,
        "%(c)s %(a(b))s",
        True,
    ),
]


# Brace-format cases where gettext versions disagree. Older gettext (e.g.
# Ubuntu's 0.21) is more lenient about dropped {} fields and stricter about
# numbering and format specs; the validator takes the stricter rule of the
# two, so a translation it accepts compiles with either.
GETTEXT_1_0_RULES = {  # validator matches 1.0; 0.21 accepts or rejects
    "brace auto dropped",
    "brace auto dropped (2 -> 1)",
}
GETTEXT_0_21_RULES = {  # validator matches 0.21; 1.0 accepts these
    "brace format spec",
    "brace nested spec dropped",
    "brace spec precision changed",
    "brace spec dropped",
    "brace spec added",
    "brace spec F",
    "brace spec fill",
    "brace spec full",
    "brace nested whole spec",
    "brace numbered -> auto",
    "brace numbered -> auto (one)",
    "brace repeated field, spec dropped",
}


def _msgfmt_version():
    result = subprocess.run(["msgfmt", "--version"], capture_output=True, text=True)
    match = re.search(r"(\d+)\.(\d+)", result.stdout)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


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
    ("name", "lang", "formats", "msgid", "msgid_plural", "msgstr", "valid"),
    CASES,
    ids=[case[0] for case in CASES],
)
def test_cases_match_msgfmt(
    tmp_path, name, lang, formats, msgid, msgid_plural, msgstr, valid
):
    """Every case above is what msgfmt --check-format itself decides."""
    if name in GETTEXT_1_0_RULES and _msgfmt_version() < (1, 0):
        pytest.skip("older msgfmt decides differently here; the validator follows 1.0")
    if name in GETTEXT_0_21_RULES and _msgfmt_version() >= (1, 0):
        pytest.skip("msgfmt 1.0 is more lenient here; the validator follows 0.21")
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
    assert "unterminated {field}" in translation_problem("Hi {name}", "Hi {name", BRACE)
    assert "conversion, which gettext doesn't support" in translation_problem(
        "{a}", "{a!r}", BRACE
    )
    assert "'*' width" in translation_problem("%(n)d", "%(n)*d", PY)
    assert "two different types" in translation_problem("%(n)d", "%(n)s %(n)d", PY)
    assert "mixed" in translation_problem("%(n)d", "%(n)d %s", PY)
    assert "lone" in translation_problem("%(n)d", "%(n", PY)
    assert "mixed" in translation_problem("%s", "%s %(x)%", PY)
    assert "unnumbered {} fields" in translation_problem("{0}", "{}", BRACE)
    assert "both numbered {0} and unnumbered {}" in translation_problem(
        "{} {}", "{0} {}", BRACE
    )
    assert "invalid format spec in {a:,.2f}" in translation_problem(
        "{a:.2f}", "{a:,.2f}", BRACE
    )
    assert "nested {a:{b:c}}" in translation_problem("{a}", "{a:{b:c}}", BRACE)
    assert "changed format spec for {a}" in translation_problem(
        "{a:.2f}", "{a:.3f}", BRACE
    )


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

    # The singular goes to plain entries of the same message: strict there,
    # with the plain entries' own format flags
    plural.has_plain = True
    assert plural.problem([["plik", "%(n)d pliki", "%(n)d plików"]]) is None
    plural.plain_formats = PY
    assert "(singular, for a non-plural entry)" in plural.problem(
        [["plik", "%(n)d pliki", "%(n)d plików"]]
    )
    assert plural.problem([["%(n)d plik", "%(n)d pliki", "%(n)d plików"]]) is None


def test_single_form_plain_duplicate_without_flag_is_not_checked():
    """ja + plural-aware provider: the only form "%(n)d ファイル" also goes
    to an unflagged plain "One file" entry, which msgfmt doesn't check."""
    POUnit = translate_module.POUnit
    unit = POUnit(None, "One file")
    unit.absorb(
        POUnit(
            None,
            "One file",
            "%(n)d files",
            formats=PY,
            nplurals=1,
            plural_forms=("0, 1, 2",),
        )
    )
    assert unit.problem([["%(n)d ファイル"]]) is None


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
    assert plain_first.has_plain
    assert (plain_first.formats, plain_first.plain_formats) == (PY, frozenset())

    plural_first = POUnit(None, "Item", "Items", formats=PY)
    plural_first.absorb(POUnit(None, "Item"))
    assert plural_first.has_plain
    assert (plural_first.formats, plural_first.plain_formats) == (PY, frozenset())

    plain_merged = POUnit(None, "Item")
    plain_merged.absorb(POUnit(None, "Item", formats=PY))
    assert not plain_merged.has_plain and plain_merged.formats == PY

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


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_failing_retry_request_keeps_the_valid_translations(temp_locale_dir, mocker):
    """Review finding: an API error on the retry used to lose the whole batch,
    while the error message said completed translations were saved."""
    from litellm.exceptions import BadRequestError

    from django.core.management.base import CommandError

    po_path = _write_po(temp_locale_dir / "de" / "LC_MESSAGES" / "django.po", ENTRIES)
    good = MagicMock()
    good.choices[0].message.content = json.dumps(
        ["Weiterlesen", "100 % kostenlos", "Hallo %(name)s"]
    )
    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        side_effect=[
            good,
            BadRequestError(message="boom", llm_provider="openai", model="m"),
        ],
    )

    with pytest.raises(CommandError, match="API request failed"):
        call_command("translate", target_lang="de")

    assert [e.msgstr for e in polib.pofile(str(po_path))] == [
        "Weiterlesen",
        "",
        "Hallo %(name)s",
    ]
