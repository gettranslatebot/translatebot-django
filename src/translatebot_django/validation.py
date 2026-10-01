"""Checks that a provider's translation of a PO message is safe to write.

They mirror what ``msgfmt --check-format`` (run by ``compilemessages``)
rejects for ``python-format`` and ``python-brace-format`` entries, so a
translation that would break compilation is caught before it is written,
and add a check for degenerate output (index markers instead of text).
"""

import re

PYTHON_FORMAT = "python-format"
PYTHON_BRACE_FORMAT = "python-brace-format"
FORMAT_FLAGS = frozenset({PYTHON_FORMAT, PYTHON_BRACE_FORMAT})

# A printf-style conversion: %s, %(name)s, %-5.2f, %%, ...
_PERCENT_SPEC_RE = re.compile(
    r"%(?:\((?P<name>[^)]*)\))?[#0\- +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?"
    r"(?P<conv>[diouxXeEfFgGcrsa%])"
)

# A str.format() replacement field: {}, {0}, {name}, {name!r:>10}. Doubled
# braces are literal and removed before matching.
_BRACE_FIELD_RE = re.compile(r"\{([^{}!:]*)(?:![rsa])?(?::[^{}]*)?\}")

# What a model sometimes returns instead of a translation: "#1", "#2", ...
_INDEX_MARKER_RE = re.compile(r"^\s*#\d+\s*$")


def _percent_specs(text):
    """Return (named, unnamed_count, has_stray_percent) for *text*."""
    named = []
    unnamed = 0
    for match in _PERCENT_SPEC_RE.finditer(text):
        if match.group("conv") == "%":
            continue
        if match.group("name") is not None:
            named.append(match.group("name"))
        else:
            unnamed += 1
    stray = "%" in _PERCENT_SPEC_RE.sub("", text)
    return named, unnamed, stray


def _brace_fields(text):
    """Return the replacement field names in *text* ("" for ``{}``)."""
    return _BRACE_FIELD_RE.findall(text.replace("{{", "").replace("}}", ""))


def _check_percent(sources, translation, plural):
    named, unnamed, stray = _percent_specs(translation)
    if stray:
        return "a lone '%' (a literal percent sign must be written as '%%')"
    source_specs = [_percent_specs(s) for s in sources]
    source_named = {n for names, _, _ in source_specs for n in names}
    extra = sorted(set(named) - source_named)
    if extra:
        return f"placeholders not in the source: {', '.join(extra)}"
    source_unnamed = max(count for _, count, _ in source_specs)
    if plural:
        # A plural form may leave a placeholder out ("one file"), but can't
        # need more arguments than the source supplies
        if unnamed > source_unnamed:
            return f"{unnamed} unnamed placeholders, the source has {source_unnamed}"
        return None
    missing = sorted(source_named - set(named))
    if missing:
        return f"missing placeholders: {', '.join(missing)}"
    if unnamed != source_unnamed:
        return f"{unnamed} unnamed placeholders, the source has {source_unnamed}"
    return None


def _check_brace(sources, translation, plural):
    fields = set(_brace_fields(translation))
    source_fields = {f for s in sources for f in _brace_fields(s)}
    extra = sorted(fields - source_fields)
    if extra:
        return f"fields not in the source: {', '.join('{' + f + '}' for f in extra)}"
    if not plural:
        missing = sorted(source_fields - fields)
        if missing:
            return f"missing fields: {', '.join('{' + f + '}' for f in missing)}"
    return None


def translation_problem(sources, translation, formats=frozenset(), plural=False):
    """Describe why *translation* can't be written, or return None if it can.

    Args:
        sources: The source string(s) the translation was made from: the
            msgid, or for a plural form both the msgid and msgid_plural.
        translation: One translated string (one plural form).
        formats: The entry's format flags (``python-format``,
            ``python-brace-format``); placeholders are only checked when
            flagged, since an unflagged ``%`` is plain text.
        plural: Whether *translation* is a plural form, which may leave
            placeholders out.
    """
    if not isinstance(translation, str):
        return "not a string"
    if _INDEX_MARKER_RE.match(translation) and not any(
        _INDEX_MARKER_RE.match(s) for s in sources
    ):
        return f"an index marker ({translation.strip()!r}) instead of a translation"
    if any(s.strip() for s in sources) and not translation.strip():
        return "empty"
    if PYTHON_FORMAT in formats:
        problem = _check_percent(sources, translation, plural)
        if problem:
            return problem
    if PYTHON_BRACE_FORMAT in formats:
        problem = _check_brace(sources, translation, plural)
        if problem:
            return problem
    return None
