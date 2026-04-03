# ai-translate

**Zero-config AI localization for developers. One command. Every platform.**

```bash
pip install ai-translate
cd your-project/
ai-translate
```

That's it. Your app speaks 10 languages. No accounts. No dashboards. No config files.

---

## What It Does

`ai-translate` scans your project, finds translatable strings, detects missing translations, translates them using AI, and writes the results directly to your locale files.

**One command. Zero setup. Works across 6 platforms.**

## Supported Platforms

| Platform | Detection | Source Format | Plural Support |
|----------|-----------|---------------|----------------|
| **Django** | `manage.py` | `.po` files | `ngettext()` with `msgid_plural` |
| **Flask** | `app.py` with Flask import | `.po` files (Babel) | `ngettext()` with `msgid_plural` |
| **FastAPI** | `main.py` with FastAPI import | `.po` files (Babel) | `ngettext()` with `msgid_plural` |
| **Flutter** | `pubspec.yaml` | `.arb` files | ICU MessageFormat |
| **Android** | `app/build.gradle` | `strings.xml` | `<plurals>` XML elements |
| **iOS** | `*.xcodeproj` | `.xcstrings` / `.strings` | `.xcstrings` variations |

The platform is detected automatically from your project structure. You never specify it.

## Supported AI Providers

| Provider | Env Variable | Model | Quality |
|----------|-------------|-------|---------|
| **Claude** (Anthropic) | `ANTHROPIC_API_KEY` | claude-sonnet-4 | Excellent |
| **OpenAI** | `OPENAI_API_KEY` | gpt-4o | High |
| **Google Gemini** | `GOOGLE_GEMINI_KEY` | gemini-2.5-flash | High |
| **OpenRouter** | `OPENROUTER_API_KEY` | 100+ models | Varies |
| **Mistral** | `MISTRAL_API_KEY` | mistral-large | High |

The provider is auto-detected from your environment variables. If no key is found, the tool guides you through setup interactively.

## Installation

```bash
# Core tool
pip install ai-translate

# With a specific provider
pip install "ai-translate[claude]"
pip install "ai-translate[openai]"
pip install "ai-translate[gemini]"

# With all providers
pip install "ai-translate[all]"
```

**Requirements:** Python 3.10+

## Quick Start

### 1. Install

```bash
pip install -U ai-translate
```

### 2. Set an API key (any ONE of these)

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # Claude (recommended)
export OPENAI_API_KEY=sk-...             # OpenAI
export GOOGLE_GEMINI_KEY=...             # Gemini (cheapest)
```

Or skip this step entirely — the tool will ask you interactively:

```
⚠ No API key detected. Let's set one up!

┌─────────────────────────────────────────────────┐
│         SELECT TRANSLATION ENGINE                │
├─────────────────────────────────────────────────┤
│  [1]  ⚡  Claude (Anthropic)  · Recommended     │
│  [2]  🧠  OpenAI GPT          · Powerful         │
│  [3]  🔥  OpenRouter           · 100+ Models     │
│  [4]  ✦   Google Gemini        · Ultra Fast      │
│  [5]  🇪🇺  Mistral AI          · EU Languages    │
└─────────────────────────────────────────────────┘

→ Enter your choice: 1

🔑 Claude (Anthropic) API Key Required

  Get your key at:
  https://console.anthropic.com/settings/keys

→ Enter your Claude API key: sk-ant-...

● API key saved to .env
```

### 3. Run

```bash
cd your-project/
ai-translate
```

Done. Your locale files are updated.

## How It Works

```
ai-translate
     │
     ├── Step 1: Detect platform (Django/Flask/FastAPI/Flutter/Android/iOS)
     ├── Step 2: Load environment + API key
     ├── Step 3: Scan source code for translatable strings
     ├── Step 4: Detect target languages from locale files
     ├── Step 5: Find missing translations
     ├── Step 6: Translate via AI (with caching, batching, retry)
     ├── Step 7: Write translations to locale files (atomic writes)
     └── Step 8: Compile .mo files (Django/Flask/FastAPI only)
```

### String Detection by Platform

**Django / Flask / FastAPI** — AST parses all `.py` files:
```python
_("Welcome")                          # gettext
gettext_lazy("Submit")                # lazy gettext
ngettext("%(count)d item", "%(count)d items", count)  # plurals
```

**Flutter** — Reads `.arb` files or scans `.dart` source:
```json
{"welcome": "Welcome back", "itemCount": "{count, plural, =0{No items} =1{1 item} other{{count} items}}"}
```

**Android** — Parses `strings.xml` or scans Kotlin/Java:
```xml
<string name="welcome">Welcome</string>
<plurals name="item_count">
    <item quantity="one">%d item</item>
    <item quantity="other">%d items</item>
</plurals>
```

**iOS** — Reads `.xcstrings` / `.strings` or scans Swift:
```swift
"Hello".localized
NSLocalizedString("Welcome", comment: "")
```

## CLI Reference

### Basic Usage

```bash
ai-translate                          # Auto-detect everything, translate
ai-translate --provider claude        # Use Claude specifically
ai-translate --provider openrouter --model deepseek/deepseek-chat-v3-0324  # Budget mode
ai-translate --dry-run                # Preview without writing files
ai-translate --details                # Show comprehensive help guide
```

### All Flags

| Flag | Description |
|------|-------------|
| `--provider PROVIDER` | Force a provider: `claude`, `openai`, `openrouter`, `gemini`, `mistral`, `skip` |
| `--model MODEL_ID` | OpenRouter model ID (e.g., `deepseek/deepseek-chat-v3-0324`) |
| `--dry-run` | Preview what would be translated. No API calls, no file writes. |
| `--estimate` | Show cost comparison across all providers, then exit. |
| `--review` | Interactive review: accept, edit, or skip each translation before writing. |
| `--min-quality N` | Quality gate (0-100). Translations below threshold marked as fuzzy. |
| `--lang CODES` | Translate only specific languages: `--lang es,fr,de` |
| `--glossary PATH` | Path to glossary JSON for consistent terminology. |
| `--context TEXT` | Project context injected into AI prompt: `--context "medical app"` |
| `--check` | Regression detection: re-translate cached strings, flag any drift. |
| `--changed-only` | Only scan files changed since last run (uses `git diff`). |
| `--quiet` | Zero output, exit code only. For CI/CD. |
| `--json` | JSON output. For CI/CD pipelines that parse results. |
| `--batch-size N` | Override auto batch size (default: 10-25 based on string count). |
| `--no-auto-install` | Don't auto-install missing provider SDKs. |
| `--debug` | Verbose debug logging. |
| `--details` | Show detailed usage guide. |

## Features

### Cost Estimation

See what translation will cost BEFORE spending money:

```bash
ai-translate --estimate
```

```
╭───────────────────────┬───────────┬───────────┬───────────╮
│ Provider              │ Est. Cost │ Est. Time │  Quality  │
├───────────────────────┼───────────┼───────────┼───────────┤
│ Claude (Anthropic)    │   $0.0025 │   ~12s    │ Excellent │
│ OpenAI GPT-4o         │   $0.0030 │   ~15s    │   High    │
│ Google Gemini         │   $0.0001 │    ~6s    │   High    │
│ DeepSeek (OpenRouter) │ $0.0000 * │    ~8s    │   Good    │
╰───────────────────────┴───────────┴───────────┴───────────╯
```

### Glossary (Consistent Terminology)

Create `.ai-translate-glossary.json` in your project root:

```json
{
  "Dashboard": {"es": "Panel de control", "fr": "Tableau de bord"},
  "Submit": {"es": "Enviar", "fr": "Soumettre"},
  "Sign in": {"es": "Iniciar sesion", "fr": "Se connecter"}
}
```

The tool auto-discovers this file. Terms are injected into the AI prompt as mandatory terminology.

### Interactive Review

Review translations before they're written:

```bash
ai-translate --review
```

```
[1/42] "Welcome back"
├── es: "Bienvenido de nuevo"      Score: 95%
├── fr: "Bon retour"               Score: 93%
└── ja: "おかえりなさい"              Score: 91%

[A]ccept / [E]dit / [S]kip
```

### Quality Gate

Only accept high-confidence translations:

```bash
ai-translate --min-quality 80
```

Translations scoring below 80% are marked as fuzzy (not used at runtime in Django). You review and approve them manually.

### Regression Detection

Check if AI model updates changed your existing translations:

```bash
ai-translate --check
```

```
⚠ TRANSLATION DRIFT DETECTED

  "Cancel" (es):
    Cached:  "Cancelar"
    New AI:  "Anular"
```

### Provider Failover

If your primary provider is down, the tool automatically tries the next available provider:

```
● Provider: Claude (auto-detected)
ℹ Failover chain: OpenAI GPT, Google Gemini
```

If Claude fails after 3 retries → OpenAI is tried → if that fails → Gemini is tried.

### Placeholder Validation

After translation, the tool validates that all placeholders are preserved:

```python
# Source:
_("%(count)d Posts deleted by %(name)s")

# If AI drops a placeholder, you get warned:
▲ [es] "%(count)d Posts..." → Missing placeholders: %(name)s
```

This prevents runtime crashes from broken format strings.

## Caching

### Zero Project Pollution

The tool stores ALL data in `~/.ai-translate/`. Nothing is written to your project folder (except `.env` for API keys and locale files for translations).

```
~/.ai-translate/
├── global_cache.json              ← Shared across ALL your projects
└── projects/
    ├── a1b2c3d4e5f6/
    │   ├── cache.json             ← Project-specific translations
    │   └── meta.json              ← Project path, platform, last run
    └── ...
```

### Cross-Project Sharing

Translate "Submit" → "Enviar" in Project A → Project B gets it for FREE. The global cache means common strings (Submit, Cancel, Save, Delete, Settings, etc.) are translated once and reused everywhere.

### How Caching Saves Money

```
First run:   42 strings × 5 languages = 210 API calls     → $0.18
Second run:  3 new strings × 5 languages = 15 API calls   → $0.01 (39 cached)
Third run:   0 new strings = 0 API calls                   → $0.00
```

## CI/CD Integration

### GitHub Actions — Simple

```yaml
# .github/workflows/translate.yml
name: Auto Translate
on:
  push:
    branches: [main]

jobs:
  translate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install ai-translate
      - name: Translate
        run: ai-translate --quiet --provider claude
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - name: Commit translations
        run: |
          git config user.name "ai-translate"
          git config user.email "bot@ai-translate.dev"
          git add locale/ translations/ lib/l10n/ app/src/main/res/
          git diff --staged --quiet || git commit -m "chore: auto-translate"
          git push
```

### GitHub Actions — With Slack Notification

```yaml
      - name: Translate
        id: translate
        run: |
          RESULT=$(ai-translate --json --provider claude)
          echo "translated=$(echo $RESULT | jq -r '.translated')" >> $GITHUB_OUTPUT
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

      - name: Notify Slack
        if: steps.translate.outputs.translated != '0'
        uses: slackapi/slack-github-action@v1
        with:
          payload: '{"text": "Translated ${{ steps.translate.outputs.translated }} strings"}'
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK }}
```

### GitHub Actions — Block PR if Translations Missing

```yaml
# .github/workflows/translate-check.yml
name: Translation Check
on:
  pull_request:
    branches: [main]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install ai-translate
      - name: Check translations
        run: |
          RESULT=$(ai-translate --json --provider skip)
          MISSING=$(echo "$RESULT" | jq -r '.missing_count')
          if [ "$MISSING" -gt "0" ]; then
            echo "::error::$MISSING strings need translation"
            exit 1
          fi
```

### CI/CD Output Modes

| Flag | Output | Exit Code | Use Case |
|------|--------|-----------|----------|
| (none) | Rich terminal UI | 0/1 | Developer in terminal |
| `--quiet` | Zero bytes | 0 = success, 1 = failure | Pass/fail gate |
| `--json` | `{"status": "...", "translated": N, ...}` | 0/1 | Parse results, post to Slack |

### JSON Output Format

```json
{
  "status": "success",
  "platform": "django",
  "source_count": 42,
  "missing_count": 8,
  "translated": 8,
  "approved": 6,
  "fuzzy": 2,
  "lang_count": 3,
  "languages": ["es", "fr", "de"],
  "elapsed": 12.4,
  "provider": "Claude (Anthropic)"
}
```

## Plural Support

Every platform's plural system is fully supported:

### Django / Flask / FastAPI

```python
from django.utils.translation import ngettext
msg = ngettext("%(count)d item", "%(count)d items", count)
```

Generated `.po` file:
```po
msgid "%(count)d item"
msgid_plural "%(count)d items"
msgstr[0] "%(count)d elemento"
msgstr[1] "%(count)d elementos"
```

The tool sets the correct `Plural-Forms` header per language (Russian gets 3 forms, Arabic gets 6, Japanese gets 1).

### Flutter (ICU MessageFormat)

```json
{
  "itemCount": "{count, plural, =0{No items} =1{1 item} other{{count} items}}"
}
```

ICU plural syntax is preserved during translation. The AI is instructed to translate each variant inside the braces.

### Android

```xml
<plurals name="item_count">
    <item quantity="one">%d item</item>
    <item quantity="other">%d items</item>
</plurals>
```

### iOS (.xcstrings)

```json
{
  "item_count": {
    "localizations": {
      "es": {
        "variations": {
          "plural": {
            "one": {"stringUnit": {"value": "%lld elemento"}},
            "other": {"stringUnit": {"value": "%lld elementos"}}
          }
        }
      }
    }
  }
}
```

## Project Structure

```
ai-translate/
├── pyproject.toml                     # Package config
├── ai_translate/
│   ├── __init__.py                    # Version
│   ├── config.py                      # Centralized constants
│   ├── cli/
│   │   ├── main.py                    # CLI entry point + pipeline
│   │   └── ui.py                      # Rich terminal UI
│   ├── platforms/
│   │   ├── __init__.py                # Platform detection
│   │   ├── _shared.py                 # Shared AST utils, PO helpers, plural forms
│   │   ├── django.py                  # Django handler
│   │   ├── flask_fastapi.py           # Flask / FastAPI handler
│   │   ├── flutter.py                 # Flutter handler
│   │   ├── android.py                 # Android handler
│   │   └── ios.py                     # iOS handler
│   └── services/
│       ├── cache.py                   # Translation cache (global + per-project)
│       ├── env_manager.py             # .env management
│       └── translators/
│           ├── __init__.py            # Provider registry + factory
│           ├── base.py                # Base class, prompt builder, retry, scoring
│           ├── claude.py              # Anthropic Claude
│           ├── openai_provider.py     # OpenAI GPT
│           ├── openrouter.py          # OpenRouter (100+ models)
│           ├── gemini.py              # Google Gemini
│           └── mistral.py             # Mistral AI
└── tests/
    ├── conftest.py                    # 8 shared fixtures
    ├── test_platforms.py              # Platform detection + handler tests
    ├── test_translators.py            # Provider + prompt tests
    ├── test_cache.py                  # Cache + global sharing tests
    ├── test_cli.py                    # CLI flag tests
    ├── test_plurals.py                # Plural support tests (all platforms)
    └── test_business_logic.py         # Real-world scenario tests
```

## FAQ

### How much does it cost?

The tool itself is free. You only pay for AI API calls:

| Provider | 500 strings, 5 languages | Quality |
|----------|-------------------------|---------|
| Gemini Flash | ~$0.01 | High |
| DeepSeek (via OpenRouter) | ~$0.02 | Good |
| Claude | ~$0.20 | Excellent |
| OpenAI GPT-4o | ~$0.25 | High |

Cached strings cost $0. The global cache means common strings are translated once across all your projects.

### Does it overwrite my existing translations?

No. The tool only fills in MISSING translations. If a translation already exists (non-empty `msgstr` in `.po`, existing key in `.arb`, etc.), it is never touched.

### Does it create files in my project?

Only the locale files your platform needs (`.po`, `.arb`, `strings.xml`, `.xcstrings`). The translation cache is stored in `~/.ai-translate/`, not in your project.

The only non-locale file created is `.env` (if you enter an API key interactively), which is a standard developer convention.

### What if I don't have any locale files yet?

The tool creates them. For a brand new project with no locale directory, it will:
1. Scan your source code for translatable strings
2. Ask you for target languages (if it can't detect them)
3. Create the locale directories and files automatically

### Can I use it in a monorepo?

Yes. Run it from the root of the specific project you want to translate. The tool detects the platform from the current directory.

### What about API key security?

- Keys are stored in `.env` (add to `.gitignore`)
- Keys are masked in terminal output (first 4 + dots + last 4)
- Keys are never written to the cache
- In CI/CD, use environment secrets (e.g., GitHub Secrets)

### What Python versions are supported?

Python 3.10, 3.11, 3.12, 3.13.

## License

MIT License. See [LICENSE](LICENSE) for details.

## Author

Built by [Happy-Cyber](https://github.com/Happy-cyber).
