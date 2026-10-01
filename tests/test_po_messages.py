"""
Tests for msgctxt handling, plural forms, and API error reporting in PO
file translation.
"""

import json
from unittest.mock import MagicMock

import httpx
import polib
import pytest
from litellm.exceptions import (
    APIConnectionError,
    APIError,
    InternalServerError,
    RateLimitError,
    Timeout,
)

from django.core.management import call_command
from django.core.management.base import CommandError

from translatebot_django.management.commands.translate import (
    Command,
    PluralText,
    POUnit,
    TranslationValidationError,
    _build_input_payload,
    batch_by_tokens,
    gather_entries,
    plural_forms_from_header,
    translate_text,
)

POLISH_PLURAL_FORMS = (
    "nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 "
    "&& (n%100<10 || n%100>=20) ? 1 : 2);"
)


def _write_po(path, entries, plural_forms=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    po = polib.POFile()
    po.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    if plural_forms:
        po.metadata["Plural-Forms"] = plural_forms
    for entry in entries:
        po.append(entry)
    po.save(str(path))
    return path


def _llm_response(mocker, payload):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(payload)
    return mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        return_value=mock_response,
    )


def _sent_payload(mock):
    user_content = mock.call_args[1]["messages"][1]["content"]
    return json.loads(user_content[user_content.find("[") :])


# --- plural_forms_from_header ---


def test_plural_forms_from_header_polish():
    assert plural_forms_from_header(POLISH_PLURAL_FORMS) == (
        "1",
        "2, 3, 4, 22, 23",
        "0, 5, 6, 7, 8",
    )


def test_plural_forms_from_header_single_form():
    assert plural_forms_from_header("nplurals=1; plural=0;") == ("0, 1, 2, 3, 4",)


def test_plural_forms_from_header_marks_unused_forms():
    # A form no count maps to is still listed, so indices stay aligned
    assert plural_forms_from_header("nplurals=3; plural=(n != 1);") == (
        "1",
        "0, 2, 3, 4, 5",
        "(unused)",
    )


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "plural=(n != 1);",
        "nplurals=2;",
        "nplurals=0; plural=0;",
        "nplurals=2; plural=(n != 1) + import os;",
        "nplurals=2; plural=n % 0;",
    ],
)
def test_plural_forms_from_header_unusable(header):
    assert plural_forms_from_header(header) is None


# --- payload building ---


def test_build_input_payload_with_plural_text_and_aligned_comments():
    texts = ["Save", PluralText("%(n)d file", "%(n)d files", ("1", "0, 2"))]
    payload = _build_input_payload(texts, [None, "File counter"])
    assert payload == [
        {"text": "Save"},
        {
            "text": "%(n)d file",
            "plural": "%(n)d files",
            "comment": "File counter",
            "plural_forms": ["1", "0, 2"],
        },
    ]


def test_build_input_payload_all_empty_aligned_comments_is_plain():
    texts = ["Save", "Open"]
    assert _build_input_payload(texts, [None, None]) is texts


def test_batch_by_tokens_slices_aligned_comments(mocker):
    mocker.patch(
        "translatebot_django.management.commands.translate._get_model_limits",
        return_value=(10_000, 10_000),
    )
    spy = mocker.spy(
        __import__(
            "translatebot_django.management.commands.translate",
            fromlist=["_build_input_payload"],
        ),
        "_build_input_payload",
    )
    groups = batch_by_tokens(["a", "b"], "nl", "gpt-4o-mini", comments=["x", None])
    assert groups == [["a", "b"]]
    assert spy.call_args_list[-1].args == (["a", "b"], ["x", None])


def test_batch_by_tokens_counts_every_plural_form_as_output(mocker):
    """A plural text's output estimate covers each of its forms."""
    mocker.patch(
        "translatebot_django.management.commands.translate._get_model_limits",
        return_value=(100_000, 60),
    )
    plural = PluralText("word " * 10, "words " * 10, ("1", "2", "5"))
    # One plural text alone fits, two exceed the output budget together
    assert batch_by_tokens([plural, plural], "pl", "gpt-4o-mini") == [
        [plural],
        [plural],
    ]


# --- translate_text plural validation ---


@pytest.mark.parametrize(
    "returned",
    [
        "%(n)d plik",
        ["%(n)d plik", "%(n)d pliki"],
        ["%(n)d plik", "%(n)d pliki", 3],
    ],
)
def test_translate_text_rejects_malformed_plural(mocker, returned):
    _llm_response(mocker, [returned])
    plural = PluralText("%(n)d file", "%(n)d files", ("1", "2", "5"))
    with pytest.raises(TranslationValidationError, match="malformed plural"):
        translate_text([plural], "pl", "gpt-4o-mini", "key")


def test_translate_text_returns_plural_forms(mocker):
    forms = ["%(n)d plik", "%(n)d pliki", "%(n)d plików"]
    _llm_response(mocker, ["Zapisz", forms])
    plural = PluralText("%(n)d file", "%(n)d files", ("1", "2", "5"))
    assert translate_text(["Save", plural], "pl", "gpt-4o-mini", "key") == [
        "Zapisz",
        forms,
    ]


# --- gather_entries / POUnit ---


def test_gather_entries_keeps_msgctxt_variants_apart(tmp_path):
    po_path = _write_po(
        tmp_path / "django.po",
        [
            polib.POEntry(msgctxt="month", msgid="May", msgstr=""),
            polib.POEntry(msgctxt="permission", msgid="May", msgstr="", comment="Verb"),
            polib.POEntry(msgid="May", msgstr=""),
        ],
    )
    units = gather_entries(po_path)
    assert [(u.key, u.comment) for u in units] == [
        (("month", "May"), "Context: month"),
        (("permission", "May"), "Context: permission\nVerb"),
        ((None, "May"), None),
    ]


def test_gather_entries_skips_duplicate_entries(tmp_path):
    """A malformed PO file repeating a message yields it once."""
    po_path = _write_po(
        tmp_path / "django.po",
        [
            polib.POEntry(msgid="Save", msgstr="", comment="first"),
            polib.POEntry(msgid="Save", msgstr="", comment="second"),
        ],
    )
    assert [(u.msgid, u.comment) for u in gather_entries(po_path)] == [
        ("Save", "first")
    ]


def test_gather_entries_reads_plural_forms_from_header(tmp_path):
    po_path = _write_po(
        tmp_path / "django.po",
        [
            polib.POEntry(
                msgid="%(n)d file",
                msgid_plural="%(n)d files",
                msgstr_plural={0: "", 1: "", 2: ""},
            )
        ],
        plural_forms=POLISH_PLURAL_FORMS,
    )
    (unit,) = gather_entries(po_path)
    assert unit.plural_forms == ("1", "2, 3, 4, 22, 23", "0, 5, 6, 7, 8")
    assert unit.nplurals == 3


def test_gather_entries_nplurals_falls_back_to_entry_forms(tmp_path):
    po_path = _write_po(
        tmp_path / "django.po",
        [
            polib.POEntry(
                msgid="%(n)d file",
                msgid_plural="%(n)d files",
                msgstr_plural={0: "", 1: "", 2: ""},
            )
        ],
    )
    (unit,) = gather_entries(po_path)
    assert unit.plural_forms is None
    assert unit.nplurals == 3


def test_po_unit_without_plural_forms_sends_two_strings():
    unit = POUnit(None, "%(n)d file", "%(n)d files", nplurals=3)
    assert unit.provider_texts(plural_aware=True) == ["%(n)d file", "%(n)d files"]
    assert unit.translation_from(["%(n)d plik", "%(n)d pliki"]) == [
        "%(n)d plik",
        "%(n)d pliki",
        "%(n)d pliki",
    ]


# --- _save_po_translations ---


def test_save_po_translations_writes_per_msgctxt(tmp_path):
    po_path = _write_po(
        tmp_path / "django.po",
        [
            polib.POEntry(msgctxt="month", msgid="May", msgstr=""),
            polib.POEntry(msgctxt="permission", msgid="May", msgstr=""),
        ],
    )
    Command._save_po_translations(
        [po_path], {("month", "May"): "maj", ("permission", "May"): "może"}
    )
    po = polib.pofile(str(po_path))
    assert [(e.msgctxt, e.msgstr) for e in po] == [
        ("month", "maj"),
        ("permission", "może"),
    ]


def test_save_po_translations_fits_forms_to_entry(tmp_path):
    po_path = _write_po(
        tmp_path / "django.po",
        [
            # More plural slots than translated forms: the last one repeats
            polib.POEntry(
                msgid="%(n)d a",
                msgid_plural="%(n)d as",
                msgstr_plural={0: "", 1: "", 2: ""},
            ),
            # No slots at all: one per translated form
            polib.POEntry(msgid="%(n)d b", msgid_plural="%(n)d bs"),
            # A plain entry given plural forms takes the first
            polib.POEntry(msgid="c", msgstr=""),
        ],
    )
    Command._save_po_translations(
        [po_path],
        {
            (None, "%(n)d a"): ["A1", "A2"],
            (None, "%(n)d b"): ["B1", "B2"],
            (None, "c"): ["C1", "C2"],
        },
    )
    po = polib.pofile(str(po_path))
    assert po[0].msgstr_plural == {0: "A1", 1: "A2", 2: "A2"}
    assert po[1].msgstr_plural == {0: "B1", 1: "B2"}
    assert po[2].msgstr == "C1"


# --- translate command ---


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_translates_msgctxt_variants_separately(temp_locale_dir, mocker):
    po_path = _write_po(
        temp_locale_dir / "pl" / "LC_MESSAGES" / "django.po",
        [
            polib.POEntry(msgctxt="month", msgid="May", msgstr=""),
            polib.POEntry(msgctxt="permission", msgid="May", msgstr=""),
        ],
    )
    mock = _llm_response(mocker, ["maj", "może"])

    call_command("translate", target_lang="pl")

    assert _sent_payload(mock) == [
        {"text": "May", "comment": "Context: month"},
        {"text": "May", "comment": "Context: permission"},
    ]
    po = polib.pofile(str(po_path))
    assert [(e.msgctxt, e.msgstr) for e in po] == [
        ("month", "maj"),
        ("permission", "może"),
    ]


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_sends_shared_msgid_once(temp_locale_dir, mocker):
    """A msgid found in django.po and djangojs.po is translated once."""
    lc = temp_locale_dir / "nl" / "LC_MESSAGES"
    for name in ("django.po", "djangojs.po"):
        _write_po(lc / name, [polib.POEntry(msgid="Save", msgstr="")])
    mock = _llm_response(mocker, ["Opslaan"])

    call_command("translate", target_lang="nl")

    assert _sent_payload(mock) == ["Save"]
    for name in ("django.po", "djangojs.po"):
        assert polib.pofile(str(lc / name))[0].msgstr == "Opslaan"


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_dry_run_counts_messages(temp_locale_dir, mocker):
    _write_po(
        temp_locale_dir / "pl" / "LC_MESSAGES" / "django.po",
        [
            polib.POEntry(msgctxt="month", msgid="May", msgstr=""),
            polib.POEntry(msgctxt="permission", msgid="May", msgstr=""),
            polib.POEntry(
                msgid="%(n)d file",
                msgid_plural="%(n)d files",
                msgstr_plural={0: "", 1: ""},
            ),
        ],
    )
    mock = mocker.patch("translatebot_django.management.commands.translate.completion")

    from translatebot_django import translate

    result = translate(target_langs="pl", dry_run=True)

    mock.assert_not_called()
    assert result.strings_found == 3
    assert result.strings_translated == 3


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key")
def test_command_deepl_fills_extra_plural_forms(temp_locale_dir, settings, mocker):
    """Providers without plural support get singular + plural; the plural
    translation fills every form past the first."""
    settings.TRANSLATEBOT_PROVIDER = "deepl"
    po_path = _write_po(
        temp_locale_dir / "pl" / "LC_MESSAGES" / "django.po",
        [
            polib.POEntry(
                msgid="%(n)d file",
                msgid_plural="%(n)d files",
                msgstr_plural={0: "", 1: "", 2: ""},
            )
        ],
        plural_forms=POLISH_PLURAL_FORMS,
    )
    translator = MagicMock()
    translator.translate_text.return_value = [
        MagicMock(text="ph0@tb.x plik"),
        MagicMock(text="ph0@tb.x pliki"),
    ]
    mocker.patch("deepl.Translator", return_value=translator)

    call_command("translate", target_lang="pl")

    assert translator.translate_text.call_args[0][0] == [
        "ph0@tb.x file",
        "ph0@tb.x files",
    ]
    entry = polib.pofile(str(po_path))[0]
    assert entry.msgstr_plural == {0: "%(n)d plik", 1: "%(n)d pliki", 2: "%(n)d pliki"}


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key")
def test_command_plural_split_across_batches(temp_locale_dir, settings, mocker):
    """A message whose texts land in two batches is saved once both are in."""
    settings.TRANSLATEBOT_PROVIDER = "deepl"
    singles = [polib.POEntry(msgid=f"s{i}", msgstr="") for i in range(49)]
    plural = polib.POEntry(
        msgid="%(n)d file",
        msgid_plural="%(n)d files",
        msgstr_plural={0: "", 1: ""},
    )
    po_path = _write_po(
        temp_locale_dir / "nl" / "LC_MESSAGES" / "django.po", [*singles, plural]
    )
    translator = MagicMock()
    translator.translate_text.side_effect = lambda texts, **_: [
        MagicMock(text=f"T:{t}") for t in texts
    ]
    mocker.patch("deepl.Translator", return_value=translator)
    saved_keys = []
    real_save = Command._save_po_translations

    def record_save(po_paths, translations, overwrite=False):
        saved_keys.append(set(translations))
        real_save(po_paths, translations, overwrite=overwrite)

    mocker.patch.object(Command, "_save_po_translations", side_effect=record_save)

    call_command("translate", target_lang="nl")

    # DeepL takes 50 texts per request: the singular ends batch 1 and the
    # plural starts batch 2, so the message is only complete after batch 2.
    batches = [c[0][0] for c in translator.translate_text.call_args_list]
    assert [len(b) for b in batches] == [50, 1]
    assert (None, "%(n)d file") not in saved_keys[0]
    assert (None, "%(n)d file") in saved_keys[1]
    entry = polib.pofile(str(po_path))[-1]
    assert entry.msgstr_plural == {
        0: "T:%(n)d file",
        1: "T:%(n)d files",
    }


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key")
def test_command_rejects_wrong_translation_count(sample_po_file, settings, mocker):
    settings.TRANSLATEBOT_PROVIDER = "deepl"
    translator = MagicMock()
    translator.translate_text.return_value = [MagicMock(text="Hallo")]
    mocker.patch("deepl.Translator", return_value=translator)
    mocker.patch(
        "translatebot_django.providers.deepl.DeepLProvider.translate",
        return_value=["Hallo"],
    )

    with pytest.raises(CommandError, match="returned 1 translations, expected 2"):
        call_command("translate", target_lang="nl")


# --- API error reporting ---


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_invalid_json_is_command_error(sample_po_file, mocker):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "not json ["
    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        return_value=mock_response,
    )

    with pytest.raises(CommandError, match="Failed to parse JSON"):
        call_command("translate", target_lang="nl")


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_connection_error_is_command_error(sample_po_file, mocker):
    sleep = mocker.patch("translatebot_django.management.commands.translate.time.sleep")
    completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        side_effect=APIConnectionError(
            message="Connection refused", llm_provider="openai", model="gpt-4o-mini"
        ),
    )

    with pytest.raises(CommandError, match="APIConnectionError") as exc_info:
        call_command("translate", target_lang="nl")
    assert "have been saved" in str(exc_info.value)
    # Retried twice after short waits before giving up
    assert completion.call_count == 3
    assert [c.args[0] for c in sleep.call_args_list] == [5, 15]


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_rate_limit_exhausted_is_command_error(sample_po_file, mocker):
    mocker.patch("translatebot_django.management.commands.translate.time.sleep")
    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        side_effect=RateLimitError(
            message="Too many requests", llm_provider="openai", model="gpt-4o-mini"
        ),
    )

    with pytest.raises(CommandError, match="Rate limit still exceeded after 5"):
        call_command("translate", target_lang="nl")


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key", "mock_model_config")
def test_command_reports_summary_before_translating(sample_po_file, mock_completion):
    from io import StringIO

    mock_completion("Vertaald")
    out = StringIO()
    call_command("translate", target_lang="nl", stdout=out)

    output = out.getvalue()
    assert (
        output.index("Found 2 untranslated entries")
        < output.index("Translating with gpt-4o-mini")
        < output.index("Saved batch 1/1")
        < output.index("Processing:")
    )


# --- Merging the same message across PO files (review findings) ---


def test_po_unit_absorb_prefers_plural_and_known_forms():
    plain = POUnit(None, "Item", comment="first")
    plural = POUnit(None, "Item", "Items", plural_forms=("1", "2", "5"), nplurals=3)
    plain.absorb(plural)
    assert (plain.msgid_plural, plain.plural_forms, plain.nplurals) == (
        "Items",
        ("1", "2", "5"),
        3,
    )
    # Comment kept: the absorbed unit has none
    assert plain.comment == "first"

    unknown = POUnit(None, "Item", "Items")
    unknown.absorb(plural)
    assert unknown.plural_forms == ("1", "2", "5")

    # A plain or less-informed duplicate changes nothing
    plural.absorb(POUnit(None, "Item"))
    plural.absorb(POUnit(None, "Item", "Items"))
    assert (plural.plural_forms, plural.nplurals) == (("1", "2", "5"), 3)


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
@pytest.mark.parametrize("plain_first", [True, False])
def test_command_plural_wins_over_plain_duplicate(
    tmp_path, settings, mocker, plain_first
):
    """gettext("Item") in one app and ngettext("Item", "Items") in another:
    the plural forms are translated regardless of file order."""
    plain_dir, plural_dir = tmp_path / "a", tmp_path / "b"
    if not plain_first:
        plain_dir, plural_dir = plural_dir, plain_dir
    plain_po = _write_po(
        plain_dir / "pl" / "LC_MESSAGES" / "django.po",
        [polib.POEntry(msgid="Item", msgstr="")],
    )
    plural_po = _write_po(
        plural_dir / "pl" / "LC_MESSAGES" / "django.po",
        [
            polib.POEntry(
                msgid="Item", msgid_plural="Items", msgstr_plural={0: "", 1: "", 2: ""}
            )
        ],
        plural_forms=POLISH_PLURAL_FORMS,
    )
    settings.LOCALE_PATHS = [str(tmp_path / "a"), str(tmp_path / "b")]
    mock = _llm_response(mocker, [["element", "elementy", "elementów"]])

    call_command("translate", target_lang="pl")

    assert _sent_payload(mock)[0]["plural"] == "Items"
    assert polib.pofile(str(plain_po))[0].msgstr == "element"
    assert polib.pofile(str(plural_po))[0].msgstr_plural == {
        0: "element",
        1: "elementy",
        2: "elementów",
    }


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_command_context_groups_keep_their_own_translations(tmp_path, settings, mocker):
    """An app with its own TRANSLATING.md gets its own translation of a
    shared msgid, never another group's."""
    settings.BASE_DIR = tmp_path
    project_po = _write_po(
        tmp_path / "locale" / "nl" / "LC_MESSAGES" / "django.po",
        [polib.POEntry(msgid="Bank", msgstr="")],
    )
    app_dir = tmp_path / "shop"
    app_dir.mkdir()
    (app_dir / "TRANSLATING.md").write_text("Bank means a river bank.")
    app_po = _write_po(
        app_dir / "locale" / "nl" / "LC_MESSAGES" / "django.po",
        [
            polib.POEntry(msgid="Water", msgstr=""),
            polib.POEntry(msgid="Bank", msgstr=""),
        ],
    )
    settings.LOCALE_PATHS = [str(tmp_path / "locale"), str(app_dir / "locale")]
    # One text per batch: the app's files are saved after "Water", before
    # its own "Bank" is translated
    mocker.patch(
        "translatebot_django.providers.litellm.LiteLLMProvider.batch",
        side_effect=lambda texts, *_, **__: [[t] for t in texts],
    )

    def respond(**kwargs):
        system = kwargs["messages"][0]["content"]
        source = _sent_payload(MagicMock(call_args=((), kwargs)))[0]
        if source == "Water":
            word = "Water"
        elif "river bank" in system:
            word = "Oever"
        else:
            word = "Bank (financieel)"
        response = MagicMock()
        response.choices[0].message.content = json.dumps([word])
        return response

    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        side_effect=respond,
    )

    call_command("translate", target_lang="nl")

    assert polib.pofile(str(project_po))[0].msgstr == "Bank (financieel)"
    assert polib.pofile(str(app_po))[1].msgstr == "Oever"


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
@pytest.mark.parametrize("dry_run", [False, True])
def test_strings_translated_counts_only_written_entries(
    temp_locale_dir, mock_completion, dry_run
):
    """An entry already translated in one file isn't counted (or reported)
    just because the same msgid was translated for another file."""
    lc = temp_locale_dir / "nl" / "LC_MESSAGES"
    _write_po(lc / "django.po", [polib.POEntry(msgid="Save", msgstr="Bewaren")])
    _write_po(lc / "djangojs.po", [polib.POEntry(msgid="Save", msgstr="")])
    mock_completion("Opslaan")

    from translatebot_django import translate

    result = translate(target_langs="nl", dry_run=dry_run)

    assert result.strings_found == 1
    assert result.strings_translated == 1
    assert polib.pofile(str(lc / "django.po"))[0].msgstr == "Bewaren"
    if not dry_run:
        assert polib.pofile(str(lc / "djangojs.po"))[0].msgstr == "Opslaan"


# --- Second review round ---

ARABIC_PLURAL_FORMS = (
    "nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : "
    "n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5;"
)


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
def test_plain_duplicate_gets_singular_form_not_form_zero(temp_locale_dir, mocker):
    """In Arabic, msgstr[0] is the zero form; a plain entry merged with a
    plural one must get the form used for a count of 1."""
    lc = temp_locale_dir / "ar" / "LC_MESSAGES"
    plural_po = _write_po(
        lc / "django.po",
        [
            polib.POEntry(
                msgid="Item",
                msgid_plural="Items",
                msgstr_plural=dict.fromkeys(range(6), ""),
            )
        ],
        plural_forms=ARABIC_PLURAL_FORMS,
    )
    plain_po = _write_po(
        lc / "djangojs.po",
        [polib.POEntry(msgid="Item", msgstr="")],
        plural_forms=ARABIC_PLURAL_FORMS,
    )
    forms = ["zero", "one", "two", "few", "many", "other"]
    mock = _llm_response(mocker, [forms])

    call_command("translate", target_lang="ar")

    assert _sent_payload(mock)[0]["plural_forms"][:3] == ["0", "1", "2"]
    assert polib.pofile(str(plural_po))[0].msgstr_plural == dict(enumerate(forms))
    assert polib.pofile(str(plain_po))[0].msgstr == "one"


def test_po_unit_singular_falls_back_to_first_form():
    unit = POUnit(None, "Item", "Items", plural_forms=("0", "2"), nplurals=2)
    assert unit.translation_from([["a", "b"]]).singular == "a"
    two_strings = POUnit(None, "Item", "Items", nplurals=3)
    assert two_strings.translation_from(["s", "p"]).singular == "s"


@pytest.mark.usefixtures("mock_env_api_key", "mock_model_config")
@pytest.mark.parametrize("dry_run", [False, True])
def test_found_and_translated_counts_agree_across_files(
    temp_locale_dir, mock_completion, dry_run
):
    """A msgid untranslated in two files is sent once, but found and
    translated are both counted per file."""
    lc = temp_locale_dir / "nl" / "LC_MESSAGES"
    for name in ("django.po", "djangojs.po"):
        _write_po(lc / name, [polib.POEntry(msgid="Save", msgstr="")])
    mock = mock_completion("Opslaan")

    from translatebot_django import translate

    result = translate(target_langs="nl", dry_run=dry_run)

    assert result.strings_found == 2
    assert result.strings_translated == 2
    assert mock.call_count == (0 if dry_run else 1)


# --- Request timeout and transient retries ---


def _ok_response(payload):
    response = MagicMock()
    response.choices[0].message.content = json.dumps(payload)
    return response


def test_translate_text_passes_timeout_and_disables_client_retries(mocker):
    completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        return_value=_ok_response(["Hallo"]),
    )
    translate_text(["Hello"], "nl", "gpt-4o-mini", "key", timeout=42)
    kwargs = completion.call_args.kwargs
    assert kwargs["timeout"] == 42
    assert kwargs["max_retries"] == 0


def test_translate_text_retries_transient_errors(mocker, caplog):
    sleep = mocker.patch("translatebot_django.management.commands.translate.time.sleep")
    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        side_effect=[
            Timeout(message="slow", llm_provider="deepseek", model="deepseek-chat"),
            InternalServerError(
                message="boom", llm_provider="deepseek", model="deepseek-chat"
            ),
            _ok_response(["Hallo"]),
        ],
    )
    assert translate_text(["Hello"], "nl", "deepseek/deepseek-chat", "key") == ["Hallo"]
    assert [c.args[0] for c in sleep.call_args_list] == [5, 15]
    assert "Timeout from deepseek/deepseek-chat, waiting 5s before retry (1/2)" in (
        caplog.text
    )


@pytest.mark.usefixtures("temp_locale_dir", "mock_env_api_key")
def test_command_timeout_exhausted_is_command_error(sample_po_file, settings, mocker):
    settings.TRANSLATEBOT_MODEL = "deepseek/deepseek-chat"
    settings.TRANSLATEBOT_TIMEOUT = 30
    mocker.patch("translatebot_django.management.commands.translate.time.sleep")
    completion = mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        side_effect=Timeout(
            message="Connection timed out",
            llm_provider="deepseek",
            model="deepseek-chat",
        ),
    )

    with pytest.raises(CommandError, match="did not respond in time") as exc_info:
        call_command("translate", target_lang="nl")
    assert "TRANSLATEBOT_TIMEOUT" in str(exc_info.value)
    assert completion.call_count == 3
    assert completion.call_args.kwargs["timeout"] == 30


@pytest.mark.parametrize("value", [0, -5, "60", None, True])
def test_get_timeout_rejects_invalid_values(settings, value):
    from translatebot_django.utils import get_timeout

    settings.TRANSLATEBOT_TIMEOUT = value
    with pytest.raises(CommandError, match="TRANSLATEBOT_TIMEOUT"):
        get_timeout()


def test_get_timeout_default_and_float(settings):
    from translatebot_django.utils import DEFAULT_TIMEOUT_SECONDS, get_timeout

    assert get_timeout() == DEFAULT_TIMEOUT_SECONDS == 300
    settings.TRANSLATEBOT_TIMEOUT = 7.5
    assert get_timeout() == 7.5


def _rate_limit(headers=None, litellm_headers=None):
    response = httpx.Response(
        429, headers=headers or {}, request=httpx.Request("POST", "https://x")
    )
    error = RateLimitError(
        message="Too many requests",
        llm_provider="openai",
        model="gpt-4o-mini",
        response=response,
    )
    if litellm_headers is not None:
        error.litellm_response_headers = litellm_headers
    return error


@pytest.mark.parametrize(
    ("error", "expected_wait"),
    [
        (_rate_limit({"retry-after": "3"}), 3),
        (_rate_limit({"retry-after-ms": "1500"}), 2),
        (_rate_limit({"retry-after": "0"}), 1),
        (_rate_limit({"retry-after": "900"}), 60),  # never longer than ours
        (_rate_limit({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}), 60),
        (_rate_limit(litellm_headers={"retry-after": "7"}), 7),
        (_rate_limit({"retry-after": "4"}, litellm_headers={"x-other": "1"}), 4),
        (_rate_limit(), 60),
    ],
)
def test_rate_limit_honours_retry_after(mocker, error, expected_wait):
    sleep = mocker.patch("translatebot_django.management.commands.translate.time.sleep")
    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        side_effect=[error, _ok_response(["Hallo"])],
    )
    assert translate_text(["Hello"], "nl", "gpt-4o-mini", "key") == ["Hallo"]
    assert sleep.call_args.args[0] == expected_wait


def test_generic_5xx_api_error_is_retried(mocker):
    sleep = mocker.patch("translatebot_django.management.commands.translate.time.sleep")
    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        side_effect=[
            APIError(status_code=522, message="cf", llm_provider="openai", model="m"),
            _ok_response(["Hallo"]),
        ],
    )
    assert translate_text(["Hello"], "nl", "gpt-4o-mini", "key") == ["Hallo"]
    assert sleep.call_count == 1


@pytest.mark.parametrize("status_code", [418, None])
def test_non_5xx_api_error_is_not_retried(mocker, status_code):
    sleep = mocker.patch("translatebot_django.management.commands.translate.time.sleep")
    error = APIError(
        status_code=418, message="teapot", llm_provider="openai", model="m"
    )
    error.status_code = status_code
    mocker.patch(
        "translatebot_django.management.commands.translate.completion",
        side_effect=error,
    )
    with pytest.raises(APIError):
        translate_text(["Hello"], "nl", "gpt-4o-mini", "key")
    sleep.assert_not_called()


@pytest.mark.parametrize("header", [None, "nplurals=INTEGER; plural=EXPRESSION;"])
def test_gather_entries_warns_about_unusable_plural_forms(tmp_path, caplog, header):
    po_path = _write_po(
        tmp_path / "django.po",
        [
            polib.POEntry(
                msgid="%(n)d file", msgid_plural="%(n)d files", msgstr_plural={0: ""}
            )
        ],
        plural_forms=header,
    )
    gather_entries(po_path)
    assert "no usable Plural-Forms header" in caplog.text


def test_gather_entries_no_plural_warning_without_plural_entries(tmp_path, caplog):
    po_path = _write_po(tmp_path / "django.po", [polib.POEntry(msgid="a", msgstr="")])
    gather_entries(po_path)
    assert "Plural-Forms" not in caplog.text
