# translatebot-django

[![PyPI](https://img.shields.io/pypi/v/translatebot-django.svg)](https://pypi.org/project/translatebot-django/) [![Downloads](https://static.pepy.tech/badge/translatebot-django)](https://pepy.tech/project/translatebot-django) [![Tests](https://github.com/gettranslatebot/translatebot-django/actions/workflows/test.yml/badge.svg)](https://github.com/gettranslatebot/translatebot-django/actions/workflows/test.yml) [![Coverage](https://codecov.io/gh/gettranslatebot/translatebot-django/graph/badge.svg)](https://codecov.io/gh/gettranslatebot/translatebot-django) [![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](https://opensource.org/licenses/MPL-2.0)

[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://www.python.org/) [![Django](https://img.shields.io/badge/django-4.2%20%7C%205.0%20%7C%205.1%20%7C%205.2%20%7C%206.0%20%7C%206.1-green)](https://www.djangoproject.com/)

AI translation for Django, covering both your `.po` files and the content in your database. Run one command after each change and only the new text gets translated.

Documentation: **[https://translatebot.dev/docs/](https://translatebot.dev/docs/)**

## The problem

Static strings are the easy part of translating a Django app. `makemessages` collects them into `.po` files, and plenty of tools can fill those in.

Database content is harder. Editors write product names, blog posts and category pages after you deploy, so that text never passes through gettext. With django-modeltranslation every language gets its own column, and someone has to work out which rows are missing which language. A translated slug still has to be a valid slug. Some of your existing translations may be machine output themselves, which makes them a poor source for the next language.

The usual options don't fit:

- Copying text into Google Translate works for 20 strings. At 200 it falls apart, and you fix every broken placeholder by hand.
- ChatGPT or Claude Code will translate a `.po` file well enough once. Next sprint you're prompting from scratch again, re-translating the whole file and hoping the terminology still matches.
- Localization platforms charge per word. You also get portals and review workflows that a solo developer or small team doesn't need.

## What TranslateBot does

TranslateBot adds a `translate` management command to your project. It translates whatever is missing and leaves existing translations alone.

### Database content

With [django-modeltranslation](https://github.com/deschler/django-modeltranslation) installed, `python manage.py translate --models` fills the empty language columns of every registered model. Pass model names to limit it to those models.

- It translates from your default language whenever that column has text, since the other languages may already be machine translations. If it's empty, the first language with content is used.
- Rows created before you installed modeltranslation only have text in the original column. TranslateBot reads it from there.
- Slug fields are slugified again and cut to their `max_length` at a word boundary. File and image fields are skipped.
- Columns that already have a translation stay as they are unless you pass `--overwrite`.

New content keeps arriving after you deploy, so the same function is available from Python. Call it from a Celery task or a signal handler when editors publish (see the [Python API docs](https://translatebot.dev/docs/usage/python-api/)).

### PO files

- Only new and changed strings are translated. Add 10 strings in a sprint and you pay for 10, not the whole file.
- A `TRANSLATING.md` file in your repo holds your glossary, tone and brand rules. Every run uses it, so a term you translated last month comes out the same today.
- Placeholders such as `%(name)s`, `{0}` and `%s` and HTML tags are kept intact. LLM providers are told to preserve them, and DeepL gets them as protected tokens.
- Every translation is checked before it's written, using the rules `compilemessages` applies to `python-format` and `python-brace-format` strings. A translation that drops or changes a placeholder is retried once. If it fails again, an untranslated entry gets it as a `#, fuzzy` draft. `compilemessages` skips fuzzy entries, so a bad translation never breaks your build, and the run ends with a list of drafts to review.
- With LLM providers, each plural form of the target language gets its own translation (Polish, Russian, Arabic, …). `pgettext` contexts keep "May" the month apart from "May" the verb.

### Everything else

- It works with OpenAI, Anthropic, Google Gemini, Azure and [any provider LiteLLM supports](https://docs.litellm.ai/docs/providers), or with [DeepL](https://www.deepl.com/).
- One run covers every language in `LANGUAGES`. Adding a locale takes one line in your settings.
- Strings are batched into as few API requests as possible. A typical app costs under $0.01 per language with GPT-4o-mini.
- `python manage.py check_translations` fails your CI build when strings are untranslated or fuzzy ([CI docs](https://translatebot.dev/docs/usage/ci/)).
- You configure it in Django settings, and `--llm-model` overrides the model for a single run. The API key can also come from the `TRANSLATEBOT_API_KEY` environment variable.
- The test suite covers 100% of the code.

## Installation

To translate PO files only, install TranslateBot as a dev dependency:

```bash
uv add --dev translatebot-django
```

To translate model fields from your running app, install it as a regular dependency instead (see the [Python API docs](https://translatebot.dev/docs/usage/python-api/)):

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

## Quick start

```python
# settings.py
import os

INSTALLED_APPS = [
    # ...
    "translatebot_django",
]

LANGUAGES = [("en", "English"), ("nl", "Dutch"), ("de", "German")]

TRANSLATEBOT_API_KEY = os.getenv("TRANSLATEBOT_API_KEY")
# The default; any LiteLLM model works, e.g. "claude-sonnet-5-5"
TRANSLATEBOT_MODEL = "gpt-4o-mini"
```

The API key must belong to the provider of `TRANSLATEBOT_MODEL`. To use DeepL instead, set `TRANSLATEBOT_PROVIDER = "deepl"`, put your DeepL key in `TRANSLATEBOT_API_KEY` and leave out `TRANSLATEBOT_MODEL`.

```bash
# Extract strings into .po files
python manage.py makemessages -l nl -l de

# Preview what would be translated (no API calls, but the key must be set)
python manage.py translate --dry-run

# Translate to all configured languages
python manage.py translate

# Compile for use
python manage.py compilemessages

# Fill the empty language columns of your modeltranslation models
python manage.py translate --models
```

## When to use TranslateBot

For a one-off translation of 20 strings, ChatGPT works fine. TranslateBot is for ongoing projects where translations have to keep up with your code and your content.

It's a good fit when:

- Your strings change every sprint and you're tired of re-translating whole files.
- Editors add content to your database in one language and your users read it in others.
- You support three or more languages and want them all updated in one run.
- You want the same terminology every time, without pasting a glossary into a prompt.

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

Contributions are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers the fork workflow, how to report a bug and the lint steps to run before you open a pull request. To get a local setup running:

```bash
git clone https://github.com/gettranslatebot/translatebot-django.git
cd translatebot-django
uv sync --extra dev
uv run pytest
```

## License

This project is licensed under the Mozilla Public License 2.0. See the [LICENSE](LICENSE) file for details.

## Credits

- Built with [LiteLLM](https://github.com/BerriAI/litellm) for universal LLM provider support
- Uses [polib](https://github.com/izimobil/polib) for `.po` file manipulation

---

Made with ❤️ for the Django community
