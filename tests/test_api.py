"""
Tests for the public Python API (translatebot_django.api.translate).
"""

from unittest.mock import patch

import pytest

from translatebot_django import TranslateResult, translate
from translatebot_django.api import TranslateResult as TranslateResultFromApi
from translatebot_django.api import translate as translate_from_api
from translatebot_django.management.commands.translate import (
    Command as TranslateCommand,
)


def test_translate_is_importable_from_package():
    """translate() is importable from the top-level package."""
    assert translate is translate_from_api


def test_translate_result_is_importable_from_package():
    """TranslateResult is importable from the top-level package."""
    assert TranslateResult is TranslateResultFromApi


def _get_call_kwargs(mock_call):
    """Extract the keyword arguments from a mocked call_command call."""
    _, kwargs = mock_call.call_args
    return kwargs


def test_translate_delegates_to_management_command(sample_po_file, mock_env_api_key):
    """translate() calls the translate management command under the hood."""
    with patch("translatebot_django.api.call_command") as mock_call:
        result = translate(target_langs="nl", dry_run=True)
        mock_call.assert_called_once()
        # First positional arg is a TranslateCommand instance
        cmd_arg = mock_call.call_args[0][0]
        assert isinstance(cmd_arg, TranslateCommand)
        assert _get_call_kwargs(mock_call) == {
            "dry_run": True,
            "overwrite": False,
            "target_lang": ["nl"],
        }
        assert isinstance(result, TranslateResult)
        assert result.dry_run is True


def test_translate_no_args_omits_target_lang(sample_po_file, mock_env_api_key):
    """When target_langs is omitted, target_lang kwarg is not passed."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(dry_run=True)
        assert _get_call_kwargs(mock_call) == {
            "dry_run": True,
            "overwrite": False,
        }


def test_translate_multiple_langs(sample_po_file, mock_env_api_key):
    """A list of languages is forwarded correctly."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs=["nl", "de"], dry_run=True)
        assert _get_call_kwargs(mock_call) == {
            "dry_run": True,
            "overwrite": False,
            "target_lang": ["nl", "de"],
        }


def test_translate_apps_string(sample_po_file, mock_env_api_key):
    """A single app label string is normalized to a list."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", apps="blog", dry_run=True)
        assert _get_call_kwargs(mock_call) == {
            "dry_run": True,
            "overwrite": False,
            "target_lang": ["nl"],
            "apps": ["blog"],
        }


def test_translate_apps_list(sample_po_file, mock_env_api_key):
    """A list of app labels is forwarded correctly."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", apps=["blog", "shop"], dry_run=True)
        assert _get_call_kwargs(mock_call) == {
            "dry_run": True,
            "overwrite": False,
            "target_lang": ["nl"],
            "apps": ["blog", "shop"],
        }


def test_translate_models_true(sample_po_file, mock_env_api_key):
    """models=True translates all registered models (empty list)."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", models=True, dry_run=True)
        assert _get_call_kwargs(mock_call) == {
            "dry_run": True,
            "overwrite": False,
            "target_lang": ["nl"],
            "models": [],
        }


def test_translate_models_list(sample_po_file, mock_env_api_key):
    """A list of model names is forwarded correctly."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", models=["Article"], dry_run=True)
        assert _get_call_kwargs(mock_call) == {
            "dry_run": True,
            "overwrite": False,
            "target_lang": ["nl"],
            "models": ["Article"],
        }


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
        assert _get_call_kwargs(mock_call) == {
            "dry_run": True,
            "overwrite": True,
            "target_lang": ["nl"],
        }


def test_translate_returns_translate_result(sample_po_file, mock_env_api_key):
    """translate() always returns a TranslateResult, even when command sets no stats."""
    with patch("translatebot_django.api.call_command"):
        result = translate(target_langs="nl")
        assert isinstance(result, TranslateResult)
        assert result.strings_found == 0
        assert result.strings_translated == 0
        assert result.po_files == 0
        assert result.model_fields_found == 0
        assert result.model_fields_translated == 0
        assert result.target_langs == []
        assert result.dry_run is False


def test_translate_returns_stats_from_command(sample_po_file, mock_env_api_key):
    """translate() populates TranslateResult from command's _translate_stats."""
    fake_stats = {
        "strings_found": 10,
        "strings_translated": 8,
        "po_files": 3,
        "model_fields_found": 0,
        "model_fields_translated": 0,
        "target_langs": ["nl"],
    }

    def set_stats_on_cmd(cmd, **kwargs):
        cmd._translate_stats = fake_stats

    with patch("translatebot_django.api.call_command", side_effect=set_stats_on_cmd):
        result = translate(target_langs="nl")
        assert result.strings_found == 10
        assert result.strings_translated == 8
        assert result.po_files == 3
        assert result.target_langs == ["nl"]
        assert result.dry_run is False


def test_translate_model_forwarded(sample_po_file, mock_env_api_key):
    """model= is forwarded to the command as llm_model."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", model="gpt-4o", dry_run=True)
        assert _get_call_kwargs(mock_call) == {
            "dry_run": True,
            "overwrite": False,
            "target_lang": ["nl"],
            "llm_model": "gpt-4o",
        }


def test_translate_model_none_omitted(sample_po_file, mock_env_api_key):
    """When model is not passed, llm_model is not included in kwargs."""
    with patch("translatebot_django.api.call_command") as mock_call:
        translate(target_langs="nl", dry_run=True)
        assert "llm_model" not in _get_call_kwargs(mock_call)


def test_translate_end_to_end_dry_run(
    sample_po_file, mock_env_api_key, mock_model_config
):
    """End-to-end: translate() in dry-run mode returns stats."""
    result = translate(target_langs="nl", dry_run=True)
    assert isinstance(result, TranslateResult)
    assert result.dry_run is True
    assert result.target_langs == ["nl"]
    assert result.strings_found >= 0
    assert result.strings_translated >= 0
    assert result.po_files >= 1
