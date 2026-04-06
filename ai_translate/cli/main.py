"""CLI entry point — the single ``ai-translate`` command."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections import defaultdict
from math import ceil
from pathlib import Path

from ai_translate import __version__
from ai_translate.cli import ui
from ai_translate.platforms import PLATFORMS, detect_platform, get_platform_handler
from ai_translate.services import cache as cache_mod
from ai_translate.services import env_manager
from ai_translate.services.env_manager import ALL_ENV_KEYS, API_KEY_URLS, PROVIDER_ENV_KEYS
from ai_translate.services.translators import (
    OPENROUTER_MODELS,
    PROVIDER_PRIORITY,
    PROVIDER_SDK_MAP,
    auto_detect_provider,
    get_translator,
)
from ai_translate.services.translators.base import estimate_tokens, score_translations, validate_placeholders

log = logging.getLogger(__name__)

# ── Trusted packages for auto-install ─────────────────────────────────

_TRUSTED_PACKAGES = frozenset(pip for _, pip in PROVIDER_SDK_MAP.values())

# ── Global interrupt state ────────────────────────────────────────────

_interrupted = False
_lock_file = None


# ── SDK helpers ───────────────────────────────────────────────────────


def _check_sdk_installed(provider: str) -> bool:
    mod_name, _ = PROVIDER_SDK_MAP.get(provider, ("", ""))
    if not mod_name:
        return True
    try:
        __import__(mod_name)
        return True
    except ImportError:
        return False


def _get_sdk_install_hint(provider: str) -> str:
    _, pip_name = PROVIDER_SDK_MAP.get(provider, ("", ""))
    return f"pip install {pip_name}" if pip_name else ""


def _auto_install_sdk(provider: str) -> bool:
    """Auto-install missing SDK with visible progress."""
    _, pip_name = PROVIDER_SDK_MAP.get(provider, ("", ""))
    if not pip_name or pip_name not in _TRUSTED_PACKAGES:
        return False

    ui.show_info(f"Auto-installing [bold]{pip_name}[/]...")

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", "--quiet", pip_name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        # Show a spinner while installing
        from rich.progress import Progress, SpinnerColumn, TextColumn
        with Progress(
            SpinnerColumn("dots", style="bright_cyan"),
            TextColumn(f"[bold bright_cyan]Installing {pip_name}...[/]"),
            console=ui.console,
            transient=True,
        ) as progress:
            progress.add_task("install", total=None)
            stdout, stderr = proc.communicate(timeout=180)

        if proc.returncode == 0:
            return True

        # Show last 3 lines of error
        err_lines = stderr.strip().splitlines()
        for line in err_lines[-3:]:
            ui.show_error(f"  {line}")
        return False

    except subprocess.TimeoutExpired:
        proc.kill()
        ui.show_error("Installation timed out after 3 minutes.")
        return False
    except Exception as exc:
        ui.show_error(f"Installation failed: {exc}")
        return False


# ── Batch size computation ────────────────────────────────────────────


def _compute_batch_size(count: int) -> int:
    if count <= 10:
        return count
    if count <= 50:
        return 15
    if count <= 200:
        return 20
    return 25


# ── Lock file ─────────────────────────────────────────────────────────


def _acquire_lock() -> bool:
    global _lock_file
    import tempfile as _tmpmod
    lock_path = Path(_tmpmod.gettempdir()) / ".ai_translate.lock"
    try:
        import fcntl
        _lock_file = lock_path.open("w")
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file.write(str(os.getpid()))
        _lock_file.flush()
        return True
    except (ImportError, OSError):
        return True  # Windows or lock busy — proceed anyway


def _release_lock() -> None:
    global _lock_file
    if _lock_file:
        try:
            import fcntl
            fcntl.flock(_lock_file, fcntl.LOCK_UN)
            _lock_file.close()
            import tempfile as _tmpmod
            (Path(_tmpmod.gettempdir()) / ".ai_translate.lock").unlink(missing_ok=True)
        except (ImportError, OSError):
            pass
        _lock_file = None


def _handle_interrupt(signum, frame) -> None:
    global _interrupted
    if _interrupted:
        ui.show_warning("Force quit.")
        sys.exit(130)
    _interrupted = True
    ui.show_warning("Finishing current batch... (press Ctrl+C again to force quit)")


# ── Glossary loading ─────────────────────────────────────────────────


def _load_glossary(glossary_path: str | None, project_root: Path) -> dict | None:
    """Load glossary from explicit path or auto-discover in project root."""
    def _try_load(p: Path) -> dict | None:
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            log.warning("Glossary is not a JSON object: %s", p)
            return None
        except json.JSONDecodeError as exc:
            log.warning("Glossary has invalid JSON: %s — %s", p, exc)
            ui.show_warning(f"Glossary skipped (invalid JSON): {p}")
            return None

    if glossary_path:
        p = Path(glossary_path)
        if p.is_file():
            return _try_load(p)
        log.warning("Glossary file not found: %s", glossary_path)
        return None

    # Auto-discover
    auto_path = project_root / ".ai-translate-glossary.json"
    if auto_path.is_file():
        return _try_load(auto_path)
    return None


# ── Git changed files ────────────────────────────────────────────────


def _get_git_changed_files(project_root: Path) -> list[str] | None:
    """Return list of changed file paths relative to project root, or None on error."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, timeout=30,
            cwd=str(project_root),
        )
        if result.returncode == 0:
            files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
            return files
    except Exception:
        pass
    return None


# ── Argument parser ───────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-translate",
        description="Zero-config AI localization for developers. One command. Every platform.",
    )
    parser.add_argument(
        "--version", action="version", version=f"ai-translate {__version__}",
    )
    parser.add_argument(
        "--provider",
        choices=["claude", "openai", "openrouter", "gemini", "mistral", "skip"],
        default=None,
        help="AI provider to use (default: auto-detect from API keys)",
    )
    parser.add_argument(
        "--model", default=None, metavar="MODEL_ID",
        help="OpenRouter model ID (only with --provider openrouter)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be translated — no API calls, no file writes",
    )
    parser.add_argument(
        "--batch-size", type=int, default=0, metavar="N",
        help="Messages per API call (default: auto 10-25)",
    )
    parser.add_argument(
        "--no-auto-install", action="store_true",
        help="Do not auto-install missing provider SDKs",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable verbose debug logging",
    )

    # ── New flags ────────────────────────────────────────────────────
    parser.add_argument(
        "--review", action="store_true",
        help="Interactive review before writing translations",
    )
    parser.add_argument(
        "--estimate", action="store_true",
        help="Show cost estimate per provider then exit",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="No output, exit code only (for CI/CD)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="JSON output (for CI/CD)",
    )
    parser.add_argument(
        "--lang", type=str, default=None, metavar="CODES",
        help="Comma-separated language codes to translate (e.g., es,fr,de)",
    )
    parser.add_argument(
        "--min-quality", type=int, default=0, metavar="N",
        help="Quality gate threshold (0-100)",
    )
    parser.add_argument(
        "--glossary", type=str, default=None, metavar="PATH",
        help="Path to glossary JSON file",
    )
    parser.add_argument(
        "--context", type=str, default=None, metavar="TEXT",
        help="Project context for AI prompt",
    )
    parser.add_argument(
        "--workers", type=int, default=4, metavar="N",
        help="Parallel workers for translation (default: 4, max: 10)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Regression detection mode",
    )
    parser.add_argument(
        "--changed-only", action="store_true",
        help="Only scan files changed since last run",
    )
    parser.add_argument(
        "--details", action="store_true",
        help="Show detailed usage guide and exit",
    )

    return parser


_DETAILS_TEXT = """\
AI TRANSLATE — Zero-config AI localization for developers.

USAGE:
  ai-translate                          Auto-detect platform, translate everything
  ai-translate --provider claude        Use Claude specifically
  ai-translate --dry-run                Preview without writing files
  ai-translate --estimate               Show cost per provider, then exit
  ai-translate --review                 Interactive review before writing
  ai-translate --check                  Detect translation drift
  ai-translate --quiet                  CI/CD mode (exit code only)
  ai-translate --json                   CI/CD mode (JSON output)
  ai-translate --workers 8              Parallel translation (2-10 workers)

PLATFORMS (auto-detected):
  Django        manage.py                      .po files
  Flask         app.py with flask import       .po files (Babel)
  FastAPI       main.py with fastapi import    .po files (Babel)
  Flutter       pubspec.yaml                   .arb files (ICU plurals)
  Android       app/build.gradle               strings.xml (<plurals>)
  iOS           *.xcodeproj                    .xcstrings / .strings

PROVIDERS (auto-detected from API keys):
  Claude        ANTHROPIC_API_KEY              Recommended
  OpenAI        OPENAI_API_KEY                 GPT-4o
  Gemini        GOOGLE_GEMINI_KEY              Ultra fast
  OpenRouter    OPENROUTER_API_KEY             100+ models
  Mistral       MISTRAL_API_KEY                EU languages

QUALITY FLAGS:
  --min-quality 80     Score translations 0-100, mark <80 as fuzzy
  --glossary g.json    Enforce consistent terminology
  --context "text"     Inject project context into AI prompt
  --lang es,fr         Translate only specific languages
  --changed-only       Only scan git-changed files

FILES:
  .env                              API key storage (in project, standard convention)
  .ai-translate-glossary.json       Glossary (in project, user-created)

CACHE (zero project pollution):
  ~/.ai-translate/
  ├── global_cache.json             Shared across ALL projects (translate once, reuse everywhere)
  └── projects/<hash>/
      ├── cache.json                Project-specific translations
      └── meta.json                 Project path, platform, last run
"""


# ── Provider failover builder ─────────────────────────────────────────


def _build_fallback_translators(
    primary_provider: str,
    key_status: dict[str, str | None],
    model_id: str = "",
) -> list:
    """Build a list of fallback translators from other available API keys.

    Skips the primary provider. Only includes providers whose keys are
    present and whose SDK is installed.
    """
    from ai_translate.services.translators.base import BaseTranslator

    fallbacks: list[BaseTranslator] = []
    for provider_key in PROVIDER_PRIORITY:
        if provider_key == primary_provider:
            continue
        env_var = PROVIDER_ENV_KEYS.get(provider_key, "")
        if not env_var or not key_status.get(env_var):
            continue
        if not _check_sdk_installed(provider_key):
            continue
        api_key = env_manager.load_key(env_var)
        if not api_key:
            continue
        try:
            fb = get_translator(provider_key, api_key, model_id="" if provider_key != "openrouter" else model_id)
            fallbacks.append(fb)
        except Exception:
            continue
    return fallbacks


# ── Core pipeline ─────────────────────────────────────────────────────


def _run(args: argparse.Namespace) -> None:
    global _interrupted
    start_time = time.time()
    project_root = Path(os.getcwd()).resolve()

    # ── --details: show guide and exit ──────────────────────────────
    if args.details:
        ui.console.print(_DETAILS_TEXT)
        return

    # ── Output mode must be set BEFORE any UI ────────────────────────
    if args.quiet and args.json:
        ui.show_error("Cannot use --quiet and --json together. Pick one.")
        sys.exit(1)
    elif args.quiet:
        ui.set_output_mode("quiet")
    elif args.json:
        ui.set_output_mode("json")

    # ── Input validation ────────────────────────────────────────────
    if args.batch_size < 0:
        ui.show_error("--batch-size must be a non-negative integer.")
        sys.exit(1)
    if args.workers < 1 or args.workers > 10:
        ui.show_error("--workers must be between 1 and 10.")
        sys.exit(1)
    if args.min_quality < 0 or args.min_quality > 100:
        ui.show_error("--min-quality must be between 0 and 100.")
        sys.exit(1)
    if args.lang:
        import re as _re
        for code in (c.strip() for c in args.lang.split(",") if c.strip()):
            if not _re.fullmatch(r"[a-zA-Z]{2,3}(?:[_-][a-zA-Z]{2,4})?", code):
                ui.show_error(
                    f"Invalid language code: '{code}'\n"
                    f"  Expected ISO format like: es, fr, zh-TW, pt-BR, zh-Hans"
                )
                sys.exit(1)

    # ── STEP 0: BOOT ─────────────────────────────────────────────────
    ui.show_boot_sequence()

    # ── STEP 1: DETECT PLATFORM ──────────────────────────────────────
    ui.show_step(1, "DETECTING PLATFORM")

    platform = detect_platform(project_root)
    if not platform:
        ui.show_error(
            "Could not detect project platform.\n"
            f"  Supported: {', '.join(PLATFORMS)}\n"
            "  Make sure you're in the project root directory."
        )
        sys.exit(1)

    handler = get_platform_handler(platform)
    ui.show_success(f"Platform detected: {ui.PLATFORM_ICONS.get(platform, platform)}")

    # ── STEP 2: ENVIRONMENT & PROVIDER SETUP ─────────────────────────
    if not args.estimate:
        ui.show_step(2, "ENVIRONMENT SETUP")

    # Load .env file (prompts user if multiple found, saves choice per project)
    # --estimate is read-only — never create .env
    chosen_env = env_manager.load_env_file(project_root=project_root)
    if not args.estimate:
        env_manager.ensure_env_exists()
    key_status = env_manager.get_env_status()
    if not args.estimate:
        if chosen_env:
            ui.show_info(f"Env: {chosen_env}")
        ui.show_env_key_status(key_status, ALL_ENV_KEYS)

    # ── Load glossary ────────────────────────────────────────────────
    glossary = _load_glossary(args.glossary, project_root)
    if glossary:
        ui.show_info(f"Glossary loaded: {len(glossary)} terms")

    # ── Store project context ────────────────────────────────────────
    project_context = args.context

    # ── Provider resolution: auto-detect → flag → interactive ────────
    detected_provider = auto_detect_provider(key_status)

    # --estimate is pure math — skip provider setup entirely
    if args.estimate:
        provider = "skip"
        translator = get_translator("skip")
        selected_model_id = ""
        fallback_translators = []
    elif args.provider:
        # User explicitly chose a provider via --provider flag
        provider = args.provider
        ui.show_info(f"Provider: {provider} (from --provider flag)")
    elif detected_provider:
        # Auto-detected from environment
        provider, reason = detected_provider
        label = next((p["label"] for p in ui.PROVIDER_MENU if p["key"] == provider), provider)
        ui.show_success(f"Provider: {label} ({reason})")
    else:
        # ★ No API key detected → guide user through setup
        ui.show_warning("No API key detected. Let's set one up!")
        ui.console.print()

        provider = ui.prompt_provider_selection()

        if provider == "skip":
            ui.show_info("Provider: skip (no translation — register strings only)")
        else:
            # Show where to get the API key
            url = API_KEY_URLS.get(provider, "")
            if url:
                ui.show_api_key_help(provider, url)

            # Prompt for API key
            env_var = PROVIDER_ENV_KEYS.get(provider, "")
            label = next((p["label"] for p in ui.PROVIDER_MENU if p["key"] == provider), provider)
            api_key = ui.prompt_api_key(label, env_var)
            env_manager.save_key(env_var, api_key)
            ui.show_success("API key saved to .env")

            # Refresh key status
            key_status = env_manager.get_env_status()

    # ── OpenRouter model selection ────────────────────────────────────
    selected_model_id = args.model or ""
    if provider == "openrouter" and not selected_model_id:
        selected_model_id = ui.show_openrouter_model_selection(OPENROUTER_MODELS)

    # ── SDK check + auto-install ──────────────────────────────────────
    if provider != "skip" and not _check_sdk_installed(provider):
        hint = _get_sdk_install_hint(provider)
        if args.no_auto_install:
            ui.show_error(
                f"Required SDK not installed.\n\n"
                f"  Run: [bold]{hint}[/]\n"
                f"  Or use: [bold]pip install \"ai-translate[{provider}]\"[/]"
            )
            sys.exit(1)
        if _auto_install_sdk(provider):
            ui.show_success(f"SDK for '{provider}' installed successfully")
        else:
            ui.show_error(
                f"Could not install SDK automatically.\n\n"
                f"  Install manually:\n"
                f"  [bold]{hint}[/]\n\n"
                f"  Or install ai-translate with the provider extra:\n"
                f"  [bold]pip install \"ai-translate[{provider}]\"[/]"
            )
            sys.exit(1)

    # ── API key verification (with fallback to other providers) ─────
    if provider != "skip":
        env_var = PROVIDER_ENV_KEYS.get(provider, "")
        api_key = env_manager.load_key(env_var) if env_var else None

        if not api_key:
            # Shouldn't happen if interactive flow worked, but handle gracefully
            ui.show_warning(f"{env_var} not found.")
            label = next((p["label"] for p in ui.PROVIDER_MENU if p["key"] == provider), provider)
            api_key = ui.prompt_api_key(label, env_var)
            env_manager.save_key(env_var, api_key)

        translator = get_translator(provider, api_key, model_id=selected_model_id)

        if not args.dry_run:
            ui.show_info(f"Validating API key for {translator.name}...")
            if translator.validate_key():
                ui.show_success("API Key Verified")
            elif args.provider:
                # User explicitly chose this provider — don't silently switch
                ui.show_auth_failure(translator.name)
                sys.exit(1)
            else:
                # Auto-detected provider failed — try others
                ui.show_warning(f"API key validation failed for {translator.name}")

                found_valid = False
                for fallback_provider in PROVIDER_PRIORITY:
                    if fallback_provider == provider:
                        continue
                    fb_env_var = PROVIDER_ENV_KEYS.get(fallback_provider, "")
                    fb_key = key_status.get(fb_env_var)
                    if not fb_key:
                        continue
                    if not _check_sdk_installed(fallback_provider):
                        continue

                    fb_model = "" if fallback_provider != "openrouter" else selected_model_id
                    try:
                        fb_translator = get_translator(fallback_provider, fb_key, model_id=fb_model)
                    except Exception:
                        continue

                    ui.show_info(f"Trying {fb_translator.name}...")
                    if fb_translator.validate_key():
                        ui.show_success(f"Provider switched to {fb_translator.name} (key valid)")
                        translator = fb_translator
                        provider = fallback_provider
                        found_valid = True
                        break
                    else:
                        ui.show_warning(f"{fb_translator.name} key also invalid")

                if not found_valid:
                    ui.show_auth_failure(translator.name)
                    sys.exit(1)
        else:
            ui.show_success("API Key Loaded (dry-run — skipping validation)")
    else:
        translator = get_translator("skip")

    # ── Build fallback translator chain ───────────────────────────────
    if not args.estimate:
        fallback_translators = _build_fallback_translators(provider, key_status, selected_model_id)
        if fallback_translators:
            ui.show_info(f"Failover chain: {', '.join(fb.name for fb in fallback_translators)}")

    # ── STEP 3: SCAN SOURCE STRINGS ──────────────────────────────────
    ui.show_step(3, "SCANNING SOURCE STRINGS")

    # --changed-only: restrict scan to git-changed files
    changed_files = None
    if args.changed_only:
        changed_files = _get_git_changed_files(project_root)
        if changed_files is not None and len(changed_files) == 0:
            ui.show_success("No files changed since last commit — nothing to translate.")
            return
        elif changed_files is not None:
            ui.show_info(f"Scanning {len(changed_files)} changed file(s) only")
        else:
            ui.show_warning("Could not determine changed files; scanning all")

    source_strings = handler.scan_source(project_root)

    # If --changed-only and we got a file list, filter source strings to those files
    if changed_files is not None and source_strings:
        changed_set = set(changed_files)
        # Filter: keep strings whose key/file reference matches a changed file
        filtered = {}
        for key, val in source_strings.items():
            # Keys may contain file paths or be file-relative — keep if any
            # changed file is a substring or if key starts with a changed path
            keep = False
            for cf in changed_set:
                if cf in key or key.startswith(cf):
                    keep = True
                    break
            if keep:
                filtered[key] = val
        # If filtering removed everything, fall back to full set
        if filtered:
            source_strings = filtered

    if not source_strings:
        _platform_hints = {
            "django": 'Use _("text") or gettext("text") in your Python files.',
            "flask": 'Use _("text") or gettext("text") in Python/Jinja2 templates.',
            "fastapi": 'Use _("text") or gettext("text") in your Python files.',
            "flutter": "Create lib/l10n/app_en.arb with translatable keys, or use .tr/.localized in Dart.",
            "android": "Create res/values/strings.xml with <string> entries, or use R.string.* in Kotlin/Java.",
            "ios": 'Create Localizable.xcstrings or .strings files, or use .localized / NSLocalizedString() in Swift.',
        }
        hint = _platform_hints.get(platform, "Add translatable strings to your source files.")
        ui.show_error(
            f"No translatable strings found.\n\n"
            f"  Platform: {ui.PLATFORM_ICONS.get(platform, platform)}\n"
            f"  Hint: {hint}\n\n"
            f"  The tool scans your source code for translation patterns.\n"
            f"  Make sure your project has strings marked for translation."
        )
        sys.exit(1)
    ui.show_success(f"Found {len(source_strings)} source strings")

    # ── STEP 4: DETECT LANGUAGES ─────────────────────────────────────
    ui.show_step(4, "DETECTING LANGUAGES")

    target_languages = handler.detect_target_languages(project_root)

    # --lang: if user specified languages, use them (even if none detected from project)
    _languages_from_user = False  # track if user manually specified languages
    if args.lang:
        from ai_translate.cli.ui import COMMON_LANGUAGES
        requested_codes = [c.strip() for c in args.lang.split(",") if c.strip()]
        if target_languages:
            # Filter detected languages to requested codes
            filtered_langs = {
                code: name for code, name in target_languages.items()
                if code in requested_codes
            }
            # Also include requested codes NOT in detected (user wants new languages)
            new_langs = {
                code: COMMON_LANGUAGES.get(code, code) for code in requested_codes
                if code not in target_languages
            }
            if filtered_langs or new_langs:
                target_languages = {**filtered_langs, **new_langs}
                _languages_from_user = bool(new_langs)
                ui.show_info(f"Filtered to {len(target_languages)} language(s): {', '.join(target_languages.keys())}")
            else:
                ui.show_error(
                    f"None of the specified languages ({args.lang}) found in project.\n"
                    f"  Available: {', '.join(target_languages.keys())}\n"
                    f"  Use one of the available codes, e.g.: --lang {','.join(list(target_languages.keys())[:3])}"
                )
                sys.exit(1)
        else:
            # No languages detected — use --lang codes directly
            target_languages = {
                code: COMMON_LANGUAGES.get(code, code) for code in requested_codes
            }
            _languages_from_user = True
            ui.show_info(f"Using specified language(s): {', '.join(target_languages.keys())}")
    elif not target_languages:
        ui.show_warning("No target languages detected from project files.")
        target_languages = ui.prompt_target_languages()
        if target_languages:
            _languages_from_user = True

    if not target_languages:
        _lang_hints = {
            "django": (
                'Run: [bold]django-admin makemessages -l <lang_code>[/]\n'
                '  Or create locale/<lang_code>/LC_MESSAGES/ directories.\n'
                '  Or specify languages directly: [bold]ai-translate --lang es,fr,de[/]'
            ),
            "flask": (
                'Create translations/<lang_code>/LC_MESSAGES/ directories.\n'
                '  Or specify languages directly: [bold]ai-translate --lang es,fr,de[/]'
            ),
            "fastapi": (
                'Create translations/<lang_code>/LC_MESSAGES/ directories.\n'
                '  Or specify languages directly: [bold]ai-translate --lang es,fr,de[/]'
            ),
            "flutter": (
                'Add ARB files like lib/l10n/app_es.arb for each target language.\n'
                '  Or specify languages directly: [bold]ai-translate --lang es,fr,de[/]'
            ),
            "android": (
                'Create resource directories like res/values-es/, res/values-fr/.\n'
                '  Or specify languages directly: [bold]ai-translate --lang es,fr,de[/]'
            ),
            "ios": (
                'Create .lproj directories like es.lproj/, fr.lproj/.\n'
                '  Or specify languages directly: [bold]ai-translate --lang es,fr,de[/]'
            ),
        }
        hint = _lang_hints.get(platform, 'Specify languages: [bold]ai-translate --lang es,fr,de[/]')
        ui.show_error(
            f"No target languages configured.\n\n"
            f"  Found {len(source_strings)} source strings, but no languages to translate into.\n\n"
            f"  Hint: {hint}"
        )
        sys.exit(1)

    # ── Scaffold language structure if user specified new languages ────
    if _languages_from_user and hasattr(handler, "scaffold_languages"):
        scaffolded = handler.scaffold_languages(project_root, target_languages)
        if scaffolded:
            ui.show_success(f"Created language structure for: {', '.join(scaffolded)}")
        # Update project config file (settings.py, build.gradle, etc.)
        if hasattr(handler, "update_language_config"):
            config_added = handler.update_language_config(project_root, target_languages)
            if config_added:
                ui.show_success(f"Updated project config with: {', '.join(config_added)}")

    ui.show_platform_detected(
        platform=platform,
        project_path=str(project_root),
        lang_count=len(target_languages),
    )
    ui.show_languages(target_languages)

    # ── Auto-config summary ───────────────────────────────────────────
    decisions = [
        ("Platform", ui.PLATFORM_ICONS.get(platform, platform)),
        ("Project", str(project_root)),
        ("Languages", f"{len(target_languages)} detected"),
        ("Provider", translator.name),
        ("Strings", f"{len(source_strings)} found"),
    ]
    if glossary:
        decisions.append(("Glossary", f"{len(glossary)} terms"))
    if project_context:
        decisions.append(("Context", project_context[:60] + ("..." if len(project_context) > 60 else "")))
    ui.show_auto_config(decisions)

    if args.dry_run:
        ui.show_dry_run_banner()

    # ── STEP 5: DETECT MISSING TRANSLATIONS ──────────────────────────
    ui.show_step(5, "DETECTING MISSING TRANSLATIONS")

    missing_by_lang = handler.get_missing_translations(project_root, source_strings, target_languages)
    all_missing_keys: set[str] = set()
    for keys in missing_by_lang.values():
        all_missing_keys.update(keys)

    total_missing = len(all_missing_keys)

    if total_missing == 0:
        ui.show_success("All translations are up to date!")
        elapsed = time.time() - start_time

        if args.json:
            report = {
                "status": "up_to_date",
                "platform": platform,
                "source_count": len(source_strings),
                "missing_count": 0,
                "translated": 0,
                "lang_count": len(target_languages),
                "elapsed": round(elapsed, 2),
                "provider": translator.name,
            }
            ui.show_json_report(report)
            return

        ui.show_final_report(
            platform=platform, source_count=len(source_strings),
            missing_count=0, translated=0, lang_count=len(target_languages),
            elapsed=elapsed, provider=translator.name, success=True,
            project_path=str(project_root),
        )
        return

    ui.show_scan_results(len(source_strings), total_missing, len(target_languages))

    # --estimate: show cost estimate and exit
    if args.estimate:
        missing_values = sorted(all_missing_keys)
        msg_values = [source_strings[k] for k in missing_values if k in source_strings]
        cost_map = estimate_tokens(msg_values, target_languages)
        _provider_info = {
            "anthropic": ("Claude (Anthropic)", "~12s", "Excellent"),
            "openai": ("OpenAI GPT-4o", "~15s", "High"),
            "google": ("Google Gemini", "~6s", "High"),
            "deepseek": ("DeepSeek (OpenRouter)", "~8s", "Good"),
        }
        estimates = []
        for key, cost in cost_map.items():
            name, est_time, quality = _provider_info.get(key, (key, "~10s", "Good"))
            estimates.append({"provider": name, "cost": cost, "time": est_time, "quality": quality})
        ui.show_cost_estimate(estimates, total_missing, len(target_languages))
        return

    # --check: regression detection mode
    if args.check:
        ui.show_step(6, "REGRESSION CHECK")
        project_cache = cache_mod.load_cache(project_root)

        # Find all cached source values that we can re-translate for drift check
        # Cache structure is flat: {source_text: {lang_code: translated_text}}
        all_source_values = set(source_strings.values())
        cached_vals = [v for v in all_source_values if v in project_cache]

        drift_results: dict[str, dict] = {}

        if cached_vals and provider != "skip":
            ui.show_info(f"Re-translating {len(cached_vals)} cached strings to check for drift...")
            batch_size = args.batch_size if args.batch_size > 0 else _compute_batch_size(len(cached_vals))
            for i in range(0, len(cached_vals), batch_size):
                if _interrupted:
                    break
                batch = cached_vals[i:i + batch_size]
                fresh = translator.translate_batch(
                    batch, target_languages, platform=platform,
                    glossary=glossary, context=project_context,
                    fallback_translators=fallback_translators,
                )
                if fresh:
                    for src_val, new_langs in fresh.items():
                        old_langs = project_cache.get(src_val, {})
                        for lang_code, new_text in new_langs.items():
                            old_text = old_langs.get(lang_code, "")
                            if old_text and old_text != new_text:
                                drift_results.setdefault(src_val, {})[lang_code] = {
                                    "old": old_text,
                                    "new": new_text,
                                }
        elif not cached_vals:
            ui.show_info("No cached translations found. Run a translation first, then use --check.")

        # Convert nested drift_results to flat list for UI
        drift_list = []
        for src_val, lang_map in drift_results.items():
            for lang_code, changes in lang_map.items():
                drift_list.append({
                    "key": src_val,
                    "lang": lang_code,
                    "old": changes["old"],
                    "new": changes["new"],
                })
        ui.show_regression_report(drift_list)
        if args.json:
            report = {
                "status": "regression_check",
                "drift_count": len(drift_results),
                "drift": drift_results,
            }
            ui.show_json_report(report)
        sys.exit(1 if drift_results else 0)

    if args.dry_run:
        if args.json:
            elapsed = time.time() - start_time
            report = {
                "status": "dry_run",
                "platform": platform,
                "source_count": len(source_strings),
                "missing_count": total_missing,
                "lang_count": len(target_languages),
                "languages": list(target_languages.keys()),
                "missing_strings": sorted(all_missing_keys),
                "elapsed": round(elapsed, 2),
            }
            ui.show_json_report(report)
            return
        ui.show_dry_run_messages(sorted(all_missing_keys), len(target_languages))
        return

    # ── STEP 6: TRANSLATION PIPELINE ─────────────────────────────────
    ui.show_step(6, "TRANSLATION PIPELINE")

    # Cache lookup
    project_cache = cache_mod.load_cache(project_root)
    missing_values = sorted(all_missing_keys)

    # Build per-string language map
    uncached_lang_map: dict[str, dict[str, str]] = {}
    for msg in missing_values:
        needed_langs: dict[str, str] = {}
        for lang_code, lang_missing in missing_by_lang.items():
            if msg in lang_missing:
                needed_langs[lang_code] = target_languages[lang_code]
        uncached_lang_map[msg] = needed_langs

    # Use source VALUES for translation (not keys)
    msg_to_value = {k: source_strings[k] for k in missing_values if k in source_strings}

    cached_translations, uncached_msgs, uncached_langs = cache_mod.lookup_cached(
        project_cache,
        list(msg_to_value.values()),
        target_languages,
    )
    ui.show_cache_stats(len(msg_to_value) - len(uncached_msgs), len(uncached_msgs))

    # Translate uncached messages
    all_translations: dict[str, dict[str, str]] = {}
    all_translations.update(cached_translations)

    if uncached_msgs and provider != "skip":
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        # Group by language set for efficient batching
        lang_groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for msg in uncached_msgs:
            langs_needed = uncached_langs.get(msg, target_languages)
            key = tuple(sorted(langs_needed.keys()))
            lang_groups[key].append(msg)

        batch_size = args.batch_size if args.batch_size > 0 else _compute_batch_size(len(uncached_msgs))
        total_batches = sum(ceil(len(msgs) / batch_size) for msgs in lang_groups.values())

        # ── Worker count ────────────────────────────────────────────
        max_workers = max(1, min(args.workers, 10))
        if total_batches <= 2:
            max_workers = 1  # not worth parallelizing small jobs
        if max_workers > 1:
            ui.show_info(f"Parallel mode: {max_workers} workers, {total_batches} batches")

        # ── Runtime failover state (shared across threads) ──────────
        _fail_lock = threading.Lock()
        _consecutive_failures = 0
        _total_failures = 0
        _MAX_CONSECUTIVE_FAILS = 3  # switch provider after 3 consecutive failures
        _active_translator = translator
        _active_provider_name = translator.name
        _switched = False

        # Events log — collected during translation, shown after progress bar
        _events_lock = threading.Lock()
        _events: list[tuple[str, str]] = []  # (level, message)

        def _log_event(level: str, msg: str) -> None:
            with _events_lock:
                _events.append((level, msg))

        def _translate_one_batch(batch, group_langs):
            """Translate a single batch, with runtime failover."""
            nonlocal _consecutive_failures, _total_failures
            nonlocal _active_translator, _active_provider_name, _switched

            current_translator = _active_translator
            result = current_translator.translate_batch(
                batch, group_langs, platform=platform,
                glossary=glossary, context=project_context,
                fallback_translators=fallback_translators,
            )

            if result:
                with _fail_lock:
                    _consecutive_failures = 0
                return result

            # Batch failed (primary + all fallbacks failed for this batch)
            with _fail_lock:
                _consecutive_failures += 1
                _total_failures += 1
                fail_count = _consecutive_failures

                _log_event("warning", f"Batch failed ({current_translator.name}) — "
                           f"{len(batch)} strings skipped [fail #{_total_failures}]")

                if fail_count >= _MAX_CONSECUTIVE_FAILS and not _switched:
                    # Try to switch the primary translator permanently
                    _log_event("warning",
                               f"{_active_provider_name} failed {fail_count} consecutive times — "
                               "searching for working provider...")
                    for fb in fallback_translators:
                        try:
                            if fb.validate_key():
                                _log_event("success",
                                           f"Provider switched: {_active_provider_name} → {fb.name}")
                                _active_translator = fb
                                _active_provider_name = fb.name
                                _switched = True
                                _consecutive_failures = 0
                                break
                            else:
                                _log_event("warning", f"{fb.name} key invalid — skipping")
                        except Exception:
                            _log_event("warning", f"{fb.name} validation error — skipping")
                            continue

                    if not _switched:
                        _log_event("error",
                                   "No working provider found — remaining batches may fail")
            return None

        # ── Build flat list of (batch, group_langs) jobs ────────────
        jobs: list[tuple[list[str], dict[str, str]]] = []
        for lang_key, group_msgs in lang_groups.items():
            group_langs = {lc: target_languages.get(lc, lc) for lc in lang_key}
            for i in range(0, len(group_msgs), batch_size):
                batch = group_msgs[i : i + batch_size]
                jobs.append((batch, group_langs))

        # ── Incremental cache save interval ─────────────────────────
        _SAVE_EVERY = 20  # save progress every 20 batches
        _results_lock = threading.Lock()
        _batches_done = 0
        _last_save_count = 0

        def _maybe_save_cache():
            """Incremental save — protects against crashes."""
            nonlocal _last_save_count
            if _batches_done > 0 and _batches_done % _SAVE_EVERY == 0 and _batches_done != _last_save_count:
                _save_cache = cache_mod.update_cache(
                    dict(project_cache), dict(all_translations)
                )
                cache_mod.save_cache(project_root, _save_cache, platform=platform)
                _last_save_count = _batches_done
                _log_event("info",
                           f"Progress saved — {len(all_translations)} translations cached "
                           f"[batch {_batches_done}/{total_batches}]")

        progress = ui.create_translation_progress()
        task = progress.add_task("translate", total=total_batches)

        with progress:
            if max_workers == 1:
                # Sequential mode (simple, no threading overhead)
                for batch, group_langs in jobs:
                    if _interrupted:
                        break
                    result = _translate_one_batch(batch, group_langs)
                    if result:
                        with _results_lock:
                            all_translations.update(result)
                            _batches_done += 1
                            _maybe_save_cache()
                    progress.advance(task)
            else:
                # Parallel mode
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    future_map = {
                        pool.submit(_translate_one_batch, batch, group_langs): idx
                        for idx, (batch, group_langs) in enumerate(jobs)
                    }
                    for future in as_completed(future_map):
                        if _interrupted:
                            pool.shutdown(wait=False, cancel_futures=True)
                            break
                        result = future.result()
                        if result:
                            with _results_lock:
                                all_translations.update(result)
                                _batches_done += 1
                                _maybe_save_cache()
                        progress.advance(task)

        # ── Show events that happened during translation ────────────
        if _events:
            ui.console.print()
            for level, msg in _events:
                if level == "success":
                    ui.show_success(msg)
                elif level == "warning":
                    ui.show_warning(msg)
                elif level == "error":
                    ui.show_error(msg)
                else:
                    ui.show_info(msg)

        # ── Summary of failures ─────────────────────────────────────
        if _total_failures > 0:
            ui.show_warning(
                f"{_total_failures} batch(es) failed during translation. "
                f"Run again to retry — cached translations won't be re-translated."
            )
        if _switched:
            ui.show_info(f"Provider was switched to {_active_provider_name} during this run")

    # Update cache (final save)
    if all_translations:
        project_cache = cache_mod.update_cache(project_cache, all_translations)
        cache_mod.save_cache(project_root, project_cache, platform=platform)

    translated_count = len(all_translations)
    ui.show_success(f"Translated {translated_count}/{total_missing} strings")

    # ── Placeholder validation ────────────────────────────────────────
    if all_translations:
        ph_issues = validate_placeholders(all_translations)
        if ph_issues:
            issue_count = sum(len(v) for v in ph_issues.values())
            ui.show_warning(
                f"Placeholder validation: {issue_count} translation(s) have missing placeholders.\n"
                "  These translations may cause runtime errors."
            )
            for src, lang_map in ph_issues.items():
                for lang_code, issue in lang_map.items():
                    ui.show_warning(f"  [{lang_code}] \"{src[:50]}\" → {issue}")

    # ── Quality gate (--min-quality) ─────────────────────────────────
    approved_translations: dict[str, dict[str, str]] = {}
    fuzzy_translations: dict[str, dict[str, str]] = {}

    if args.min_quality:
        args.min_quality = max(0, min(100, args.min_quality))

    if args.min_quality and all_translations:
        scores = score_translations(translator, all_translations, list(all_translations.keys()), target_languages)
        threshold = args.min_quality
        for src_val, lang_map in all_translations.items():
            src_score = scores.get(src_val, {})
            approved_langs: dict[str, str] = {}
            fuzzy_langs: dict[str, str] = {}
            for lang_code, translated_text in lang_map.items():
                quality = src_score.get(lang_code, 100)
                if quality >= threshold:
                    approved_langs[lang_code] = translated_text
                else:
                    fuzzy_langs[lang_code] = translated_text
            if approved_langs:
                approved_translations[src_val] = approved_langs
            if fuzzy_langs:
                fuzzy_translations[src_val] = fuzzy_langs

        ui.show_info(
            f"Quality gate ({threshold}%): "
            f"{len(approved_translations)} approved, "
            f"{len(fuzzy_translations)} fuzzy"
        )
    else:
        approved_translations = all_translations

    # ── Interactive review (--review) ────────────────────────────────
    if args.review and approved_translations:
        # Flatten {src: {lang: text}} -> {"src [lang]": text} for review UI
        flat_for_review: dict[str, str] = {}
        flat_key_map: dict[str, tuple[str, str]] = {}
        for src_val, lang_map in approved_translations.items():
            for lang_code, text in lang_map.items():
                review_key = f"{src_val} [{lang_code}]"
                flat_for_review[review_key] = text
                flat_key_map[review_key] = (src_val, lang_code)

        reviewed = ui.show_review_mode(flat_for_review, None)

        # Unflatten back to {src: {lang: text}}
        final_translations: dict[str, dict[str, str]] = {}
        for bucket in ("accepted", "edited"):
            for review_key, text in reviewed.get(bucket, {}).items():
                if review_key in flat_key_map:
                    src_val, lang_code = flat_key_map[review_key]
                    final_translations.setdefault(src_val, {})[lang_code] = text
        approved_translations = final_translations

    # ── STEP 7: WRITE TRANSLATIONS ───────────────────────────────────
    ui.show_step(7, "WRITING TRANSLATIONS")

    # Remap: translations keyed by source value → keyed by source key
    write_source = approved_translations
    key_translations: dict[str, dict[str, str]] = {}
    value_to_keys: dict[str, list[str]] = {}
    for key, val in source_strings.items():
        value_to_keys.setdefault(val, []).append(key)

    for source_val, lang_map in write_source.items():
        for key in value_to_keys.get(source_val, [source_val]):
            key_translations[key] = lang_map

    # --json: output JSON report instead of writing
    if args.json:
        elapsed = time.time() - start_time
        report = {
            "status": "success" if translated_count > 0 else "no_translations",
            "platform": platform,
            "source_count": len(source_strings),
            "missing_count": total_missing,
            "translated": translated_count,
            "approved": len(approved_translations),
            "fuzzy": len(fuzzy_translations),
            "lang_count": len(target_languages),
            "languages": list(target_languages.keys()),
            "elapsed": round(elapsed, 2),
            "provider": translator.name,
            "translations": key_translations,
        }
        ui.show_json_report(report)
        return

    write_stats = handler.write_translations(project_root, key_translations, source_strings)
    ui.show_translation_results(write_stats)

    # ── STEP 8: COMPILE (PO-based platforms) ──────────────────────────
    if platform in ("django", "flask", "fastapi"):
        ui.show_step(8, "COMPILING TRANSLATIONS")
        if handler.compile_messages(project_root):
            ui.show_success("Compilation successful — .mo files ready")
        else:
            ui.show_warning("Compilation skipped (msgfmt not found)")

    # ── FINAL REPORT ──────────────────────────────────────────────────
    elapsed = time.time() - start_time
    success = translated_count > 0

    if args.json:
        report = {
            "status": "success" if success else "failure",
            "platform": platform,
            "source_count": len(source_strings),
            "missing_count": total_missing,
            "translated": translated_count,
            "approved": len(approved_translations),
            "fuzzy": len(fuzzy_translations),
            "lang_count": len(target_languages),
            "languages": list(target_languages.keys()),
            "elapsed": round(elapsed, 2),
            "provider": translator.name,
            "project_path": str(project_root),
        }
        ui.show_json_report(report)
    else:
        ui.show_final_report(
            platform=platform,
            source_count=len(source_strings),
            missing_count=total_missing,
            translated=translated_count,
            lang_count=len(target_languages),
            elapsed=elapsed,
            provider=translator.name,
            success=success,
            project_path=str(project_root),
        )

    # --quiet: exit with code based on success
    if args.quiet:
        sys.exit(0 if success else 1)


# ── Entry point ───────────────────────────────────────────────────────


def main() -> None:
    # Suppress noisy third-party warnings (gRPC, absl, protobuf)
    import warnings
    warnings.filterwarnings("ignore")
    os.environ.setdefault("GRPC_VERBOSITY", "NONE")
    os.environ.setdefault("GLOG_minloglevel", "3")
    logging.getLogger("absl").setLevel(logging.CRITICAL)
    logging.getLogger("grpc").setLevel(logging.CRITICAL)
    logging.getLogger("google").setLevel(logging.CRITICAL)
    logging.getLogger("urllib3").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.CRITICAL)

    parser = build_parser()
    args = parser.parse_args()

    # Logging — only show logs in --debug mode. In normal mode, suppress
    # ALL log output (WARNING, ERROR) to keep the UI clean. Errors are
    # handled via ui.show_error() instead of logging.
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")
    else:
        logging.basicConfig(level=logging.CRITICAL + 1)  # suppress everything

    # Lock
    if not _acquire_lock():
        ui.show_error("Another instance of ai-translate is already running.")
        sys.exit(1)

    # Signal handler
    signal.signal(signal.SIGINT, _handle_interrupt)

    try:
        _run(args)
    except KeyboardInterrupt:
        ui.show_warning("Interrupted.")
        ui._show_signoff()
        sys.exit(130)
    except SystemExit:
        raise
    except PermissionError as exc:
        ui.show_error(f"Permission denied: {exc.filename}")
        ui._show_signoff()
        sys.exit(1)
    except Exception as exc:
        if args.debug:
            import traceback
            traceback.print_exc()
        ui.show_error(f"Unexpected error: {exc}")
        ui._show_signoff()
        sys.exit(1)
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
