import dataclasses

from django.core.management import call_command

from translatebot_django.management.commands.translate import (
    Command as TranslateCommand,
)


@dataclasses.dataclass
class TranslateResult:
    """Statistics returned by :func:`translate`.

    Attributes:
        strings_found: Number of translatable strings found in PO files.
        strings_translated: Number of PO strings that were translated
            (or would be translated in dry-run mode).
        po_files: Number of PO files processed.
        model_fields_found: Number of translatable model fields found.
        model_fields_translated: Number of model fields that were translated
            (or would be translated in dry-run mode).
        target_langs: Language codes that were translated to.
        dry_run: Whether the translation was a dry run.
    """

    strings_found: int = 0
    strings_translated: int = 0
    po_files: int = 0
    model_fields_found: int = 0
    model_fields_translated: int = 0
    target_langs: list[str] = dataclasses.field(default_factory=list)
    dry_run: bool = False


def translate(
    *,
    target_langs=None,
    dry_run=False,
    overwrite=False,
    apps=None,
    models=None,
):
    """Translate PO files and/or model fields programmatically.

    This is the public Python API for TranslateBot. It provides the same
    functionality as the ``translate`` management command but can be called
    directly from Python code — for example, from a Celery task, a custom
    management command, or a Django view.

    Args:
        target_langs: Language code or list of language codes to translate to
            (e.g. ``"nl"`` or ``["nl", "de"]``). When omitted, translates to
            all languages defined in ``settings.LANGUAGES``, excluding the
            source language (``settings.LANGUAGE_CODE``).
        dry_run: If ``True``, preview what would be translated without saving.
        overwrite: If ``True``, re-translate entries that already have
            translations.
        apps: App label or list of app labels to restrict PO file translation
            to (e.g. ``"blog"`` or ``["blog", "shop"]``). Cannot be combined
            with *models*.
        models: Controls model field translation via django-modeltranslation.

            - ``None`` (default): translate PO files only.
            - ``True`` or ``[]``: translate all registered model fields.
            - A list of model names (e.g. ``["Article", "Product"]``):
              translate only those models.

    Returns:
        TranslateResult: Statistics about what was translated, including
        counts of strings found, translated, PO files processed, and
        model fields handled.

    Raises:
        ValueError: If *apps* and *models* are both provided, or if *models*
            is not ``True``, a list, or ``None``.
        django.core.management.base.CommandError: On configuration or
            translation errors (missing API key, unknown language, etc.).

    Examples::

        from translatebot_django import translate

        # Translate PO files to all configured languages
        result = translate()
        print(f"Translated {result.strings_translated} strings")

        # Translate to specific languages
        result = translate(target_langs=["nl", "de"])

        # Translate model fields
        result = translate(models=True)
        print(f"Updated {result.model_fields_translated} model fields")

        # Translate specific models for one language
        translate(target_langs="fr", models=["Article", "Product"])

        # Preview changes
        result = translate(dry_run=True)
        print(f"Would translate {result.strings_found} strings")

        # Use in a Celery task
        from celery import shared_task

        @shared_task
        def translate_nightly():
            translate()
    """
    if apps is not None and models is not None:
        raise ValueError(
            "apps and models cannot be used together. "
            "The apps parameter only filters PO file translation."
        )

    kwargs = {
        "dry_run": dry_run,
        "overwrite": overwrite,
    }

    if target_langs is not None:
        if isinstance(target_langs, str):
            target_langs = [target_langs]
        kwargs["target_lang"] = list(target_langs)

    if apps is not None:
        if isinstance(apps, str):
            apps = [apps]
        kwargs["apps"] = list(apps)

    if models is not None:
        if models is True:
            kwargs["models"] = []
        elif isinstance(models, list):
            kwargs["models"] = models
        else:
            raise ValueError(
                "models must be True, a list of model names, or None. "
                f"Got {type(models).__name__}."
            )

    cmd = TranslateCommand()
    call_command(cmd, **kwargs)
    stats = getattr(cmd, "_translate_stats", {})
    return TranslateResult(
        strings_found=stats.get("strings_found", 0),
        strings_translated=stats.get("strings_translated", 0),
        po_files=stats.get("po_files", 0),
        model_fields_found=stats.get("model_fields_found", 0),
        model_fields_translated=stats.get("model_fields_translated", 0),
        target_langs=stats.get("target_langs", []),
        dry_run=dry_run,
    )
