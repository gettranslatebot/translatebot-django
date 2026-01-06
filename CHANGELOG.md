# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-01-06

### Changed

- Updated Django 5.2.9 to 5.2.10
- Updated Django 6.0 to 6.0.1

## [0.2.1] - 2026-01-04

### Changed

- Updated author metadata formatting in package configuration

## [0.2.0] - 2026-01-03

### Added

- Added proper error handling for authentication failures with helpful error messages
- Added support for translating django-modeltranslation model fields with `--models` flag
- Added intelligent batching for large translation jobs to handle token limits

### Changed

- `TRANSLATEBOT_MODEL` setting now defaults to `gpt-4o-mini` when not configured (previously required)
- Improved token limit handling for model field translations with intelligent batching
- Updated README to use `uv add` instead of `pip install` for installation instructions
- Removed unused `source_lang` parameter from `ModeltranslationBackend`
- Improved CLI output for dry-run mode to be cleaner and more informative
- Enhanced dry-run output to show accurate counts of entries that would be translated
- Improved modeltranslation integration to properly detect source content in language-specific fields

### Fixed

- Fixed `--dry-run` flag to properly skip LLM API calls instead of contacting the provider
- Fixed dry-run mode showing incorrect entry counts (was always showing 0)
- Fixed dry-run mode for model field translation showing 0 fields when records exist
- Fixed dry-run output showing excessive separator lines when no translations needed
- Fixed incorrect model names in README (updated to current Claude 4.5, Gemini 2.5/3, etc.)
- Corrected misleading README claims about source language auto-detection
- Added missing link to django-modeltranslation project in README features
- Fixed queryset filtering to check language-specific fields instead of base field
- Fixed source text extraction to use populated language fields instead of relying on fallback

### Infrastructure

- Achieved 100% test coverage across entire codebase
- Added comprehensive test suite for authentication error handling
- Added tests for dry-run mode edge cases
- Added tests for batching logic with large datasets

## [0.1.4] - 2025-12-31

### Changed

- Improved CLI output with emojis to match the landing page demo

## [0.1.3] - 2025-12-29

### Changed

- Added PyPI badge to README
- Grouped badges in README

## [0.1.2] - 2025-12-28

### Added

- Added Changelog and a link to it on the PyPI page

## [0.1.1] - 2025-12-28

### Changed

- Added project URLs to PyPI page (Homepage, Repository, Issues)
- Added Poetry installation instructions to README

### Infrastructure

- Added Codecov integration for test coverage reporting

## [0.1.0] - 2025-12-28

### Added

- Initial release
- Django management command `translatebot` for translating .po files
- Support for OpenAI-compatible LLM providers via LiteLLM
- Automatic detection of untranslated and fuzzy entries
- Preserves existing translations by default
- Configurable via Django settings or command-line arguments
- Support for Django 4.2, 5.0, 5.1, 5.2, and 6.0
- Support for Python 3.9 through 3.14

[0.2.2]: https://github.com/gettranslatebot/translatebot-django/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/gettranslatebot/translatebot-django/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/gettranslatebot/translatebot-django/compare/v0.1.4...v0.2.0
[0.1.4]: https://github.com/gettranslatebot/translatebot-django/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/gettranslatebot/translatebot-django/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/gettranslatebot/translatebot-django/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/gettranslatebot/translatebot-django/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/gettranslatebot/translatebot-django/releases/tag/v0.1.0
