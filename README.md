# translatebot-django

[![PyPI](https://img.shields.io/pypi/v/translatebot-django.svg)](https://pypi.org/project/translatebot-django/) [![Tests](https://github.com/gettranslatebot/translatebot-django/actions/workflows/test.yml/badge.svg)](https://github.com/gettranslatebot/translatebot-django/actions/workflows/test.yml) [![Coverage](https://codecov.io/gh/gettranslatebot/translatebot-django/graph/badge.svg)](https://codecov.io/gh/gettranslatebot/translatebot-django) [![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](https://opensource.org/licenses/MPL-2.0)

[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://www.python.org/) [![Django](https://img.shields.io/badge/django-4.2%20%7C%205.x%20%7C%206.0-green)](https://www.djangoproject.com/)

Automatic AI-powered translations for Django `.po` files using the power of LLMs.

Translate your Django application's gettext messages and model field content automatically using state-of-the-art language models from OpenAI, Anthropic, Google, and many other providers.

## Features

- 🤖 **Multiple AI Providers**: Supports OpenAI, Anthropic Claude, Google Gemini, Azure OpenAI, and [many more models](https://docs.litellm.ai/docs/providers) via LiteLLM
- 🎯 **Smart Translation**: Preserves placeholders (`%(name)s`, `{0}`, `%s`) and HTML tags
- 🗄️ **Model Field Translation**: Supports [django-modeltranslation](https://github.com/deschler/django-modeltranslation) for translating database content
- ⚙️ **Flexible Configuration**: Django settings, environment variables, or command-line arguments
- 🔄 **Selective Translation**: Only translate empty entries or force re-translate all, saving costs
- 🧪 **Dry Run Mode**: Preview translations before applying them
- ✅ **Well Tested**: Comprehensive test suite with 100% code coverage
- 🚀 **Easy Integration**: Simple Django management command

## Installation

```bash
pip install translatebot-django
```

Or with poetry:

```bash
poetry add translatebot-django
```

Or with uv:

```bash
uv add translatebot-django
```

## Quick Start

1. **Add to your Django project:**

```python
# settings.py
INSTALLED_APPS = [
    # ...
    'translatebot_django',
]

# Configure API key and model
TRANSLATEBOT_API_KEY = os.getenv("your-api-key-here")
TRANSLATEBOT_MODEL = "gpt-4o-mini"  # Optional, defaults to gpt-4o-mini

# Optional: Define languages for automatic translation
LANGUAGES = [
    ('en', 'English'),
    ('de', 'German'),
    ('fr', 'French'),
    ('nl', 'Dutch'),
]
```

2. **Generate your `.po` files** (if you haven't already):

```bash
python manage.py makemessages -l de  # German
python manage.py makemessages -l fr  # French
python manage.py makemessages -l nl  # Dutch
```

3. **Translate automatically:**

```bash
# Translate to a specific language
python manage.py translate --target-lang nl

# Or translate to all configured LANGUAGES at once (if LANGUAGES is defined)
python manage.py translate
```

4. **Compile the translations:**

```bash
python manage.py compilemessages
```

## Configuration

### Django Settings

```python
# settings.py

# Required: Your API key
TRANSLATEBOT_API_KEY = "your-api-key-here"

# Optional: Model to use (default: gpt-4o-mini)
TRANSLATEBOT_MODEL = "gpt-4o-mini"
```

## Usage

### Basic Usage

```bash
# Translate to a specific language
python manage.py translate --target-lang fr

# Translate to all configured LANGUAGES at once (if LANGUAGES is defined in settings)
python manage.py translate

# Translate multiple specific languages
python manage.py translate --target-lang de
python manage.py translate --target-lang nl
```

### Advanced Options

```bash
# Preview translations without saving (dry run)
python manage.py translate --target-lang nl --dry-run

# Re-translate entries that already have translations
python manage.py translate --target-lang nl --overwrite

# Use a specific model
python manage.py translate --target-lang nl
```

### Model Field Translation (django-modeltranslation)

If you're using [django-modeltranslation](https://github.com/deschler/django-modeltranslation) to translate model fields, translatebot-django can automatically translate your database content too!

#### Installation

```bash
# Install with modeltranslation support
uv add translatebot-django[modeltranslation]

# Or install django-modeltranslation separately
uv add django-modeltranslation
```

#### Setup

```python
# settings.py
INSTALLED_APPS = [
    'modeltranslation',  # Must be before django.contrib.admin
    'translatebot_django',
    # ...
]
```

Register your models for translation:

```python
# myapp/translation.py
from modeltranslation.translator import register, TranslationOptions
from .models import Article

@register(Article)
class ArticleTranslationOptions(TranslationOptions):
    fields = ('title', 'content', 'description')
```

#### Usage

```bash
# Translate all registered model fields
python manage.py translate --target-lang nl --models

# Translate specific models only
python manage.py translate --target-lang de --models Article Product

# Preview without saving to database
python manage.py translate --target-lang fr --models --dry-run

# Re-translate existing content
python manage.py translate --target-lang de --models --overwrite
```

#### How It Works

1. Discovers all models registered with django-modeltranslation
2. Finds fields that need translation (empty target language fields by default)
3. Translates content from the base field to target language field
4. Updates the database using efficient bulk operations
5. Supports dry-run mode to preview before committing changes

#### Example

```python
# models.py
class Article(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    published = models.DateTimeField(auto_now_add=True)

# translation.py
@register(Article)
class ArticleTranslationOptions(TranslationOptions):
    fields = ('title', 'content')
```

After running migrations, django-modeltranslation creates additional fields:
- `title`, `title_de`, `title_nl`, `title_en`
- `content`, `content_de`, `content_nl`, `content_en`

Translate to German:
```bash
python manage.py translate --target-lang de --models Article
```

### Command Options

| Option          | Description                                                                                      | Default |
| --------------- | ------------------------------------------------------------------------------------------------ | ------- |
| `--target-lang` | Target language code. Required unless `LANGUAGES` is defined in settings.                        | -       |
| `--dry-run`     | Preview without saving                                                                           | `False` |
| `--overwrite`   | Re-translate existing entries                                                                    | `False` |
| `--models`      | Translate model fields (only available with django-modeltranslation installed)                   | -       |

**Notes:**
- If you define `LANGUAGES` in your Django settings, `--target-lang` becomes optional. Without it, the command will translate to all configured languages.
- The `--models` option is only available when django-modeltranslation is installed and configured.

## Supported Models

Thanks to [LiteLLM](https://github.com/BerriAI/litellm), you can use models from many providers:

### OpenAI
```python
TRANSLATEBOT_MODEL = "gpt-4o-mini"      # Recommended, fast and cheap
TRANSLATEBOT_MODEL = "gpt-4o"           # More capable
TRANSLATEBOT_MODEL = "gpt-4-turbo"      # Good balance
```

### Anthropic Claude
```python
TRANSLATEBOT_MODEL = "claude-sonnet-4-5-20250929"  # Latest Sonnet (recommended)
TRANSLATEBOT_MODEL = "claude-opus-4-5-20251101"    # Latest Opus (most capable)
TRANSLATEBOT_MODEL = "claude-3-5-sonnet-20240620"  # Claude 3.5 Sonnet
TRANSLATEBOT_MODEL = "claude-3-haiku-20240307"     # Fast and cheap
```

### Google Gemini

```python
TRANSLATEBOT_MODEL = "gemini/gemini-2.5-flash"  # Latest Flash (recommended)
TRANSLATEBOT_MODEL = "gemini/gemini-3-flash-preview"  # Gemini 3 Preview
```

### Azure OpenAI
```python
TRANSLATEBOT_MODEL = "azure/gpt-4o-mini"
```

See the [LiteLLM providers documentation](https://docs.litellm.ai/docs/providers) for the complete list.

## How It Works

1. Reads your `.po` files from the locale directory
2. Identifies untranslated entries (empty `msgstr`)
3. Sends each message to the configured AI model with instructions to:
   - Preserve all placeholders and formatting
   - Maintain HTML tags exactly
   - Return only the translation
4. Updates the `.po` file with translations
5. Skips obsolete entries automatically

## Example Workflow

```bash
# 1. Create your translatable strings in code
# myapp/views.py
from django.utils.translation import gettext as _

def my_view(request):
    message = _("Welcome to %(site_name)s!")
    # ...

# 2. Configure your languages in settings.py
LANGUAGES = [
    ('en', 'English'),
    ('fr', 'French'),
    ('de', 'German'),
    ('nl', 'Dutch'),
]

# 3. Generate .po files
python manage.py makemessages -l fr -l de -l nl

# 4. Translate automatically to all languages at once
python manage.py translate

# Or translate to specific languages
python manage.py translate --target-lang fr
python manage.py translate --target-lang de

# 5. Compile messages
python manage.py compilemessages

# 6. Your app is now multilingual! 🎉
```

## Development

### Setup

```bash
# Clone the repository
git clone https://github.com/gettranslatebot/translatebot-django.git
cd translatebot-django

# Install with dev dependencies using uv
uv sync --extra dev

# Or with pip
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=translatebot_django --cov-report=html

# Run specific test
uv run pytest tests/test_translate_command.py::TestTranslateText -v
```

### Testing with Different Django Versions

```bash
# Test with Django 4.2
uv pip install "Django~=4.2.0"
uv run pytest

# Test with Django 5.2
uv pip install "Django~=5.2.0"
uv run pytest
```

## Requirements

- Python 3.9+
- Django 4.2+
- LiteLLM 1.80.0+

## FAQ

### How much does it cost?

Costs depend on your chosen model and the amount of text to translate:

- **GPT-4o-mini**: ~$0.15 per million input tokens (~750k words)
- **Claude 3.5 Haiku**: ~$0.80 per million input tokens
- **Claude 3.5 Sonnet**: ~$3 per million input tokens

For a typical small-to-medium app with 500 translatable strings (~10,000 words), costs are usually under $0.01 per language with GPT-4o-mini.

### Can I review translations before they're saved?

Yes! Use the `--dry-run` flag:

```bash
python manage.py translate --target-lang nl --dry-run
```

This shows you all translations without saving them to the `.po` file.

### What if I want to update existing translations?

Use the `--overwrite` flag:

```bash
python manage.py translate --target-lang nl --overwrite
```

By default, only empty entries are translated.

### Does it preserve Django template placeholders?

Yes! The AI is specifically instructed to preserve:
- Named placeholders: `%(name)s`, `%(count)d`
- Positional placeholders: `%s`, `%d`
- Format strings: `{0}`, `{name}`
- HTML tags: `<strong>`, `<a href="...">`, etc.

### Can I use my own prompts?

Currently, the prompt is built-in to ensure reliable placeholder preservation. If you need custom prompts, please open an issue to discuss your use case.

### Which models work best for translation?

Based on testing:
- **Best quality**: Claude 3.5 Sonnet, GPT-4o
- **Best value**: GPT-4o-mini, Claude 3.5 Haiku
- **Best for Asian languages**: GPT-4o, Claude 3.5 Sonnet

Start with GPT-4o-mini for most use cases.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`uv run pytest`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## License

This project is licensed under the Mozilla Public License 2.0 - see the [LICENSE](LICENSE) file for details.

## Credits

- Built with [LiteLLM](https://github.com/BerriAI/litellm) for universal LLM provider support
- Uses [polib](https://github.com/izimobil/polib) for `.po` file manipulation
- Inspired by the need to make Django internationalization easier

## Support

- 🐛 [Issue Tracker](https://github.com/gettranslatebot/translatebot-django/issues)

---

Made with ❤️ for the Django community
