# translatebot-django

[![PyPI](https://img.shields.io/pypi/v/translatebot-django.svg)](https://pypi.org/project/translatebot-django/) [![Downloads](https://static.pepy.tech/badge/translatebot-django)](https://pepy.tech/project/translatebot-django) [![Tests](https://github.com/gettranslatebot/translatebot-django/actions/workflows/test.yml/badge.svg)](https://github.com/gettranslatebot/translatebot-django/actions/workflows/test.yml) [![Coverage](https://codecov.io/gh/gettranslatebot/translatebot-django/graph/badge.svg)](https://codecov.io/gh/gettranslatebot/translatebot-django) [![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](https://opensource.org/licenses/MPL-2.0)

[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://www.python.org/) [![Django](https://img.shields.io/badge/django-4.2%20%7C%205.0%20%7C%205.1%20%7C%205.2%20%7C%206.0%20%7C%206.1-green)](https://www.djangoproject.com/)

⚡ **Translate Django .po files and model fields with AI.** Repeatable, consistent, and pennies per language.

Documentation: **[https://translatebot.dev/docs/](https://translatebot.dev/docs/)**

## The Problem

Translating a Django app sounds simple until it isn't:

- **Manual workflow doesn't scale.** Copy strings to Google Translate, paste back, fix placeholders, repeat for every language. It works for 20 strings. It falls apart at 200.
- **AI assistants work once, but not repeatedly.** You can ask ChatGPT or Claude Code to translate a `.po` file, and it'll do a decent job. Once. Next sprint, when 15 new strings appear, you're prompting from scratch, re-translating the whole file, and hoping it stays consistent.
- **SaaS translation platforms are expensive overkill.** Paid localization services charge per-word subscriptions and come with portals, review workflows, and team features you don't need for a solo project or small team.

## Why TranslateBot

TranslateBot is a dedicated tool that sits between "do it by hand" and "pay for a platform":

- **Incremental.** Only translates new and changed strings. Add 10 strings in a sprint, pay for 10 strings, not the whole file.
- **Consistent.** A `TRANSLATING.md` file in your repo acts as a version-controlled glossary: terminology, tone, brand rules. Every translation run uses it.
- **Cost-efficient.** Batches strings into optimized API requests. A typical app costs under $0.01 per language with GPT-4o-mini.
- **Scales to many languages.** One command translates all your configured languages. Adding a new locale is a one-liner.
- **Automatable.** A CLI command you can script or hook into your workflow. No browser, no portal.
- **Placeholder-safe.** Keeps `%(name)s`, `{0}`, `%s`, and HTML tags intact: the model is instructed to preserve them, and with DeepL they're swapped for protected tokens before translation. `compilemessages` catches any slip.
- **Grammar-aware.** With LLM providers, every plural form of the target language gets its own translation (Polish, Russian, Arabic, …), and `pgettext` contexts keep "May" the month apart from "May" the verb.

## Installation

For PO file translation, TranslateBot is a development tool; install it as a dev dependency:

```bash
uv add --dev translatebot-django
```

For model translation at runtime, install it as a regular dependency instead (see [Python API docs](https://translatebot.dev/docs/usage/python-api/)):

```bash
uv add translatebot-django
```

Optional extras: `translatebot-django[deepl]` for the [DeepL](https://translatebot.dev/docs/integrations/deepl/) provider, and `translatebot-django[modeltranslation]` for [model field translation](https://translatebot.dev/docs/usage/model-translation/).

### Supported versions

Each Django series is tested against the Python versions Django itself supports:

| Django | Python |
| ------ | ------------------------ |
| 4.2    | 3.10, 3.11, 3.12         |
| 5.0    | 3.10, 3.11, 3.12         |
| 5.1    | 3.10, 3.11, 3.12, 3.13   |
| 5.2    | 3.10, 3.11, 3.12, 3.13, 3.14 |
| 6.0    | 3.12, 3.13, 3.14         |
| 6.1    | 3.12, 3.13, 3.14         |

## Quick Start

```python
# settings.py
import os

INSTALLED_APPS = [
    # ...
    "translatebot_django",
]

LANGUAGES = [("en", "English"), ("nl", "Dutch"), ("de", "German")]

TRANSLATEBOT_API_KEY = os.getenv("TRANSLATEBOT_API_KEY")
# The default; any LiteLLM model works, e.g. "claude-sonnet-5"
TRANSLATEBOT_MODEL = "gpt-4o-mini"
```

The API key must belong to the provider of `TRANSLATEBOT_MODEL`.

```bash
# Extract strings into .po files
python manage.py makemessages -l nl -l de

# Preview what would be translated (no API calls)
python manage.py translate --dry-run

# Translate to all configured languages
python manage.py translate

# Compile for use
python manage.py compilemessages
```

## Features

- **Multiple AI Providers**: OpenAI, Anthropic, Google Gemini, Azure, [many more](https://docs.litellm.ai/docs/providers), and [DeepL](https://www.deepl.com/)
- **Smart Translation**: Preserves placeholders (`%(name)s`, `{0}`, `%s`) and HTML tags
- **Model Field Translation**: Supports [django-modeltranslation](https://github.com/deschler/django-modeltranslation)
- **CI Check**: `python manage.py check_translations` fails the build when strings are untranslated or fuzzy ([CI docs](https://translatebot.dev/docs/usage/ci/))
- **Python API**: `from translatebot_django import translate` for Celery tasks and scripts ([Python API docs](https://translatebot.dev/docs/usage/python-api/))
- **Flexible Configuration**: Django settings, environment variables, or CLI arguments
- **Well Tested**: 100% code coverage

## When to Use TranslateBot

For a one-off translation of 20 strings, ChatGPT works fine. TranslateBot is for **ongoing projects** with multiple languages where translations need to stay in sync as your code changes.

Use TranslateBot when:

- You're actively developing and strings change every sprint
- You support 3+ languages and want them all updated at once
- You want consistent terminology across translation runs
- You want translations done in seconds, not hours of manual work

## Documentation

For full documentation, visit **[translatebot.dev/docs/](https://translatebot.dev/docs/)**

- [Installation](https://translatebot.dev/docs/getting-started/installation/)
- [Configuration](https://translatebot.dev/docs/getting-started/configuration/)
- [PO File Translation](https://translatebot.dev/docs/usage/po-files/)
- [Translation Context (`TRANSLATING.md`)](https://translatebot.dev/docs/usage/translation-context/)
- [Model Translation](https://translatebot.dev/docs/usage/model-translation/)
- [Command Reference](https://translatebot.dev/docs/usage/command-reference/)
- [Python API](https://translatebot.dev/docs/usage/python-api/)
- [CI Integration](https://translatebot.dev/docs/usage/ci/)
- [Supported AI Models](https://translatebot.dev/docs/integrations/ai-models/)
- [DeepL](https://translatebot.dev/docs/integrations/deepl/)
- [FAQ](https://translatebot.dev/docs/faq/)

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

```bash
# Setup
git clone https://github.com/gettranslatebot/translatebot-django.git
cd translatebot-django
uv sync --extra dev

# Run tests
uv run pytest
```

## License

This project is licensed under the Mozilla Public License 2.0 - see the [LICENSE](LICENSE) file for details.

## Credits

- Built with [LiteLLM](https://github.com/BerriAI/litellm) for universal LLM provider support
- Uses [polib](https://github.com/izimobil/polib) for `.po` file manipulation

---

Made with ❤️ for the Django community
