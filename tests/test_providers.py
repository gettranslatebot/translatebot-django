"""Tests for translation providers."""

import sys
from unittest.mock import MagicMock

import pytest

from django.core.management.base import CommandError

# --- Factory tests ---


def test_get_provider_default_is_litellm(settings, monkeypatch):
    """Default provider (no setting) returns LiteLLMProvider."""
    monkeypatch.setenv("TRANSLATEBOT_API_KEY", "test-key")
    if hasattr(settings, "TRANSLATEBOT_PROVIDER"):
        delattr(settings, "TRANSLATEBOT_PROVIDER")
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    from translatebot_django.providers import get_provider
    from translatebot_django.providers.litellm import LiteLLMProvider

    provider = get_provider("test-key")
    assert isinstance(provider, LiteLLMProvider)


def test_get_provider_explicit_litellm(settings):
    """Explicit litellm setting returns LiteLLMProvider."""
    settings.TRANSLATEBOT_PROVIDER = "litellm"
    settings.TRANSLATEBOT_MODEL = "gpt-4o-mini"

    from translatebot_django.providers import get_provider
    from translatebot_django.providers.litellm import LiteLLMProvider

    provider = get_provider("test-key")
    assert isinstance(provider, LiteLLMProvider)


def test_get_provider_deepl(settings):
    """DeepL setting returns DeepLProvider."""
    settings.TRANSLATEBOT_PROVIDER = "deepl"

    from translatebot_django.providers import get_provider
    from translatebot_django.providers.deepl import DeepLProvider

    provider = get_provider("test-key")
    assert isinstance(provider, DeepLProvider)


def test_get_provider_unknown_raises_error(settings):
    """Unknown provider name raises CommandError."""
    settings.TRANSLATEBOT_PROVIDER = "unknown_provider"

    from translatebot_django.providers import get_provider

    with pytest.raises(CommandError, match="Unknown translation provider"):
        get_provider("test-key")


# --- LiteLLM provider property tests ---


def test_litellm_provider_name(settings):
    """LiteLLMProvider.name returns the model name."""
    settings.TRANSLATEBOT_MODEL = "claude-3-sonnet"

    from translatebot_django.providers.litellm import LiteLLMProvider

    provider = LiteLLMProvider(model="claude-3-sonnet", api_key="test-key")
    assert provider.name == "claude-3-sonnet"


def test_litellm_provider_supports_context():
    """LiteLLMProvider supports TRANSLATING.md context."""
    from translatebot_django.providers.litellm import LiteLLMProvider

    provider = LiteLLMProvider(model="gpt-4o-mini", api_key="test-key")
    assert provider.supports_context is True


# --- DeepL provider property tests ---


def test_deepl_provider_name():
    """DeepLProvider.name returns 'DeepL'."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    assert provider.name == "DeepL"


def test_deepl_provider_supports_context():
    """DeepLProvider does not support TRANSLATING.md context."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    assert provider.supports_context is False


# --- DeepL language code mapping tests ---


def test_deepl_lang_mapping_simple():
    """Simple language codes are uppercased."""
    from translatebot_django.providers.deepl import django_to_deepl_target

    assert django_to_deepl_target("de") == "DE"
    assert django_to_deepl_target("fr") == "FR"
    assert django_to_deepl_target("nl") == "NL"
    assert django_to_deepl_target("ja") == "JA"


def test_deepl_lang_mapping_english_default():
    """'en' maps to 'EN-US' (DeepL requires a regional variant)."""
    from translatebot_django.providers.deepl import django_to_deepl_target

    assert django_to_deepl_target("en") == "EN-US"


def test_deepl_lang_mapping_portuguese_default():
    """'pt' maps to 'PT-BR' (DeepL requires a regional variant)."""
    from translatebot_django.providers.deepl import django_to_deepl_target

    assert django_to_deepl_target("pt") == "PT-BR"


def test_deepl_lang_mapping_regional_variant():
    """Regional variants like 'pt-br' are uppercased as-is."""
    from translatebot_django.providers.deepl import django_to_deepl_target

    assert django_to_deepl_target("pt-br") == "PT-BR"
    assert django_to_deepl_target("zh-hans") == "ZH-HANS"
    assert django_to_deepl_target("en-gb") == "EN-GB"


# --- DeepL batching tests ---


def test_deepl_batch_under_limit():
    """Texts under both limits stay in a single batch."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    texts = ["Hello", "World", "Test"]
    batches = provider.batch(texts, "de")
    assert batches == [["Hello", "World", "Test"]]


def test_deepl_batch_over_50_texts():
    """More than 50 texts are split into multiple batches."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    texts = [f"String {i}" for i in range(120)]
    batches = provider.batch(texts, "de")

    assert len(batches) == 3  # 50 + 50 + 20
    assert len(batches[0]) == 50
    assert len(batches[1]) == 50
    assert len(batches[2]) == 20

    # All texts accounted for
    flat = [t for batch in batches for t in batch]
    assert flat == texts


def test_deepl_batch_large_texts():
    """Texts exceeding 128KB are split by size."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    # Each text is ~10KB, so 128KB limit fits ~12 texts
    large_text = "A" * 10_000
    texts = [large_text] * 20
    batches = provider.batch(texts, "de")

    assert len(batches) > 1
    # Each batch should be at most 128KB
    for batch in batches:
        total_size = sum(len(t.encode("utf-8")) for t in batch)
        assert total_size <= 128 * 1024


def test_deepl_batch_empty():
    """Empty input returns empty list."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    batches = provider.batch([], "de")
    assert batches == []


def test_deepl_batch_single_oversized_text():
    """A single text larger than 128KB goes into its own batch."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    huge_text = "B" * (200 * 1024)  # 200KB
    texts = ["small", huge_text, "also small"]
    batches = provider.batch(texts, "de")

    # "small" in first batch, huge_text overflows to second, "also small" in third
    assert len(batches) == 3
    assert batches[0] == ["small"]
    assert batches[1] == [huge_text]
    assert batches[2] == ["also small"]


# --- DeepL translate tests ---


def test_deepl_translate_basic(mocker):
    """DeepLProvider.translate returns translated texts."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset()

    mock_result_1 = MagicMock()
    mock_result_1.text = "Hallo"
    mock_result_2 = MagicMock()
    mock_result_2.text = "Welt"

    provider._translator.translate_text = MagicMock(
        return_value=[mock_result_1, mock_result_2]
    )

    result = provider.translate(["Hello", "World"], "de")
    assert result == ["Hallo", "Welt"]

    provider._translator.translate_text.assert_called_once_with(
        ["Hello", "World"],
        target_lang="DE",
        preserve_formatting=True,
    )


def test_deepl_translate_strips_trailing_dot():
    """Strip trailing dot added by DeepL when source has none."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset()

    sources = ["Hello", "World", "Done.", ".", "OK", ""]
    raw_translations = ["Hallo.", "Welt.", "Fertig.", ".", "Gut", ""]

    mock_results = []
    for text in raw_translations:
        m = MagicMock()
        m.text = text
        mock_results.append(m)

    provider._translator.translate_text = MagicMock(return_value=mock_results)

    result = provider.translate(sources, "de")
    assert result == [
        "Hallo",  # source no dot, translation dot -> stripped
        "Welt",  # source no dot, translation dot -> stripped
        "Fertig.",  # source has dot, translation dot -> kept
        ".",  # source is just a dot -> kept
        "Gut",  # neither has dot -> no change
        "",  # empty strings -> no change
    ]


def test_deepl_translate_uses_mapped_lang(mocker):
    """DeepLProvider.translate converts language codes for the API."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset()

    mock_result = MagicMock()
    mock_result.text = "Hello"

    provider._translator.translate_text = MagicMock(return_value=[mock_result])

    provider.translate(["Hi"], "en")

    provider._translator.translate_text.assert_called_once_with(
        ["Hi"],
        target_lang="EN-US",
        preserve_formatting=True,
    )


# --- DeepL error handling tests ---


def test_deepl_translate_auth_error():
    """DeepL auth error raises CommandError."""
    import deepl

    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="bad-key")
    provider._no_formality_langs = frozenset()
    provider._translator.translate_text = MagicMock(
        side_effect=deepl.AuthorizationException("Invalid auth key")
    )

    with pytest.raises(CommandError, match="DeepL authentication failed"):
        provider.translate(["Hello"], "de")


def test_deepl_translate_quota_error():
    """DeepL quota error raises CommandError."""
    import deepl

    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset()
    provider._translator.translate_text = MagicMock(
        side_effect=deepl.QuotaExceededException("Quota exceeded")
    )

    with pytest.raises(CommandError, match="DeepL character quota exceeded"):
        provider.translate(["Hello"], "de")


def test_deepl_translate_rate_limit_error():
    """DeepL rate limit error raises CommandError."""
    import deepl

    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset()
    provider._translator.translate_text = MagicMock(
        side_effect=deepl.TooManyRequestsException("Too many requests")
    )

    with pytest.raises(CommandError, match="DeepL rate limit exceeded"):
        provider.translate(["Hello"], "de")


def test_deepl_translate_generic_error():
    """Generic DeepL API error raises CommandError."""
    import deepl

    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset()
    provider._translator.translate_text = MagicMock(
        side_effect=deepl.DeepLException("Something went wrong")
    )

    with pytest.raises(CommandError, match="DeepL API error"):
        provider.translate(["Hello"], "de")


# --- DeepL placeholder protection tests ---


def _w(ph):
    """Wrap a placeholder in <x> tags (test helper)."""
    return f"<x>{ph}</x>"


@pytest.mark.parametrize(
    "text, expected",
    [
        # %(name)s style
        ("%(country)s", _w("%(country)s")),
        ("%(count)d items", f"{_w('%(count)d')} items"),
        # %s / %d style
        ("%s items", f"{_w('%s')} items"),
        ("%d remaining", f"{_w('%d')} remaining"),
        # %% literal percent
        ("100%%", f"100{_w('%%')}"),
        # {name} / {0} / {} style
        ("{country}", _w("{country}")),
        ("{0} items", f"{_w('{0}')} items"),
        ("{} left", f"{_w('{}')} left"),
        # Format specs
        ("{0:.2f}", _w("{0:.2f}")),
        ("{name!r}", _w("{name!r}")),
        # Multiple placeholders
        (
            "Available in %(country)s from %(start_date)s",
            f"Available in {_w('%(country)s')} from {_w('%(start_date)s')}",
        ),
        # Double braces (str.format literal braces) – NOT wrapped
        ("{{escaped}}", "{{escaped}}"),
        ("{{count}} items", "{{count}} items"),
        # Arbitrary brace content – NOT wrapped
        ("{ color: red }", "{ color: red }"),
        # No placeholders – unchanged
        ("Hello world", "Hello world"),
        ("", ""),
    ],
)
def test_wrap_placeholders(text, expected):
    """_wrap_placeholders wraps format strings in <x> tags."""
    from translatebot_django.providers.deepl import _wrap_placeholders

    assert _wrap_placeholders(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        (_w("%(country)s"), "%(country)s"),
        (f"Dostupno u {_w('%(country)s')}", "Dostupno u %(country)s"),
        ("no tags here", "no tags here"),
        ("", ""),
    ],
)
def test_unwrap_placeholders(text, expected):
    """_unwrap_placeholders strips <x> tag wrappers."""
    from translatebot_django.providers.deepl import _unwrap_placeholders

    assert _unwrap_placeholders(text) == expected


def test_deepl_translate_protects_placeholders():
    """Placeholders are wrapped before sending and unwrapped after."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset()

    mock_result = MagicMock()
    mock_result.text = f"Verfügbar in {_w('%(country)s')} ab {_w('%(start_date)s')}"

    provider._translator.translate_text = MagicMock(return_value=[mock_result])

    result = provider.translate(["Available in %(country)s from %(start_date)s"], "de")
    assert result == ["Verfügbar in %(country)s ab %(start_date)s"]

    # Verify wrapped text was sent to the API
    sent_texts = provider._translator.translate_text.call_args[0][0]
    assert sent_texts == [
        f"Available in {_w('%(country)s')} from {_w('%(start_date)s')}"
    ]


def test_deepl_translate_preserves_html_with_placeholders():
    """HTML tags in source text are preserved through translation (regression test)."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset()

    source = 'Click <a href="/shop">%(name)s</a> or <br> visit <b>us</b>'

    mock_result = MagicMock()
    # Without tag_handling, DeepL treats HTML as plain text and preserves it
    mock_result.text = (
        f'Klicken Sie auf <a href="/shop">{_w("%(name)s")}</a>'
        " oder <br> besuchen Sie <b>uns</b>"
    )

    provider._translator.translate_text = MagicMock(return_value=[mock_result])

    result = provider.translate([source], "de")
    assert result == [
        'Klicken Sie auf <a href="/shop">%(name)s</a> oder <br> besuchen Sie <b>uns</b>'
    ]

    # Verify no tag_handling mode is used
    call_kwargs = provider._translator.translate_text.call_args[1]
    assert "tag_handling" not in call_kwargs
    assert "ignore_tags" not in call_kwargs


def test_deepl_translate_no_placeholders_unchanged():
    """Texts without placeholders pass through normally."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset()

    mock_result = MagicMock()
    mock_result.text = "Hallo Welt"

    provider._translator.translate_text = MagicMock(return_value=[mock_result])

    result = provider.translate(["Hello World"], "de")
    assert result == ["Hallo Welt"]

    sent_texts = provider._translator.translate_text.call_args[0][0]
    assert sent_texts == ["Hello World"]


# --- DeepL email placeholder tests (affected languages) ---


@pytest.mark.parametrize(
    "text, expected_replaced, expected_originals",
    [
        ("%(country)s", "ph0@tb.x", ["%(country)s"]),
        (
            "Available in %(country)s from %(start_date)s",
            "Available in ph0@tb.x from ph1@tb.x",
            ["%(country)s", "%(start_date)s"],
        ),
        (
            "{name} has {count} items",
            "ph0@tb.x has ph1@tb.x items",
            ["{name}", "{count}"],
        ),
        ("%s items", "ph0@tb.x items", ["%s"]),
        ("No placeholders", "No placeholders", []),
        ("", "", []),
    ],
)
def test_replace_placeholders_with_emails(text, expected_replaced, expected_originals):
    """_replace_placeholders_with_emails swaps placeholders for email tokens."""
    from translatebot_django.providers.deepl import _replace_placeholders_with_emails

    replaced, originals = _replace_placeholders_with_emails(text)
    assert replaced == expected_replaced
    assert originals == expected_originals


@pytest.mark.parametrize(
    "text, originals, expected",
    [
        ("ph0@tb.x", ["%(country)s"], "%(country)s"),
        (
            "Dostupno u ph0@tb.x od ph1@tb.x",
            ["%(country)s", "%(start_date)s"],
            "Dostupno u %(country)s od %(start_date)s",
        ),
        ("no tokens here", [], "no tokens here"),
        ("", [], ""),
        # Out-of-range index left as-is
        ("ph5@tb.x", ["%(name)s"], "ph5@tb.x"),
    ],
)
def test_restore_email_placeholders(text, originals, expected):
    """_restore_email_placeholders swaps email tokens back to originals."""
    from translatebot_django.providers.deepl import _restore_email_placeholders

    assert _restore_email_placeholders(text, originals) == expected


@pytest.mark.parametrize("lang", ["hr", "sr", "bs", "sq", "mk"])
def test_deepl_translate_affected_lang_uses_email_placeholders(lang):
    """Affected languages use email-shaped placeholders and tag_handling=html."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset({"HR", "SR", "BS", "SQ", "MK"})

    mock_result = MagicMock()
    mock_result.text = "Dostupno u ph0@tb.x od ph1@tb.x"

    provider._translator.translate_text = MagicMock(return_value=[mock_result])

    result = provider.translate(["Available in %(country)s from %(start_date)s"], lang)
    assert result == ["Dostupno u %(country)s od %(start_date)s"]

    # Verify email tokens were sent, not <x> tags
    sent_texts = provider._translator.translate_text.call_args[0][0]
    assert sent_texts == ["Available in ph0@tb.x from ph1@tb.x"]

    # Verify tag_handling="html" is used for affected languages
    call_kwargs = provider._translator.translate_text.call_args[1]
    assert call_kwargs["tag_handling"] == "html"


@pytest.mark.parametrize("lang", ["de", "fr", "it", "nl", "ja"])
def test_deepl_translate_normal_lang_uses_x_tags(lang):
    """Non-affected languages still use the <x> tag approach."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset()

    mock_result = MagicMock()
    mock_result.text = f"Translated {_w('%(name)s')}"

    provider._translator.translate_text = MagicMock(return_value=[mock_result])

    result = provider.translate(["Hello %(name)s"], lang)
    assert result == ["Translated %(name)s"]

    sent_texts = provider._translator.translate_text.call_args[0][0]
    assert sent_texts == [f"Hello {_w('%(name)s')}"]


def test_deepl_translate_affected_lang_no_placeholders():
    """Affected languages with no placeholders pass through normally."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset({"HR", "SR", "BS", "SQ", "MK"})

    mock_result = MagicMock()
    mock_result.text = "Zdravo svijete"

    provider._translator.translate_text = MagicMock(return_value=[mock_result])

    result = provider.translate(["Hello World"], "hr")
    assert result == ["Zdravo svijete"]

    sent_texts = provider._translator.translate_text.call_args[0][0]
    assert sent_texts == ["Hello World"]


def test_deepl_translate_affected_lang_preserves_html():
    """Affected languages preserve HTML tags via tag_handling=html and unescape entities."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset({"HR", "SR", "BS", "SQ", "MK"})

    source = 'Click <a href="/shop">%(name)s</a> & visit <b>us</b>'

    mock_result = MagicMock()
    # With tag_handling="html", DeepL preserves HTML tags but encodes & as &amp;
    mock_result.text = 'Kliknite <a href="/shop">ph0@tb.x</a> &amp; posjetite <b>nas</b>'

    provider._translator.translate_text = MagicMock(return_value=[mock_result])

    result = provider.translate([source], "hr")
    # html.unescape() decodes &amp; back to &
    assert result == ['Kliknite <a href="/shop">%(name)s</a> & posjetite <b>nas</b>']


# --- DeepL formality detection tests ---


def test_deepl_lacks_formality_support_queries_api():
    """_lacks_formality_support queries get_target_languages and caches result."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")

    mock_lang_de = MagicMock()
    mock_lang_de.code = "DE"
    mock_lang_de.supports_formality = True
    mock_lang_hr = MagicMock()
    mock_lang_hr.code = "HR"
    mock_lang_hr.supports_formality = False

    provider._translator.get_target_languages = MagicMock(
        return_value=[mock_lang_de, mock_lang_hr]
    )

    assert provider._lacks_formality_support("HR") is True
    assert provider._lacks_formality_support("DE") is False

    # API should only be called once (cached)
    provider._translator.get_target_languages.assert_called_once()


def test_deepl_lacks_formality_support_api_error_fallback():
    """_lacks_formality_support falls back to empty set on API error."""
    import deepl

    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._translator.get_target_languages = MagicMock(
        side_effect=deepl.DeepLException("API error")
    )

    # On error, falls back to empty set (all languages use <x> tags)
    assert provider._lacks_formality_support("HR") is False
    assert provider._lacks_formality_support("DE") is False


def test_deepl_translate_affected_lang_unescapes_html_entities():
    """html.unescape() decodes entities produced by tag_handling=html."""
    from translatebot_django.providers.deepl import DeepLProvider

    provider = DeepLProvider(api_key="test-key")
    provider._no_formality_langs = frozenset({"HR"})

    mock_result = MagicMock()
    # DeepL with tag_handling="html" encodes < > & as entities
    mock_result.text = "Koristite operatore &lt; ili &gt; (npr. &lt;100 ili &gt;50)"

    provider._translator.translate_text = MagicMock(return_value=[mock_result])

    result = provider.translate(
        ["Use < or > operators (e.g. <100 or >50)"], "hr"
    )
    assert result == ["Koristite operatore < ili > (npr. <100 ili >50)"]


# --- DeepL import guard test ---


def test_deepl_import_guard_raises_error(monkeypatch):
    """Missing deepl package raises CommandError with install instructions."""
    # Temporarily remove deepl from sys.modules
    original = sys.modules.pop("deepl", None)
    # Also remove our provider module so it re-imports
    sys.modules.pop("translatebot_django.providers.deepl", None)

    import builtins

    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "deepl":
            raise ImportError("No module named 'deepl'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    try:
        from translatebot_django.providers.deepl import _get_deepl_module

        with pytest.raises(CommandError, match="pip install translatebot-django"):
            _get_deepl_module()
    finally:
        # Restore
        if original:
            sys.modules["deepl"] = original
        sys.modules.pop("translatebot_django.providers.deepl", None)
