"""Full integration tests for modeltranslation with real database operations."""

import pytest

from tests.models import Article, Product
from translatebot_django.backends.modeltranslation import ModeltranslationBackend


@pytest.mark.django_db
class TestModeltranslationBackendWithDB:
    """Test modeltranslation backend with real database operations."""

    def test_backend_get_all_registered_models(self):
        """Test getting all registered models."""
        backend = ModeltranslationBackend(target_lang="nl")
        models = backend.get_all_registered_models()

        # Should include our registered models
        model_names = [m.__name__ for m in models]
        assert "Article" in model_names
        assert "Product" in model_names

    def test_backend_get_translatable_fields(self):
        """Test getting translatable fields for a model."""
        backend = ModeltranslationBackend(target_lang="nl")
        fields = backend.get_translatable_fields(Article)

        assert "title" in fields
        assert "content" in fields
        assert "description" in fields

    def test_backend_parse_model_names_simple(self):
        """Test parsing model names without app label."""
        backend = ModeltranslationBackend(target_lang="nl")
        models = backend.parse_model_names(["Article"])

        assert len(models) == 1
        assert models[0] == Article

    def test_backend_parse_model_names_with_app_label(self):
        """Test parsing model names with app label."""
        backend = ModeltranslationBackend(target_lang="nl")
        models = backend.parse_model_names(["tests.Article", "tests.Product"])

        assert len(models) == 2
        assert Article in models
        assert Product in models

    def test_backend_parse_model_names_not_registered(self, mocker):
        """Test parsing model name that's not registered with modeltranslation."""
        backend = ModeltranslationBackend(target_lang="nl")

        # Mock a model class that exists but isn't registered

        # Temporarily make it look like User isn't registered
        with pytest.raises(ValueError, match="not registered with modeltranslation"):
            backend.parse_model_names(["auth.User"])

    def test_backend_gather_translatable_content_with_data(self):
        """Test gathering translatable content from models."""
        backend = ModeltranslationBackend(target_lang="nl")

        # Create test data with explicit empty target fields
        Article.objects.create(
            title="English Title",
            title_nl="",
            content="English Content",
            content_nl="",
            description="English Description",
            description_nl="",
        )

        items = backend.gather_translatable_content(
            model_list=[Article], only_empty=True
        )

        assert len(items) > 0
        assert items[0]["model"] == Article
        assert items[0]["field"] in ["title", "content", "description"]
        assert items[0]["target_field"].endswith("_nl")

    def test_backend_gather_translatable_content_overwrite(self):
        """Test gathering content with overwrite (not only_empty)."""
        backend = ModeltranslationBackend(target_lang="nl")

        # Create article with existing Dutch translation
        Article.objects.create(
            title="English Title",
            title_nl="Dutch Title",
            content="English Content",
            content_nl="",  # Empty target field
        )

        # With only_empty=True, should not include title (already translated)
        items_empty = backend.gather_translatable_content(
            model_list=[Article], only_empty=True
        )

        # Check if content is in the items (should be, as it's empty)
        content_fields = [item["field"] for item in items_empty]
        assert "content" in content_fields

        # With only_empty=False, should include all fields
        items_all = backend.gather_translatable_content(
            model_list=[Article], only_empty=False
        )

        # Should have items for both title and content
        assert len(items_all) >= len(items_empty)

    def test_backend_apply_translations(self):
        """Test applying translations to model instances."""
        backend = ModeltranslationBackend(target_lang="nl")

        # Create test article
        article = Article.objects.create(
            title="English Title", content="English Content"
        )

        # Prepare translation items
        translation_items = [
            {
                "instance": article,
                "target_field": "title_nl",
                "translation": "Dutch Title",
            },
            {
                "instance": article,
                "target_field": "content_nl",
                "translation": "Dutch Content",
            },
        ]

        # Apply translations
        updated = backend.apply_translations(translation_items, dry_run=False)

        # The function counts translated fields
        assert updated == 2

        # Verify translations were applied
        article.refresh_from_db()
        assert article.title_nl == "Dutch Title"
        assert article.content_nl == "Dutch Content"

    def test_backend_apply_translations_dry_run(self):
        """Test dry run doesn't actually save."""
        backend = ModeltranslationBackend(target_lang="nl")

        # Create test article
        article = Article.objects.create(
            title="English Title", content="English Content"
        )

        # Prepare translation items
        translation_items = [
            {
                "instance": article,
                "target_field": "title_nl",
                "translation": "Dutch Title",
            }
        ]

        # Apply with dry_run=True
        updated = backend.apply_translations(translation_items, dry_run=True)

        assert updated == 1  # Returns count

        # Verify translations were NOT applied
        article.refresh_from_db()
        assert not article.title_nl  # Should still be empty

    def test_backend_gather_translatable_content_with_empty_source(self):
        """Test content skips fields with empty source text after getattr."""
        backend = ModeltranslationBackend(target_lang="nl")

        # Create article with a field that might be None or falsy
        # We'll use description which might be nullable
        Article.objects.create(
            title="Test",
            content="Content",
            # description is left as default (empty string or None)
        )

        # Try to gather content
        items = backend.gather_translatable_content(
            model_list=[Article], only_empty=True
        )

        # All items should have non-empty source_text
        # This tests the double-check at lines 158-160
        assert all(item["source_text"] for item in items)

    def test_backend_gather_skips_instance_with_all_empty_source_fields(self, mocker):
        """Test that instances with no populated source fields are skipped."""
        backend = ModeltranslationBackend(target_lang="nl")

        # Create an article, then empty out every source of content in memory:
        # all language fields and the original columns
        article = Article.objects.create(title="tmp", content="tmp")
        for field in ("title", "content", "description"):
            for lang in ("en", "de"):
                setattr(article, f"{field}_{lang}", None)
            article.__dict__[field] = None

        # Fake queryset that yields our empty article no matter how the
        # chain is composed, simulating a TOCTOU where data changed after
        # the queryset filter
        class FakeQuerySet:
            def rewrite(self, mode=True):
                return self

            def filter(self, *args, **kwargs):
                return self

            def __iter__(self):
                return iter([article])

        mocker.patch.object(Article.objects, "all", return_value=FakeQuerySet())

        items = backend.gather_translatable_content(
            model_list=[Article], only_empty=False
        )
        assert len(items) == 0

    def test_backend_gather_handles_queryset_without_rewrite(self, mocker):
        """A custom manager may return a plain QuerySet without
        modeltranslation's rewrite(); gathering must still work."""
        backend = ModeltranslationBackend(target_lang="nl")

        article = Article.objects.create(
            title="Plain QS Title", content="Plain QS Content"
        )

        # Fake queryset lacking rewrite(), as returned by a custom manager
        # whose get_queryset() doesn't use MultilingualQuerySet
        class FakeQuerySet:
            def filter(self, *args, **kwargs):
                return self

            def __iter__(self):
                return iter([article])

        mocker.patch.object(Article.objects, "all", return_value=FakeQuerySet())

        items = backend.gather_translatable_content(
            model_list=[Article], only_empty=False
        )

        source_texts = {item["source_text"] for item in items}
        assert "Plain QS Title" in source_texts
        assert "Plain QS Content" in source_texts

    def test_backend_gather_finds_legacy_rows_original_column_only(self):
        """Regression test for #251: rows whose content lives only in the
        original column (e.g. data predating modeltranslation, never passed
        through update_translation_fields) must still be discovered."""
        backend = ModeltranslationBackend(target_lang="nl")

        article = Article.objects.create(title="Legacy Title", content="Legacy Content")
        # Simulate legacy data: language columns empty, content only in the
        # original columns (queryset.update() bypasses the descriptor sync)
        Article.objects.filter(pk=article.pk).update(
            title_en=None, title_de=None, content_en=None, content_de=None
        )

        items = backend.gather_translatable_content(
            model_list=[Article], only_empty=True
        )

        by_field = {item["field"]: item for item in items}
        assert by_field["title"]["source_text"] == "Legacy Title"
        assert by_field["content"]["source_text"] == "Legacy Content"
        # Legacy rows are marked for default-language backfill on save
        assert by_field["title"]["backfill_field"] == "title_en"
        assert by_field["title"]["backfill_value"] == "Legacy Title"

    def test_backend_original_column_fallback_skips_file_fields(self):
        """The original column of a FileField holds a raw path string; it
        must never be shipped to a translation provider (which would write
        prose into file columns). Text fields still fall back."""
        from tests.models import Document

        backend = ModeltranslationBackend(target_lang="nl")

        doc = Document.objects.create(name="Manual", attachment="docs/manual.pdf")
        # Simulate legacy data: language columns empty, values only in the
        # original columns
        Document.objects.filter(pk=doc.pk).update(
            name_en=None,
            name_de=None,
            attachment_en=None,
            attachment_de=None,
        )

        items = backend.gather_translatable_content(
            model_list=[Document], only_empty=True
        )

        fields = {item["field"] for item in items}
        assert "name" in fields  # text falls back to the original column
        assert "attachment" not in fields  # file path is never a source text

    def test_backend_apply_translations_backfills_default_language(self):
        """Applying a translation sourced from the original column also syncs
        the default-language column, like update_translation_fields would."""
        backend = ModeltranslationBackend(target_lang="nl")

        article = Article.objects.create(title="Legacy Title", content="c")
        Article.objects.filter(pk=article.pk).update(title_en=None)

        items = backend.gather_translatable_content(
            model_list=[Article], only_empty=True
        )
        title_item = next(i for i in items if i["field"] == "title")

        backend.apply_translations(
            [
                {
                    "instance": title_item["instance"],
                    "target_field": title_item["target_field"],
                    "translation": "NL Titel",
                    "backfill_field": title_item["backfill_field"],
                    "backfill_value": title_item["backfill_value"],
                }
            ],
            dry_run=False,
        )

        article.refresh_from_db()
        assert article.title_nl == "NL Titel"
        assert article.title_en == "Legacy Title"  # backfilled

    def test_backend_gather_target_is_default_language(self):
        """When translating INTO the default language, rows whose original
        column already holds the (default-language) value are skipped instead
        of being relay-translated from another language column; rows without
        it are translated from the other languages."""
        backend = ModeltranslationBackend(target_lang="en")

        # Row 1: pristine English in the original column, Dutch machine
        # translation present. Must NOT be re-generated from Dutch.
        legacy = Article.objects.create(title="tmp", content="c")
        Article.objects.filter(pk=legacy.pk).rewrite(False).update(
            title="Pristine English", title_en=None, title_nl="Machine Dutch"
        )

        # Row 2: no English anywhere, Dutch content only. Should be
        # translated from Dutch.
        dutch_only = Article.objects.create(title="tmp2", content="c")
        Article.objects.filter(pk=dutch_only.pk).rewrite(False).update(
            title="", title_en=None, title_nl="Alleen Nederlands"
        )

        items = backend.gather_translatable_content(
            model_list=[Article], only_empty=True
        )

        title_items = {i["instance"].pk: i for i in items if i["field"] == "title"}
        assert legacy.pk not in title_items
        assert title_items[dutch_only.pk]["source_text"] == "Alleen Nederlands"

    def test_backend_original_column_used_at_default_language_position(self):
        """When the default-language column is empty, the original column is
        used in its place (mirroring update_translation_fields), so the
        default-language original outranks other languages' columns — which
        may themselves be machine translations."""
        backend = ModeltranslationBackend(target_lang="nl")

        article = Article.objects.create(title="Original English", content="c")
        Article.objects.filter(pk=article.pk).update(
            title_en=None, title_de="Deutscher Titel"
        )

        items = backend.gather_translatable_content(
            model_list=[Article], only_empty=True
        )

        title_items = [i for i in items if i["field"] == "title"]
        assert title_items[0]["source_text"] == "Original English"
        assert title_items[0]["backfill_field"] == "title_en"
        assert title_items[0]["backfill_value"] == "Original English"

    def test_backend_apply_translations_multiple_fields_same_row(self):
        """Regression test for #251: gather returns a separate instance copy
        per field for the same row; applying them together must not let one
        copy's stale values clobber another copy's translation."""
        backend = ModeltranslationBackend(target_lang="nl")

        article = Article.objects.create(
            title="Hello", content="World", description="Desc"
        )

        items = backend.gather_translatable_content(
            model_list=[Article], only_empty=True
        )
        assert len(items) == 3

        translation_items = [
            {
                "instance": item["instance"],
                "target_field": item["target_field"],
                "translation": f"NL {item['source_text']}",
            }
            for item in items
        ]
        updated = backend.apply_translations(translation_items, dry_run=False)
        assert updated == 3

        article.refresh_from_db()
        assert article.title_nl == "NL Hello"
        assert article.content_nl == "NL World"
        assert article.description_nl == "NL Desc"

    def test_backend_apply_translations_preserves_other_rows_fields(self):
        """A bulk update for one row's fields must not write another row's
        stale values for fields it wasn't translated for (e.g. a field
        already translated in an earlier batch)."""
        backend = ModeltranslationBackend(target_lang="nl")

        a = Article.objects.create(title="A title", content="A content")
        b = Article.objects.create(title="B title", content="B content")

        # Instance copies as gather_translatable_content would produce them
        a_copy = Article.objects.get(pk=a.pk)
        b_copy = Article.objects.get(pk=b.pk)

        # Simulate an earlier batch having already translated a.content_nl
        Article.objects.filter(pk=a.pk).update(content_nl="Earlier translation")

        backend.apply_translations(
            [
                {
                    "instance": a_copy,
                    "target_field": "title_nl",
                    "translation": "NL A title",
                },
                {
                    "instance": b_copy,
                    "target_field": "content_nl",
                    "translation": "NL B content",
                },
            ],
            dry_run=False,
        )

        a.refresh_from_db()
        b.refresh_from_db()
        assert a.title_nl == "NL A title"
        assert a.content_nl == "Earlier translation"  # not clobbered
        assert b.content_nl == "NL B content"

    def test_backend_gather_skips_empty_first_source_lang(self):
        """Test that gather falls through to second source lang when first is empty."""
        # target_lang="nl", so source_langs = ["en", "de"]
        backend = ModeltranslationBackend(target_lang="nl")

        # Create article with en (language column AND original column, which
        # stands in for the default language) empty but de populated.
        # rewrite(False) keeps modeltranslation from redirecting the plain
        # field names to the active language.
        article = Article.objects.create(title="placeholder", content="placeholder")
        Article.objects.filter(pk=article.pk).rewrite(False).update(
            title="",
            title_en="",
            title_de="Deutscher Titel",
            title_nl="",
            content="",
            content_en="",
            content_de="Deutscher Inhalt",
            content_nl="",
        )

        items = backend.gather_translatable_content(
            model_list=[Article], only_empty=True
        )

        # Should find items using the German source text
        source_texts = [item["source_text"] for item in items]
        assert "Deutscher Titel" in source_texts
        assert "Deutscher Inhalt" in source_texts
