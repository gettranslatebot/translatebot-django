"""Checks that a provider's translation of a PO message is safe to write.

They mirror what ``msgfmt --check-format`` (GNU gettext, run by
``compilemessages``) rejects for ``python-format`` and
``python-brace-format`` entries, so a translation that would break
compilation is caught before it is written, and add a check for degenerate
output (index markers instead of text). The rules were confirmed against
gettext 1.0; see tests/test_validation.py.
"""

import re
import string

PYTHON_FORMAT = "python-format"
PYTHON_BRACE_FORMAT = "python-brace-format"
FORMAT_FLAGS = frozenset({PYTHON_FORMAT, PYTHON_BRACE_FORMAT})

# A printf-style conversion gettext accepts in python-format strings:
# %s, %(name)s, %-5.2f, %*d, %%, ... (%F and %a are not among them).
_PERCENT_SPEC_RE = re.compile(
    r"%(?:\((?P<name>[^)]*)\))?[#0\- +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?"
    r"(?P<conv>[diouxXeEfgGcsr%])"
)

# Conversions msgfmt treats as interchangeable
_TYPE_CLASSES = {
    **dict.fromkeys("diouxX", "integer"),
    **dict.fromkeys("eEfgG", "float"),
    **dict.fromkeys("sr", "string"),
    "c": "character",
}

# What a model sometimes returns instead of a translation: "#1", "#2", ...
_INDEX_MARKER_RE = re.compile(r"^\s*#\d+\s*$")


def _percent_specs(text):
    """Return ``(named, unnamed)`` conversions in *text*, or None if invalid.

    *named* maps each name to its type class; *unnamed* lists the type
    classes in order. A ``%`` that isn't part of a valid conversion (a
    literal percent sign must be written ``%%``) makes the text invalid.
    """
    if "%" in _PERCENT_SPEC_RE.sub("", text):
        return None
    named = {}
    unnamed = []
    for match in _PERCENT_SPEC_RE.finditer(text):
        conv = match.group("conv")
        if conv == "%":
            continue
        type_class = _TYPE_CLASSES[conv]
        if match.group("name") is not None:
            named[match.group("name")] = type_class
        else:
            unnamed.append(type_class)
    return named, unnamed


def _brace_fields(text):
    """Return the set of field names in *text*, or None if it's invalid.

    Auto-numbered ``{}`` fields are numbered by position, as str.format()
    does, so dropping one of two ``{}`` is noticed.
    """
    fields = set()
    position = 0
    try:
        for _, field, _, _ in string.Formatter().parse(text):
            if field is None:
                continue
            name = re.split(r"[.\[]", field, maxsplit=1)[0]
            if name == "":
                name = str(position)
                position += 1
            fields.add(name)
    except ValueError:
        return None
    return fields


def _check_percent(source, translation, may_omit):
    specs = _percent_specs(translation)
    if specs is None:
        return "a lone '%' (a literal percent sign must be written as '%%')"
    named, unnamed = specs
    source_specs = _percent_specs(source)
    if source_specs is None:
        # The source itself isn't a valid format string; nothing to compare
        return None
    source_named, source_unnamed = source_specs
    extra = sorted(set(named) - set(source_named))
    if extra:
        return f"placeholders not in the source: {', '.join(extra)}"
    missing = sorted(set(source_named) - set(named))
    if missing and not may_omit:
        return f"missing placeholders: {', '.join(missing)}"
    changed = sorted(n for n in named if named[n] != source_named[n])
    if changed:
        return f"placeholders with a different type: {', '.join(changed)}"
    if len(unnamed) != len(source_unnamed):
        return (
            f"{len(unnamed)} unnamed placeholders, the source has {len(source_unnamed)}"
        )
    if unnamed != source_unnamed:
        return "unnamed placeholders with a different type or order"
    return None


def _check_brace(source, translation, may_omit):
    fields = _brace_fields(translation)
    if fields is None:
        return "an invalid {field} (e.g. an unmatched brace)"
    source_fields = _brace_fields(source)
    if source_fields is None:
        return None
    extra = sorted(fields - source_fields)
    if extra:
        return f"fields not in the source: {', '.join('{' + f + '}' for f in extra)}"
    missing = sorted(source_fields - fields)
    if missing and not may_omit:
        return f"missing fields: {', '.join('{' + f + '}' for f in missing)}"
    return None


def translation_problem(source, translation, formats=frozenset(), may_omit=False):
    """Describe why *translation* can't be written, or return None if it can.

    Args:
        source: The string msgfmt compares the translation with: the msgid,
            or for a plural form the msgid_plural.
        translation: One translated string (a msgstr, or one plural form).
        formats: The entry's format flags (``python-format``,
            ``python-brace-format``); placeholders are only checked when
            flagged, since an unflagged ``%`` is plain text.
        may_omit: Whether named placeholders and fields may be left out.
            msgfmt allows this in the plural forms of a language with more
            than one form ("one file"); positional ``%s``/``%d`` must
            always all be there.
    """
    if not isinstance(translation, str):
        return "not a string"
    if _INDEX_MARKER_RE.match(translation) and not _INDEX_MARKER_RE.match(source):
        return f"an index marker ({translation.strip()!r}) instead of a translation"
    if source.strip() and not translation.strip():
        return "empty"
    if PYTHON_FORMAT in formats:
        problem = _check_percent(source, translation, may_omit)
        if problem:
            return problem
    if PYTHON_BRACE_FORMAT in formats:
        problem = _check_brace(source, translation, may_omit)
        if problem:
            return problem
    return None
