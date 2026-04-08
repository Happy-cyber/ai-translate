# Contributing to ai-translate

Thanks for your interest in contributing! Here's how you can help.

## Getting Started

```bash
git clone https://github.com/Happy-cyber/ai-translate.git
cd ai-translate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
```

## Linting

```bash
ruff check .
```

## How to Contribute

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Run tests (`pytest`)
5. Commit your changes
6. Push to your fork
7. Open a Pull Request

## What We Need Help With

- New platform support (React Native, Vue, etc.)
- Translation provider integrations
- Bug reports with real project examples
- Documentation improvements
- Translations of this README

## Reporting Bugs

Open an issue with:
- Your platform (Django, Flutter, etc.)
- Python version
- Output of `ai-translate --debug`
- What you expected vs what happened

## Code Style

- Follow existing patterns
- Run `ruff check .` before committing
- Keep functions focused and small
