import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.utils.translation import to_locale


def get_model():
    """Get default model from the Django settings or use fallback."""
    model = getattr(settings, "TRANSLATEBOT_MODEL", "gpt-4o-mini")
    return model


def get_api_key():
    """Get the API key from the Django settings or an environment variable."""
    # Try Django settings first
    api_key = getattr(settings, "TRANSLATEBOT_API_KEY", None)

    if api_key is None:
        api_key = os.getenv("TRANSLATEBOT_API_KEY", None)

    if api_key is None:
        raise CommandError(
            "API key not configured. Set TRANSLATEBOT_API_KEY in the Django settings "
            "or TRANSLATEBOT_API_KEY environment variable."
        )

    return api_key


def get_all_po_paths(target_lang, app_labels=None):
    """
    Find all .po files for the given target language across all Django
    locale paths.

    The target_lang should be a language code (e.g., 'zh-hans', 'pt-br').
    This function converts it to a locale name (e.g., 'zh_Hans', 'pt_BR')
    for directory lookup, as Django uses locale names for directory structure.

    If app_labels is provided, only include .po files from apps whose
    app_config.label matches one of the given labels. LOCALE_PATHS and the
    default locale/ directory are skipped when filtering by app.
    """
    import sys

    from django.apps import apps

    # Convert language code to locale name for directory lookup
    # e.g., 'zh-hans' -> 'zh_Hans', 'pt-br' -> 'pt_BR'
    locale_name = to_locale(target_lang)

    # Validate app labels if provided
    if app_labels:
        all_app_labels = {cfg.label for cfg in apps.get_app_configs()}
        unknown = set(app_labels) - all_app_labels
        if unknown:
            raise CommandError(
                f"Unknown app label(s): {', '.join(sorted(unknown))}. "
                f"Available apps: {', '.join(sorted(all_app_labels))}"
            )

    po_paths = []
    checked_paths = []

    # Identify site-packages directories to exclude third-party apps
    site_packages_dirs = {
        Path(p).resolve()
        for p in sys.path
        if "site-packages" in p or "dist-packages" in p
    }

    # Check LOCALE_PATHS from settings (skip when filtering by app)
    locale_paths = getattr(settings, "LOCALE_PATHS", [])
    if not app_labels:
        for locale_path in locale_paths:
            po_path = Path(locale_path) / locale_name / "LC_MESSAGES" / "django.po"
            checked_paths.append(str(po_path))
            if po_path.exists():
                po_paths.append(po_path)

    # Check each installed app for locale directories
    # (only project apps, not third-party)
    for app_config in apps.get_app_configs():
        # If filtering by app, skip apps not in the list
        if app_labels and app_config.label not in app_labels:
            continue

        app_path = Path(app_config.path).resolve()

        # Skip if app is in site-packages (third-party)
        is_third_party = any(
            str(app_path).startswith(str(site_pkg)) for site_pkg in site_packages_dirs
        )

        if not is_third_party:
            app_locale_dir = app_path / "locale"
            if app_locale_dir.exists():
                po_path = app_locale_dir / locale_name / "LC_MESSAGES" / "django.po"
                checked_paths.append(str(po_path))
                if po_path.exists():
                    po_paths.append(po_path)

    # Check default locale directory in project root (skip when filtering by app)
    if not app_labels and not locale_paths:
        default_locale = Path("locale")
        if default_locale.exists():
            po_path = default_locale / locale_name / "LC_MESSAGES" / "django.po"
            checked_paths.append(str(po_path))
            if po_path.exists():
                po_paths.append(po_path)

    if not po_paths:
        locations = "\n".join(f"  - {p}" for p in checked_paths)
        raise CommandError(
            f"No translation files found for language '{target_lang}'.\n"
            f"Checked locations:\n{locations}\n"
            f"Run 'django-admin makemessages -l {locale_name}' to create "
            "translation files."
        )

    return po_paths


def is_modeltranslation_available():
    """Check if django-modeltranslation is installed and configured."""
    try:
        import modeltranslation  # noqa: F401

        return "modeltranslation" in settings.INSTALLED_APPS
    except ImportError:
        return False


def get_modeltranslation_translator():
    """Get the modeltranslation translator registry if available."""
    if not is_modeltranslation_available():
        return None

    from modeltranslation.translator import translator

    return translator


def get_translation_context():
    """
    Find and read the TRANSLATING.md file from the project root.

    This file allows users to provide context about their project
    that will be included in the system prompt sent to the LLM,
    helping to improve translation quality.

    Returns:
        str or None: The contents of TRANSLATING.md if found, None otherwise.
    """
    # Look for TRANSLATING.md in the project root (where manage.py typically is)
    # Try settings.BASE_DIR first, then fall back to current directory
    search_paths = []

    # Try BASE_DIR from settings
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir:
        search_paths.append(Path(base_dir))

    # Also try current working directory as fallback
    search_paths.append(Path.cwd())

    for base_path in search_paths:
        context_file = base_path / "TRANSLATING.md"
        if context_file.exists():
            try:
                return context_file.read_text(encoding="utf-8").strip()
            except OSError:
                # If we can't read the file for some reason, skip it
                continue

    return None


def get_app_translation_context(po_path):
    """
    Find and read a TRANSLATING.md file from the app directory containing the .po file.

    Expects the standard Django app locale structure:
    {app_dir}/locale/{lang}/LC_MESSAGES/django.po

    Skips if the path doesn't match this structure (e.g. LOCALE_PATHS entries),
    or if app_dir matches settings.BASE_DIR or Path.cwd() to avoid
    duplicating the project-level context.

    Args:
        po_path: Path to a django.po file

    Returns:
        str or None: The contents of the app's TRANSLATING.md if found, None otherwise.
    """
    po_path = Path(po_path).resolve()

    # Validate expected Django app locale structure:
    # {app}/locale/{lang}/LC_MESSAGES/django.po
    if (
        po_path.parent.name != "LC_MESSAGES"
        or po_path.parent.parent.parent.name != "locale"
    ):
        return None

    app_dir = po_path.parent.parent.parent.parent

    # Skip project root directories to avoid duplicating project-level context
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir and app_dir == Path(base_dir).resolve():
        return None
    if app_dir == Path.cwd().resolve():
        return None

    context_file = app_dir / "TRANSLATING.md"
    if context_file.exists():
        try:
            content = context_file.read_text(encoding="utf-8").strip()
            return content or None
        except OSError:
            return None

    return None


def combine_translation_contexts(project_context, app_context):
    """
    Combine project-level and app-level translation contexts.

    Args:
        project_context: Optional string from project-level TRANSLATING.md
        app_context: Optional string from app-level TRANSLATING.md

    Returns:
        str or None: Combined context, or whichever is available, or None.
    """
    if project_context and app_context:
        return f"{project_context}\n\n## App-Specific Context\n{app_context}"
    if project_context:
        return project_context
    if app_context:
        return app_context
    return None
