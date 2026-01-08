# translatebot-django

[![PyPI](https://img.shields.io/pypi/v/translatebot-django.svg)](https://pypi.org/project/translatebot-django/) [![Tests](https://github.com/gettranslatebot/translatebot-django/actions/workflows/test.yml/badge.svg)](https://github.com/gettranslatebot/translatebot-django/actions/workflows/test.yml) [![Coverage](https://codecov.io/gh/gettranslatebot/translatebot-django/graph/badge.svg)](https://codecov.io/gh/gettranslatebot/translatebot-django) [![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](https://opensource.org/licenses/MPL-2.0)

[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://www.python.org/) [![Django](https://img.shields.io/badge/django-4.2%20%7C%205.x%20%7C%206.0-green)](https://www.djangoproject.com/)

Automatic AI-powered translations for Django `.po` files using the power of LLMs.

## Features

- **Multiple AI Providers**: OpenAI, Anthropic, Google Gemini, Azure, and [many more](https://docs.litellm.ai/docs/providers)
- **Smart Translation**: Preserves placeholders (`%(name)s`, `{0}`, `%s`) and HTML tags
- **Model Field Translation**: Supports [django-modeltranslation](https://github.com/deschler/django-modeltranslation)
- **Flexible Configuration**: Django settings, environment variables, or CLI arguments
- **Well Tested**: 100% code coverage

## Installation

```bash
pip install translatebot-django
```

## Quick Start

```python
# settings.py
INSTALLED_APPS = [
    # ...
    'translatebot_django',
]

TRANSLATEBOT_API_KEY = "your-api-key-here"
```

```bash
# Translate to Dutch
python manage.py translate --target-lang nl

# Preview without saving
python manage.py translate --target-lang nl --dry-run
```

## Documentation

For full documentation, visit **[translatebot.dev/docs](https://translatebot.dev/docs)**

- [Installation](https://translatebot.dev/docs/getting-started/installation)
- [Configuration](https://translatebot.dev/docs/getting-started/configuration)
- [Command Reference](https://translatebot.dev/docs/usage/command-reference)
- [Model Translation](https://translatebot.dev/docs/usage/model-translation)
- [Supported AI Models](https://translatebot.dev/docs/integrations/ai-models)
- [FAQ](https://translatebot.dev/docs/faq)

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
