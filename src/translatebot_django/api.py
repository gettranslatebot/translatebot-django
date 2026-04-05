from django.core.management import call_command


def translate(
    *,
    target_langs=None,
    dry_run=False,
    overwrite=False,
    apps=None,
    models=None,
    model=None,
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
        model: LiteLLM model name to use for this call (e.g. ``"gpt-4o"`` or
            ``"claude-3-5-haiku-20241022"``). When provided, overrides
            ``TRANSLATEBOT_MODEL`` in settings. Ignored when using the DeepL
            provider.

    Raises:
        ValueError: If *apps* and *models* are both provided, or if *models*
            is not ``True``, a list, or ``None``.
        django.core.management.base.CommandError: On configuration or
            translation errors (missing API key, unknown language, etc.).

    Examples::

        from translatebot_django import translate

        # Translate PO files to all configured languages
        translate()

        # Translate to specific languages
        translate(target_langs=["nl", "de"])

        # Translate model fields
        translate(models=True)

        # Translate specific models for one language
        translate(target_langs="fr", models=["Article", "Product"])

        # Preview changes
        translate(dry_run=True)

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

    if model is not None:
        kwargs["model"] = model

    call_command("translate", **kwargs)
