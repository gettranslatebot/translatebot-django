"""Checks that a provider's translation of a PO message is safe to write.

They mirror what ``msgfmt --check-format`` (GNU gettext, run by
``compilemessages``) rejects for ``python-format`` and
``python-brace-format`` entries, so a translation that would break
compilation is caught before it is written, and add a check for degenerate
output (index markers instead of text). The rules were confirmed against
gettext 1.0; see tests/test_validation.py.
"""

import re

PYTHON_FORMAT = "python-format"
PYTHON_BRACE_FORMAT = "python-brace-format"
FORMAT_FLAGS = frozenset({PYTHON_FORMAT, PYTHON_BRACE_FORMAT})

# Conversions msgfmt treats as interchangeable
_TYPE_CLASSES = {
    **dict.fromkeys("diouxX", "integer"),
    **dict.fromkeys("eEfgG", "float"),
    **dict.fromkeys("sr", "string"),
    "a": "ascii",
    "c": "character",
}

# What a model sometimes returns instead of a translation: "#1", "#2", ...
_INDEX_MARKER_RE = re.compile(r"^\s*#\d+\s*$")

_LONE_PERCENT = "a lone '%' (a literal percent sign must be written as '%%')"


class _Invalid(Exception):
    """The string isn't a valid format string; the message says why."""


def _skip_width(text, i, stars):
    """Skip a width or precision (``*`` or digits) at *i*; count ``*``."""
    if text.startswith("*", i):
        return i + 1, stars + 1
    while i < len(text) and text[i].isdigit():
        i += 1
    return i, stars


def _percent_specs(text):
    """Parse *text* as a python-format string, the way msgfmt does.

    Returns ``(named, unnamed)``: *named* maps each name to its type class,
    *unnamed* lists the type classes of positional arguments in order
    (``*`` widths take an integer argument of their own).

    Raises:
        _Invalid: For a lone ``%``, an unknown conversion, ``*`` in a named
            conversion, a name used with two types, or named and unnamed
            conversions mixed.
    """
    named = {}
    unnamed = []
    i = 0
    while True:
        i = text.find("%", i)
        if i == -1:
            break
        i += 1
        if text.startswith("%", i):
            i += 1
            continue
        name = None
        if text.startswith("(", i):
            end = text.find(")", i)
            if end == -1:
                raise _Invalid(_LONE_PERCENT)
            name = text[i + 1 : end]
            i = end + 1
        while i < len(text) and text[i] in "#0- +":
            i += 1
        stars = 0
        i, stars = _skip_width(text, i, stars)
        if text.startswith(".", i):
            i, stars = _skip_width(text, i + 1, stars)
        if i < len(text) and text[i] in "hlL":
            i += 1
        conv = text[i] if i < len(text) else ""
        if conv not in _TYPE_CLASSES:
            raise _Invalid(_LONE_PERCENT)
        i += 1
        type_class = _TYPE_CLASSES[conv]
        if name is None:
            unnamed.extend(["integer"] * stars + [type_class])
        elif stars:
            raise _Invalid(f"a '*' width in the named placeholder %({name})")
        elif named.setdefault(name, type_class) != type_class:
            raise _Invalid(f"placeholder {name} used with two different types")
    if named and unnamed:
        raise _Invalid("named and unnamed placeholders mixed")
    return named, unnamed


def _brace_fields(text):
    """Parse *text* as a python-brace-format string, the way msgfmt does.

    Returns the set of replacement fields, each with its full
    ``.attribute`` / ``[index]`` chain. Auto-numbered ``{}`` fields are
    numbered by position, as str.format() does, without their chain: msgfmt
    is inconsistent about those (it accepts ``{.x}`` -> ``{.y}`` but not
    ``{}`` -> ``{.x}``), and being lenient there is safer than rejecting a
    valid translation. A lone ``}`` is literal.

    Raises:
        _Invalid: For an unterminated field, or a ``!conversion``, which
            msgfmt doesn't support.
    """
    fields = set()
    position = 0
    i = 0
    while True:
        i = text.find("{", i)
        if i == -1:
            break
        if text.startswith("{", i + 1):
            i += 2
            continue
        depth = 0
        end = i + 1
        while end < len(text):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                if depth == 0:
                    break
                depth -= 1
            end += 1
        else:
            raise _Invalid("an unterminated {field}")
        field = re.split(r"[:]", text[i + 1 : end], maxsplit=1)[0]
        if "!" in field:
            raise _Invalid(f"a {{{field}}} conversion, which gettext doesn't support")
        if re.match(r"[^.\[]*", field).group() == "":
            field = str(position)
            position += 1
        fields.add(field)
        i = end + 1
    return fields


def _check_percent(source, translation, may_omit):
    try:
        source_named, source_unnamed = _percent_specs(source)
    except _Invalid:
        # msgfmt doesn't compare against an invalid source; neither do we,
        # or a valid translation could never be written
        return None
    try:
        named, unnamed = _percent_specs(translation)
    except _Invalid as e:
        return str(e)
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
    try:
        source_fields = _brace_fields(source)
    except _Invalid:
        return None
    try:
        fields = _brace_fields(translation)
    except _Invalid as e:
        return str(e)
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
