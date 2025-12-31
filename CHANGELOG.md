# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.4]: https://github.com/gettranslatebot/translatebot-django/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/gettranslatebot/translatebot-django/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/gettranslatebot/translatebot-django/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/gettranslatebot/translatebot-django/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/gettranslatebot/translatebot-django/releases/tag/v0.1.0
