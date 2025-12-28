import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError


def get_model():
    """Get default model from the Django settings or use fallback."""
    model = getattr(settings, "TRANSLATEBOT_MODEL", None)

    if model is None:
        raise CommandError(
            "Model is not configured. Set TRANSLATEBOT_MODEL in the Django settings."
        )
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


def get_all_po_paths(target_lang):
    """
    Find all .po files for the given target language across all Django
    locale paths.
    """
    import sys

    from django.apps import apps

    po_paths = []
    checked_paths = []

    # Identify site-packages directories to exclude third-party apps
    site_packages_dirs = {
        Path(p).resolve()
        for p in sys.path
        if "site-packages" in p or "dist-packages" in p
    }

    # Check LOCALE_PATHS from settings
    locale_paths = getattr(settings, "LOCALE_PATHS", [])
    for locale_path in locale_paths:
        po_path = Path(locale_path) / target_lang / "LC_MESSAGES" / "django.po"
        checked_paths.append(str(po_path))
        if po_path.exists():
            po_paths.append(po_path)

    # Check each installed app for locale directories
    # (only project apps, not third-party)
    for app_config in apps.get_app_configs():
        app_path = Path(app_config.path).resolve()

        # Skip if app is in site-packages (third-party)
        is_third_party = any(
            str(app_path).startswith(str(site_pkg)) for site_pkg in site_packages_dirs
        )

        if not is_third_party:
            app_locale_dir = app_path / "locale"
            if app_locale_dir.exists():
                po_path = app_locale_dir / target_lang / "LC_MESSAGES" / "django.po"
                checked_paths.append(str(po_path))
                if po_path.exists():
                    po_paths.append(po_path)

    # Check default locale directory in project root
    if not locale_paths:
        default_locale = Path("locale")
        if default_locale.exists():
            po_path = default_locale / target_lang / "LC_MESSAGES" / "django.po"
            checked_paths.append(str(po_path))
            if po_path.exists():
                po_paths.append(po_path)

    if not po_paths:
        locations = "\n".join(f"  - {p}" for p in checked_paths)
        raise CommandError(
            f"No translation files found for language '{target_lang}'.\n"
            f"Checked locations:\n{locations}\n"
            f"Run 'django-admin makemessages -l {target_lang}' to create "
            "translation files."
        )

    return po_paths
