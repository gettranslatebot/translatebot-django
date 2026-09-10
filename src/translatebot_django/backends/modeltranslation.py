"""Backend for django-modeltranslation model field translation."""

from collections import defaultdict

from django.apps import apps
from django.db import transaction
from django.db.models import F, FileField, Q


def _q_has_content(field):
    """Q clause matching rows where the column is non-null and non-empty."""
    return Q(**{f"{field}__isnull": False}) & ~Q(**{f"{field}__exact": ""})


def _q_is_empty(field):
    """Q clause matching rows where the column is null or empty."""
    return Q(**{f"{field}__isnull": True}) | Q(**{f"{field}__exact": ""})


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

    def _plain_queryset(self, model):
        """
        Queryset with modeltranslation's lookup rewriting disabled, so
        original-column conditions and updates aren't redirected to the
        active language. A custom manager may return a plain QuerySet
        without ``rewrite``; that needs no disabling.
        """
        queryset = model.objects.all()
        if hasattr(queryset, "rewrite"):
            queryset = queryset.rewrite(False)
        return queryset

    def gather_translatable_content(self, model_list=None, only_empty=True):
        """
        Gather all model field content that needs translation.

        The default language is the preferred source — the other languages'
        columns may themselves be machine translations. Its value is
        resolved the way modeltranslation's ``update_translation_fields``
        command would: when the ``<field>_<default_lang>`` column is empty,
        the original column is used in its place (and reported via
        ``backfill_field`` so it can be synced on save).

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
                - backfill_field: Default-language field name to sync with
                  the source text when translations are applied (or None)
        """
        models = model_list or self.get_all_registered_models()
        # Dedupe, preserving order: duplicate --models arguments (e.g.
        # "Article tests.Article") resolve to the same class and would
        # otherwise emit every item twice.
        models = list(dict.fromkeys(models))

        source_langs = [
            lang for lang in self.available_langs if lang != self.target_lang
        ]
        if not source_langs:
            return []
        # Nothing guarantees the default language is listed first in
        # AVAILABLE_LANGUAGES, so order it first explicitly.
        if self.default_lang in source_langs:
            source_langs.remove(self.default_lang)
            source_langs.insert(0, self.default_lang)

        translatable_items = []

        for model in models:
            for field_name in self.get_translatable_fields(model):
                # A FileField/ImageField column — original or per-language —
                # holds a raw path string; shipping one to a translation
                # provider would write prose into file columns.
                if isinstance(model._meta.get_field(field_name), FileField):
                    continue

                target_field = self.get_target_field_name(field_name)
                lang_fields = [
                    self._localized_fieldname(field_name, lang) for lang in source_langs
                ]

                # Build OR query: at least one source language field must
                # have content
                q_content = Q()
                for lang_field in lang_fields:
                    q_content |= _q_has_content(lang_field)

                # The original column holds the default-language value for
                # rows never saved through the modeltranslation descriptor.
                if self.default_lang in source_langs:
                    q_content |= _q_has_content(field_name)

                # Narrow the query to the columns actually read; field_name
                # must stay in the list because the fallback below reads it
                # from __dict__, where deferred columns are absent.
                queryset = self._plain_queryset(model).only(
                    field_name, target_field, *lang_fields
                )
                queryset = queryset.filter(q_content)

                if only_empty:
                    # Only translate where target field is empty or null
                    queryset = queryset.filter(_q_is_empty(target_field))

                if self.target_lang == self.default_lang:
                    # The original column IS the default-language value.
                    # A row with content there already has its target text;
                    # machine-translating it from another (possibly machine-
                    # translated) language column would only degrade it.
                    queryset = queryset.filter(_q_is_empty(field_name))

                for instance in queryset:
                    # Get source text from the first populated language
                    # field, default language first. The default language
                    # checks the original column as well: it holds the
                    # default-language value for rows never saved through
                    # the modeltranslation descriptor.
                    source_text = None
                    backfill_field = None
                    for lang, lang_field in zip(source_langs, lang_fields, strict=True):
                        text = getattr(instance, lang_field, None)
                        if not text and lang == self.default_lang:
                            # Read the original column from __dict__ to bypass
                            # the descriptor, which would resolve to the
                            # active language instead of the raw column value.
                            text = instance.__dict__.get(field_name)
                            if text:
                                # Sync the default-language column on save,
                                # like modeltranslation's
                                # update_translation_fields command does.
                                backfill_field = lang_field
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
                - field (optional): Source field name; when translating into
                  the default language it is used to keep the original
                  column in sync with the new default-language value
                - backfill_field (optional): Default-language field to fill
                  with the item's source text; requires source_text
                - source_text (optional): Source text the translation was
                  made from, written to backfill_field
            dry_run: If True, don't actually save to database

        Returns:
            int: Number of distinct model fields updated (duplicate items
            for the same row and field count once)
        """
        # Validate before mutating any instance, so a bad item can't leave
        # earlier items half-applied in memory.
        for item in translation_items:
            if item.get("backfill_field") and item.get("source_text") is None:
                raise ValueError(
                    "translation item with backfill_field "
                    f"'{item['backfill_field']}' is missing source_text"
                )

        if dry_run:
            # Report what a real run would write: duplicate items for the
            # same row and field collapse into a single update.
            return len(
                {
                    (
                        item["instance"].__class__,
                        getattr(item["instance"], "pk", None),
                        item["target_field"],
                    )
                    for item in translation_items
                }
            )

        # The same DB row appears as a separate instance per translated field
        # (gather_translatable_content runs one queryset per field), so first
        # consolidate onto one canonical instance per (model, pk). Passing
        # duplicate pks to bulk_update would let one copy's stale loaded
        # values overwrite another copy's translation.
        by_model = defaultdict(dict)  # model -> pk -> row dict

        for item in translation_items:
            instance = item["instance"]
            target_field = item["target_field"]

            rows = by_model[instance.__class__]
            row = rows.setdefault(
                instance.pk,
                {
                    "instance": instance,
                    "translated": set(),
                    "write_fields": set(),
                    "mirror_fields": set(),
                },
            )
            canonical = row["instance"]
            setattr(canonical, target_field, item["translation"])
            row["translated"].add(target_field)
            row["write_fields"].add(target_field)

            backfill_field = item.get("backfill_field")
            if backfill_field:
                setattr(canonical, backfill_field, item["source_text"])
                row["write_fields"].add(backfill_field)

            # When translating INTO the default language, the original
            # column — the authoritative default-language store — must
            # receive the new value as well. bulk_update can't write it
            # safely (reading the field goes through the modeltranslation
            # descriptor, which resolves to the active language), so mirror
            # it with a raw column-to-column UPDATE below.
            field_name = item.get("field")
            if field_name and self.target_lang == self.default_lang:
                row["mirror_fields"].add((field_name, target_field))

        updated_count = 0
        for model_cls, rows in by_model.items():
            # Group instances per written field: each bulk_update then
            # writes exactly one explicitly-set column, so no instance can
            # leak a stale loaded value into a field it wasn't translated
            # for (e.g. one already translated in an earlier batch).
            field_groups = defaultdict(list)  # field -> [instances]
            mirror_groups = defaultdict(list)  # (field, target_field) -> [pks]
            for row in rows.values():
                updated_count += len(row["translated"])
                for field in row["write_fields"]:
                    field_groups[field].append(row["instance"])
                for mirror in row["mirror_fields"]:
                    mirror_groups[mirror].append(row["instance"].pk)

            with transaction.atomic():
                for field, instances in field_groups.items():
                    model_cls.objects.bulk_update(instances, [field])
                for (field_name, target_field), pks in mirror_groups.items():
                    self._plain_queryset(model_cls).filter(pk__in=pks).update(
                        **{field_name: F(target_field)}
                    )

        return updated_count
