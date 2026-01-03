"""
Minimal Django settings for running tests.
"""

SECRET_KEY = "test-secret-key"

INSTALLED_APPS = [
    "modeltranslation",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "translatebot_django",
    "tests",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

USE_TZ = True

# Modeltranslation settings
LANGUAGES = [
    ("en", "English"),
    ("nl", "Dutch"),
    ("de", "German"),
]

MODELTRANSLATION_DEFAULT_LANGUAGE = "en"
