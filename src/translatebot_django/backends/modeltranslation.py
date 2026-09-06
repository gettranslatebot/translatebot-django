"""Backend for django-modeltranslation model field translation."""

from collections import defaultdict

from django.apps import apps
from django.db import transaction


class ModeltranslationBackend:
    """Backend for translating django-modeltranslation model fields."""

    def __init__(self, target_lang):
        """
        Initialize the modeltranslation backend.

        Args:
            target_lang: Target language code (e.g., 'de', 'nl', 'fr')
        """
        from modeltranslation import settings as mt_settings
        from modeltranslation.translator import translator
        from modeltranslation.utils import build_localized_fieldname

        self.translator = translator
        self.target_lang = target_lang
        # modeltranslation stores the default-language value in the original
        # column; the <field>_<default_lang> column is only a mirror that gets
        # synced on descriptor-mediated saves. Rows created before
        # modeltranslation was installed (or written via update()/imports)
        # have their default-language content only in the original column.
        self.default_lang = mt_settings.DEFAULT_LANGUAGE
        # Use modeltranslation's own (import-time) language list: these are
        # the languages it actually created columns for. Reading Django
        # settings live could diverge from that and reference columns that
        # don't exist.
        self.available_langs = list(mt_settings.AVAILABLE_LANGUAGES)
        # Maps e.g. ('title', 'en-gb') -> 'title_en_gb'; a raw f-string
        # would produce invalid lookups for hyphenated language codes.
        self._localized_fieldname = build_localized_fieldname

    def get_all_registered_models(self):
        """
        Get all models registered with modeltranslation.

        Returns:
            list: List of Django model classes
        """
        return list(self.translator.get_registered_models())

    def get_translatable_fields(self, model):
        """
        Get translatable field names for a model.

        Args:
            model: Django model class

        Returns:
            tuple: Field names registered for translation
        """
        opts = self.translator.get_options_for_model(model)
        return opts.fields if opts else ()

    def get_target_field_name(self, field_name):
        """
        Convert a field name to its target language field name.

        Args:
            field_name: Original field name (e.g., 'title')

        Returns:
            str: Target language field name (e.g., 'title_nl')
        """
        return self._localized_fieldname(field_name, self.target_lang)

    def parse_model_names(self, model_names):
        """
        Parse model name strings into model classes.

        Args:
            model_names: List of model name strings (e.g., ['Article', 'blog.Product'])
                        or empty list [] to indicate all models should be used

        Returns:
            list: List of Django model classes, or None if empty list provided

        Raises:
            ValueError: If a model cannot be found
        """
        if not model_names:
            # Empty list or None means translate all models
            return None

        registered_models = self.get_all_registered_models()
        parsed_models = []

        for model_name in model_names:
            # Support both "Article" and "app.Article" formats
            if "." in model_name:
                try:
                    model_cls = apps.get_model(model_name)
                except LookupError as e:
                    raise ValueError(
                        f"Model '{model_name}' not found. "
                        f"Use format 'app_label.ModelName'"
                    ) from e
            else:
                # Try to find by model name alone in registered models
                model_cls = None
                for registered_model in registered_models:
                    if registered_model.__name__ == model_name:
                        model_cls = registered_model
                        break

                if model_cls is None:
                    raise ValueError(
                        f"Model '{model_name}' not found in registered "
                        f"modeltranslation models. Available models: "
                        f"{', '.join(m.__name__ for m in registered_models)}"
                    )

            # Check if model is registered with modeltranslation
            if model_cls not in registered_models:
                raise ValueError(
                    f"Model '{model_name}' is not registered with modeltranslation"
                )

            parsed_models.append(model_cls)

        return parsed_models

    def gather_translatable_content(self, model_list=None, only_empty=True):
        """
        Gather all model field content that needs translation.

        The default-language source is resolved the way modeltranslation's
        ``update_translation_fields`` command would: when the
        ``<field>_<default_lang>`` column is empty, the original column is
        used in its place (and reported via ``backfill_field`` /
        ``backfill_value`` so it can be synced on save).

        Args:
            model_list: List of model classes to process (None = all registered models)
            only_empty: If True, only gather fields with empty target language values

        Returns:
            list: List of dicts with keys:
                - model: Model class
                - instance: Model instance
                - field: Source field name
                - target_field: Target language field name
                - source_text: Text to translate
                - backfill_field: Default-language field name to sync from the
                  original column when translations are applied (or None)
                - backfill_value: Value for backfill_field (or None)
        """
        from django.db.models import FileField, Q

        models = model_list or self.get_all_registered_models()
        translatable_items = []

        for model in models:
            fields = self.get_translatable_fields(model)

            for field_name in fields:
                target_field = self.get_target_field_name(field_name)

                # The original column of a FileField/ImageField holds a raw
                # path string; shipping it to a translation provider would
                # write prose into file columns. Only text content may fall
                # back to the original column.
                original_is_text = not isinstance(
                    model._meta.get_field(field_name), FileField
                )

                source_langs = [
                    lang for lang in self.available_langs if lang != self.target_lang
                ]

                # If no source languages available, skip this field
                if not source_langs:
                    continue

                # Build OR query: at least one language field must have content
                # (excluding the target language field)
                q_has_content = Q()
                for lang in source_langs:
                    lang_field = self._localized_fieldname(field_name, lang)
                    # Add condition: this language field is not null AND not empty
                    q_has_content |= Q(**{f"{lang_field}__isnull": False}) & ~Q(
                        **{f"{lang_field}__exact": ""}
                    )

                # The original column holds the default-language value for
                # rows never saved through the modeltranslation descriptor.
                if self.default_lang in source_langs and original_is_text:
                    q_has_content |= Q(**{f"{field_name}__isnull": False}) & ~Q(
                        **{f"{field_name}__exact": ""}
                    )

                # Base queryset: at least one source language field has content.
                # Disable modeltranslation's lookup rewriting so the original
                # column condition isn't redirected to the active language.
                queryset = model.objects.all()
                if hasattr(queryset, "rewrite"):
                    queryset = queryset.rewrite(False)
                queryset = queryset.filter(q_has_content)

                if only_empty:
                    # Only translate where target field is empty or null
                    queryset = queryset.filter(
                        Q(**{f"{target_field}__isnull": True})
                        | Q(**{f"{target_field}__exact": ""})
                    )

                if self.target_lang == self.default_lang:
                    # The original column IS the default-language value.
                    # A row with content there already has its target text;
                    # machine-translating it from another (possibly machine-
                    # translated) language column would only degrade it.
                    queryset = queryset.filter(
                        Q(**{f"{field_name}__isnull": True})
                        | Q(**{f"{field_name}__exact": ""})
                    )

                for instance in queryset:
                    # Get source text from the first populated language field.
                    # The default language checks the original column as well:
                    # it holds the default-language value for rows never saved
                    # through the modeltranslation descriptor.
                    source_text = None
                    backfill_field = None
                    backfill_value = None
                    for lang in source_langs:
                        lang_field = self._localized_fieldname(field_name, lang)
                        text = getattr(instance, lang_field, None)
                        if not text and lang == self.default_lang and original_is_text:
                            # Read the original column from __dict__ to bypass
                            # the descriptor, which would resolve to the
                            # active language instead of the raw column value.
                            text = instance.__dict__.get(field_name)
                            if text:
                                # Sync the default-language column on save,
                                # like modeltranslation's
                                # update_translation_fields command does.
                                backfill_field = lang_field
                                backfill_value = text
                        if text:  # Found a populated source field
                            source_text = text
                            break

                    if source_text:
                        translatable_items.append(
                            {
                                "model": model,
                                "instance": instance,
                                "field": field_name,
                                "target_field": target_field,
                                "source_text": str(source_text),
                                "backfill_field": backfill_field,
                                "backfill_value": backfill_value,
                            }
                        )

        return translatable_items

    def apply_translations(self, translation_items, dry_run=False):
        """
        Apply translations to model instances.

        Args:
            translation_items: List of dicts with keys:
                - instance: Model instance
                - target_field: Target field name
                - translation: Translated text
                - backfill_field (optional): Default-language field to sync
                  from the original column alongside the translation
                - backfill_value (optional): Value for backfill_field
            dry_run: If True, don't actually save to database

        Returns:
            int: Number of model fields updated
        """
        if dry_run:
            return len(translation_items)

        # The same DB row appears as a separate instance per translated field
        # (gather_translatable_content runs one queryset per field), so first
        # consolidate onto one canonical instance per (model, pk). Passing
        # duplicate pks to bulk_update would let one copy's stale loaded
        # values overwrite another copy's translation.
        by_model = defaultdict(dict)  # model -> pk -> row dict

        for item in translation_items:
            instance = item["instance"]
            target_field = item["target_field"]
            translation = item["translation"]

            rows = by_model[instance.__class__]
            row = rows.setdefault(
                instance.pk,
                {"instance": instance, "translated": set(), "write_fields": set()},
            )
            canonical = row["instance"]
            setattr(canonical, target_field, translation)
            row["translated"].add(target_field)
            row["write_fields"].add(target_field)

            backfill_field = item.get("backfill_field")
            if backfill_field:
                setattr(canonical, backfill_field, item["backfill_value"])
                row["write_fields"].add(backfill_field)

        # Bulk update per model, grouping rows by the exact set of written
        # fields: updating a broader field set would write stale values into
        # fields that were translated in an earlier batch.
        updated_count = 0
        for model_cls, rows in by_model.items():
            field_groups = defaultdict(list)  # frozenset(fields) -> [instances]
            for row in rows.values():
                field_groups[frozenset(row["write_fields"])].append(row["instance"])
                updated_count += len(row["translated"])

            with transaction.atomic():
                for fields, instances in field_groups.items():
                    model_cls.objects.bulk_update(instances, list(fields))

        return updated_count
