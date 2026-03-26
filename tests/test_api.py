"""
Tests for the public Python API (translatebot_django.api.translate).
"""

from unittest.mock import patch

import pytest

from translatebot_django import translate
from translatebot_django.api import translate as translate_from_api


def test_translate_is_importable_from_package():
    """translate() is importable from the top-level package."""
    assert translate is translate_from_api


def test_translate_delegates_to_management_command(sample_po_file, mock_env_api_key):
    """translate() calls the translate management command under the hood."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", dry_run=True)
        mock_call.assert_called_once_with(
            "translate",
            dry_run=True,
            overwrite=False,
            target_lang=["nl"],
        )


def test_translate_no_args_omits_target_lang(sample_po_file, mock_env_api_key):
    """When target_langs is omitted, target_lang kwarg is not passed."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(dry_run=True)
        mock_call.assert_called_once_with(
            "translate",
            dry_run=True,
            overwrite=False,
        )


def test_translate_multiple_langs(sample_po_file, mock_env_api_key):
    """A list of languages is forwarded correctly."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs=["nl", "de"], dry_run=True)
        mock_call.assert_called_once_with(
            "translate",
            dry_run=True,
            overwrite=False,
            target_lang=["nl", "de"],
        )


def test_translate_apps_string(sample_po_file, mock_env_api_key):
    """A single app label string is normalized to a list."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", apps="blog", dry_run=True)
        mock_call.assert_called_once_with(
            "translate",
            dry_run=True,
            overwrite=False,
            target_lang=["nl"],
            apps=["blog"],
        )


def test_translate_apps_list(sample_po_file, mock_env_api_key):
    """A list of app labels is forwarded correctly."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", apps=["blog", "shop"], dry_run=True)
        mock_call.assert_called_once_with(
            "translate",
            dry_run=True,
            overwrite=False,
            target_lang=["nl"],
            apps=["blog", "shop"],
        )


def test_translate_models_true(sample_po_file, mock_env_api_key):
    """models=True translates all registered models (empty list)."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", models=True, dry_run=True)
        mock_call.assert_called_once_with(
            "translate",
            dry_run=True,
            overwrite=False,
            target_lang=["nl"],
            models=[],
        )


def test_translate_models_list(sample_po_file, mock_env_api_key):
    """A list of model names is forwarded correctly."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", models=["Article"], dry_run=True)
        mock_call.assert_called_once_with(
            "translate",
            dry_run=True,
            overwrite=False,
            target_lang=["nl"],
            models=["Article"],
        )


def test_translate_models_invalid_type():
    """An invalid models type raises ValueError."""
    with pytest.raises(ValueError, match="models must be True"):
        translate(models="Article")


def test_translate_apps_and_models_mutually_exclusive():
    """Passing both apps and models raises ValueError."""
    with pytest.raises(ValueError, match="apps and models cannot be used together"):
        translate(apps="blog", models=True)


def test_translate_overwrite(sample_po_file, mock_env_api_key):
    """The overwrite flag is forwarded correctly."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", overwrite=True, dry_run=True)
        mock_call.assert_called_once_with(
            "translate",
            dry_run=True,
            overwrite=True,
            target_lang=["nl"],
        )


def test_translate_end_to_end_dry_run(
    sample_po_file, mock_env_api_key, mock_model_config
):
    """End-to-end: translate() in dry-run mode completes without error."""
    translate(target_langs="nl", dry_run=True)
