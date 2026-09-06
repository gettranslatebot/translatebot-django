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

        self.translator = translator
        self.target_lang = target_lang
        # modeltranslation stores the default-language value in the original
        # column; the <field>_<default_lang> column is only a mirror that gets
        # synced on descriptor-mediated saves. Rows created before
        # modeltranslation was installed (or written via update()/imports)
        # have their default-language content only in the original column.
        self.default_lang = mt_settings.DEFAULT_LANGUAGE

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
        return f"{field_name}_{self.target_lang}"

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
        """
        models = model_list or self.get_all_registered_models()
        translatable_items = []

        for model in models:
            fields = self.get_translatable_fields(model)

            for field_name in fields:
                target_field = self.get_target_field_name(field_name)

                # Get all available languages from settings
                from django.conf import settings
                from django.db.models import Q

                # Get available languages from modeltranslation or Django settings
                if hasattr(settings, "MODELTRANSLATION_LANGUAGES"):
                    available_langs = list(settings.MODELTRANSLATION_LANGUAGES)
                elif hasattr(settings, "LANGUAGES"):
                    available_langs = [lang_code for lang_code, _ in settings.LANGUAGES]
                else:
                    # Fallback: just use target language (edge case)
                    available_langs = [self.target_lang]

                # Build OR query: at least one language field must have content
                # (excluding the target language field)
                q_has_content = Q()
                source_langs = [
                    lang for lang in available_langs if lang != self.target_lang
                ]

                for lang in source_langs:
                    lang_field = f"{field_name}_{lang}"
                    # Add condition: this language field is not null AND not empty
                    q_has_content |= Q(**{f"{lang_field}__isnull": False}) & ~Q(
                        **{f"{lang_field}__exact": ""}
                    )

                # The original column holds the default-language value for
                # rows never saved through the modeltranslation descriptor.
                if self.default_lang in source_langs:
                    q_has_content |= Q(**{f"{field_name}__isnull": False}) & ~Q(
                        **{f"{field_name}__exact": ""}
                    )

                # If no source languages available, skip this field
                if not source_langs:
                    continue

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

                for instance in queryset:
                    # Get source text from the first populated language field
                    source_text = None
                    for lang in source_langs:
                        lang_field = f"{field_name}_{lang}"
                        text = getattr(instance, lang_field, None)
                        if text:  # Found a populated source field
                            source_text = text
                            break

                    if not source_text and self.default_lang in source_langs:
                        # Last resort: the original column, which holds the
                        # default-language value for rows never saved through
                        # the modeltranslation descriptor. Read it from
                        # __dict__ to bypass the descriptor, which would
                        # resolve to the active language instead of the raw
                        # column value.
                        source_text = instance.__dict__.get(field_name)

                    if source_text:
                        translatable_items.append(
                            {
                                "model": model,
                                "instance": instance,
                                "field": field_name,
                                "target_field": target_field,
                                "source_text": str(source_text),
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
        by_model = defaultdict(dict)  # model -> pk -> (instance, {fields})

        for item in translation_items:
            instance = item["instance"]
            target_field = item["target_field"]
            translation = item["translation"]

            rows = by_model[instance.__class__]
            canonical, fields = rows.setdefault(instance.pk, (instance, set()))
            setattr(canonical, target_field, translation)
            fields.add(target_field)

        # Bulk update per model, grouping rows by the exact set of translated
        # fields: updating a broader field set would write stale values into
        # fields that were translated in an earlier batch.
        for model_cls, rows in by_model.items():
            field_groups = defaultdict(list)  # frozenset(fields) -> [instances]
            for canonical, fields in rows.values():
                field_groups[frozenset(fields)].append(canonical)

            with transaction.atomic():
                for fields, instances in field_groups.items():
                    model_cls.objects.bulk_update(instances, list(fields))

        return len(translation_items)
