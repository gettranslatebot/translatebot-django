# Contributing to translatebot-django

Thanks for your interest in contributing! This guide will help you get started.

## Reporting Bugs

Open an issue on [GitHub Issues](https://github.com/gettranslatebot/translatebot-django/issues) with:

- A clear description of the problem
- Steps to reproduce
- Expected vs actual behavior
- Your Python, Django, and translatebot-django versions

## Development Setup

1. [Fork the repository](https://github.com/gettranslatebot/translatebot-django/fork) on GitHub

2. Clone your fork:

```bash
git clone https://github.com/YOUR_USERNAME/translatebot-django.git
cd translatebot-django
```

3. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if you don't have it, then install dependencies:

```bash
uv sync --extra dev
```

## Running Tests

```bash
uv run pytest
```

With coverage:

```bash
uv run pytest -v --cov=translatebot_django --cov-report=term-missing
```

## Code Style

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting. Before submitting a PR:

```bash
uv run ruff check .
uv run ruff format .
```

## Submitting a Pull Request

1. Create a feature branch from `main`
2. Make your changes
3. Add or update tests as needed
4. Ensure all tests pass and linting is clean
5. Submit a pull request against `main`

## License

By contributing, you agree that your contributions will be licensed under the [Mozilla Public License 2.0](LICENSE).
