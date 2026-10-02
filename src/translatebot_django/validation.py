"""Checks that a provider's translation of a PO message is safe to write.

They mirror what ``msgfmt --check-format`` (GNU gettext, run by
``compilemessages``) rejects for ``python-format`` and
``python-brace-format`` entries, so a translation that would break
compilation is caught before it is written, and add a check for degenerate
output (index markers instead of text). Where gettext 0.21 (common on
Debian/Ubuntu) and 1.0 disagree, the stricter rule is used, so an accepted
translation compiles with either; tests/test_validation.py cross-checks
every rule against the installed msgfmt.
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
    "c": "character",
}

# A python-brace-format spec as gettext accepts it: [[fill]align][sign][#][0]
# [width][.precision][type]. Note: no "," / "_" grouping and no "s" type.
_BRACE_SPEC = r"(?:[\x00-\x7f]?[<>=^])?[-+ ]?#?0?[0-9]*(?:\.[0-9]{})?[bcdeEfFgGnoxX%]?"
# gettext 0.21 also accepts a "." without digits ("{a:.f}"); 1.0 doesn't.
# Such specs are parsed (0.21 compares them), but a translation may only use
# one the source has (see _check_brace).
_BRACE_SPEC_RE = re.compile(_BRACE_SPEC.format("*"))
_BRACE_SPEC_1_0_RE = re.compile(_BRACE_SPEC.format("+"))

# A field name as gettext accepts it: an ASCII identifier or number, then
# any .attribute (identifier) or [index] (ASCII letters, digits, _) parts
_BRACE_BASE = r"(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+)"
_BRACE_CHAIN = r"(?:\.[A-Za-z_][A-Za-z0-9_]*|\[(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+)\])*"
_BRACE_NAME_RE = re.compile(f"{_BRACE_BASE}?{_BRACE_CHAIN}")
# The whole spec may be one nested field, which needs a name: "{a:{w.x}}"
_BRACE_NESTED_RE = re.compile(f"\\{{{_BRACE_BASE}{_BRACE_CHAIN}\\}}")

# What a model sometimes returns instead of a translation: "#1", "#2", ...
_INDEX_MARKER_RE = re.compile(r"^\s*#\d+\s*$")

_LONE_PERCENT = "a lone '%' (a literal percent sign must be written as '%%')"


class _Invalid(Exception):
    """The string isn't a valid format string; the message says why."""


def _skip_width(text, i, stars):
    """Skip a width or precision (``*`` or ASCII digits) at *i*; count ``*``."""
    if text.startswith("*", i):
        return i + 1, stars + 1
    while i < len(text) and "0" <= text[i] <= "9":
        i += 1
    return i, stars


def _percent_specs(text):
    """Parse *text* as a python-format string, the way msgfmt does.

    Returns ``(named, unnamed)``: *named* maps each name to its type class,
    *unnamed* lists the type classes of positional arguments in order
    (``*`` widths take an integer argument of their own).

    ``%.0s`` has the type "any" and ``%(x)%`` the type "none", as in gettext.

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
            # Names may contain balanced parentheses: %(a(b))s
            depth = 0
            for end in range(i, len(text)):
                depth += {"(": 1, ")": -1}.get(text[end], 0)
                if depth == 0:
                    break
            else:
                raise _Invalid(_LONE_PERCENT)
            name = text[i + 1 : end]
            i = end + 1
        while i < len(text) and text[i] in "#0- +":
            i += 1
        stars = 0
        i, stars = _skip_width(text, i, stars)
        precision = None
        if text.startswith(".", i):
            start = i + 1
            i, stars = _skip_width(text, start, stars)
            precision = text[start:i]
        if i < len(text) and text[i] in "hlL":
            i += 1
        conv = text[i] if i < len(text) else ""
        i += 1
        if conv == "%":
            if name is None:
                # "%5%" or "% %" is a literal percent sign too, but a "*" in
                # it still takes an argument
                unnamed.extend(["integer"] * stars)
                continue
            # "%(x)%" consumes the argument x without a type
            type_class = "none"
        elif conv not in _TYPE_CLASSES:
            raise _Invalid(_LONE_PERCENT)
        elif conv in "sr" and precision and precision.strip("0") == "":
            # "%.0s" prints nothing, so gettext accepts any argument type
            type_class = "any"
        else:
            type_class = _TYPE_CLASSES[conv]
        if name is None:
            unnamed.extend(["integer"] * stars + [type_class])
        elif stars:
            raise _Invalid(f"a '*' width in the named placeholder %({name})")
        else:
            # "any" (%.0s) merges with the other uses' type, as in gettext
            known = named.get(name, "any")
            if "any" not in (known, type_class) and known != type_class:
                raise _Invalid(f"placeholder {name} used with two different types")
            named[name] = type_class if known == "any" else known
    if named and unnamed:
        raise _Invalid("named and unnamed placeholders mixed")
    return named, unnamed


def _brace_fields(text):
    """Parse *text* as a python-brace-format string, the way msgfmt does.

    Returns a dict mapping each replacement field, with its full
    ``.attribute`` / ``[index]`` chain, to the set of format specs it is
    used with. Auto-numbered ``{}`` fields are
    numbered by position, as str.format() does, without their chain: msgfmt
    is inconsistent about those (it accepts ``{.x}`` -> ``{.y}`` but not
    ``{}`` -> ``{.x}``), and being lenient there is safer than rejecting a
    valid translation. A lone ``}`` is literal.

    Also returns whether unnumbered ``{}`` fields are used.

    Raises:
        _Invalid: For an unterminated field, a ``!conversion`` (msgfmt doesn't
            support those), or numbered and unnumbered fields mixed.
    """
    fields = {}
    position = 0
    numbered = False
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
            if text.startswith("{{", end):
                end += 2  # literal braces inside a spec, not nesting
                continue
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                if depth == 0:
                    break
                depth -= 1
            end += 1
        else:
            raise _Invalid("an unterminated {field}")
        field, colon, spec = text[i + 1 : end].partition(":")
        if "!" in field:
            raise _Invalid(f"a {{{field}}} conversion, which gettext doesn't support")
        if not _BRACE_NAME_RE.fullmatch(field):
            raise _Invalid(f"an invalid field name {{{field}}}")
        if spec == "{{":
            pass  # a literal "{" as the whole spec
        elif "{" in spec:
            # One nested field as the whole spec ("{a:{w}}") is allowed
            if not _BRACE_NESTED_RE.fullmatch(spec):
                raise _Invalid(f"a nested {{{field}:{spec}}} gettext doesn't support")
        elif not _BRACE_SPEC_RE.fullmatch(spec):
            raise _Invalid(f"an invalid format spec in {{{field}:{spec}}}")
        # gettext 0.21 compares the directive text, so "{a}" and "{a:}" differ
        spec = colon + spec
        name = re.match(r"[^.\[]*", field).group()
        if name == "" and field:
            raise _Invalid(f"a {{{field}}} field without a name")
        if name == "":
            field = str(position)
            position += 1
        elif name.isdigit():
            numbered = True
        if position and numbered:
            raise _Invalid("both numbered {0} and unnumbered {} fields")
        fields.setdefault(field, set()).add(spec)
        i = end + 1
    return fields, position > 0


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
    changed = sorted(
        n
        for n in named
        if named[n] != source_named[n]
        # In plural forms msgfmt lets "%(n).0s" stand for any type
        and not (may_omit and "any" in (named[n], source_named[n]))
    )
    if changed:
        return f"placeholders with a different type: {', '.join(changed)}"
    if len(unnamed) != len(source_unnamed):
        return (
            f"{len(unnamed)} unnamed placeholders, the source has {len(source_unnamed)}"
        )
    if any(
        a != b and not (may_omit and "any" in (a, b))
        for a, b in zip(unnamed, source_unnamed, strict=True)
    ):
        return "unnamed placeholders with a different type or order"
    return None


def _check_brace(source, translation, may_omit):
    try:
        source_fields, source_auto = _brace_fields(source)
    except _Invalid:
        return None
    try:
        fields, auto = _brace_fields(translation)
    except _Invalid as e:
        return str(e)
    for field, specs in fields.items():
        for spec in specs - source_fields.get(field, set()):
            # A spec 1.0 rejects ("{a:.f}") only passes when the source has it;
            # spec is ":" + the spec text, or "" without a colon
            if "{" not in spec and not _BRACE_SPEC_1_0_RE.fullmatch(spec[1:]):
                return f"a format spec gettext 1.0 rejects in {{{field}{spec}}}"
    if auto and not source_auto:
        # gettext 0.21 rejects "{}" unless the source uses it too
        return "unnumbered {} fields, the source names or numbers them"
    extra = sorted(fields.keys() - source_fields.keys())
    if extra:
        return f"fields not in the source: {', '.join('{' + f + '}' for f in extra)}"
    missing = sorted(source_fields.keys() - fields.keys())
    if missing and not may_omit:
        return f"missing fields: {', '.join('{' + f + '}' for f in missing)}"
    # gettext 0.21 (Debian/Ubuntu) rejects any change to a field's format
    # spec ("{a:.2f}" -> "{a:.3f}", or adding/dropping one); 1.0 allows
    # some. Requiring the source's specs works with both. Plural forms
    # aren't compared this strictly by msgfmt.
    if may_omit or source_auto:
        # gettext 0.21 doesn't compare a source with unnumbered {} fields at
        # all, and 1.0 allows spec changes, so only names are compared then
        return None
    changed = sorted(
        field for field, specs in fields.items() if specs != source_fields[field]
    )
    if changed:
        return f"a changed format spec for {', '.join('{' + f + '}' for f in changed)}"
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
