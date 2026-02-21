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

        # The function counts instances in the list, which includes the same
        # instance twice
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

        # Create an article, then null out all source language fields
        article = Article.objects.create(title="tmp", content="tmp")
        Article.objects.filter(pk=article.pk).update(
            title_en=None,
            title_de=None,
            content_en=None,
            content_de=None,
            description_en=None,
            description_de=None,
        )
        article.refresh_from_db()

        # Mock filter to return our empty article, simulating a TOCTOU
        # where data changed after the queryset filter
        mocker.patch.object(Article.objects, "filter", return_value=[article])

        items = backend.gather_translatable_content(
            model_list=[Article], only_empty=False
        )
        assert len(items) == 0

    def test_backend_gather_skips_empty_first_source_lang(self):
        """Test that gather falls through to second source lang when first is empty."""
        # target_lang="nl", so source_langs = ["en", "de"]
        backend = ModeltranslationBackend(target_lang="nl")

        # Create article with en empty but de populated
        article = Article.objects.create(title="placeholder", content="placeholder")
        Article.objects.filter(pk=article.pk).update(
            title_en="",
            title_de="Deutscher Titel",
            title_nl="",
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
