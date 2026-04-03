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
    _, pip_name = PROVIDER_SDK_MAP.get(provider, ("", ""))
    if not pip_name or pip_name not in _TRUSTED_PACKAGES:
        return False
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", pip_name],
            capture_output=True, timeout=120, check=True,
        )
        return True
    except Exception:
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
    if glossary_path:
        p = Path(glossary_path)
        if p.is_file():
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
        log.warning("Glossary file not found: %s", glossary_path)
        return None

    # Auto-discover
    auto_path = project_root / ".ai-translate-glossary.json"
    if auto_path.is_file():
        with auto_path.open("r", encoding="utf-8") as f:
            return json.load(f)
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
    if args.quiet:
        ui.set_output_mode("quiet")
    elif args.json:
        ui.set_output_mode("json")

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
    ui.show_step(2, "ENVIRONMENT SETUP")

    env_manager.ensure_env_exists()
    key_status = env_manager.get_env_status()
    ui.show_env_key_status(key_status, ALL_ENV_KEYS)

    # ── Load glossary ────────────────────────────────────────────────
    glossary = _load_glossary(args.glossary, project_root)
    if glossary:
        ui.show_info(f"Glossary loaded: {len(glossary)} terms")

    # ── Store project context ────────────────────────────────────────
    project_context = args.context

    # ── Provider resolution: auto-detect → flag → interactive ────────
    detected_provider = auto_detect_provider(key_status)

    if args.provider:
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
        if args.no_auto_install:
            hint = _get_sdk_install_hint(provider)
            ui.show_error(f"Required SDK not installed. Run: {hint}")
            sys.exit(1)
        ui.show_info(f"Installing SDK for '{provider}'...")
        if _auto_install_sdk(provider):
            ui.show_success(f"SDK for '{provider}' installed successfully")
        else:
            hint = _get_sdk_install_hint(provider)
            ui.show_error(f"Failed to install SDK. Run manually: {hint}")
            sys.exit(1)

    # ── API key verification ──────────────────────────────────────────
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
            else:
                ui.show_auth_failure(translator.name)
                sys.exit(1)
        else:
            ui.show_success("API Key Loaded (dry-run — skipping validation)")
    else:
        translator = get_translator("skip")

    # ── Build fallback translator chain ───────────────────────────────
    fallback_translators = _build_fallback_translators(provider, key_status, selected_model_id)
    if fallback_translators:
        ui.show_info(f"Failover chain: {', '.join(fb.name for fb in fallback_translators)}")

    # ── STEP 3: SCAN SOURCE STRINGS ──────────────────────────────────
    ui.show_step(3, "SCANNING SOURCE STRINGS")

    # --changed-only: restrict scan to git-changed files
    changed_files = None
    if args.changed_only:
        changed_files = _get_git_changed_files(project_root)
        if changed_files is not None:
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
    if not target_languages:
        ui.show_warning("No target languages detected from project files.")
        target_languages = ui.prompt_target_languages()

    # --lang: filter to specified language codes
    if args.lang:
        requested_codes = [c.strip() for c in args.lang.split(",") if c.strip()]
        filtered_langs = {
            code: name for code, name in target_languages.items()
            if code in requested_codes
        }
        if filtered_langs:
            target_languages = filtered_langs
            ui.show_info(f"Filtered to {len(target_languages)} language(s): {', '.join(target_languages.keys())}")
        else:
            ui.show_warning(
                f"None of the specified languages ({args.lang}) found in project. "
                f"Available: {', '.join(target_languages.keys())}"
            )

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
            "deepl": ("DeepSeek (OpenRouter)", "~8s", "Good"),
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
        # Group by language set for efficient batching
        lang_groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for msg in uncached_msgs:
            langs_needed = uncached_langs.get(msg, target_languages)
            key = tuple(sorted(langs_needed.keys()))
            lang_groups[key].append(msg)

        batch_size = args.batch_size if args.batch_size > 0 else _compute_batch_size(len(uncached_msgs))
        total_batches = sum(ceil(len(msgs) / batch_size) for msgs in lang_groups.values())

        progress = ui.create_translation_progress()
        task = progress.add_task("translate", total=total_batches)

        with progress:
            for lang_key, group_msgs in lang_groups.items():
                if _interrupted:
                    break
                group_langs = {lc: target_languages.get(lc, lc) for lc in lang_key}

                for i in range(0, len(group_msgs), batch_size):
                    if _interrupted:
                        break
                    batch = group_msgs[i : i + batch_size]
                    result = translator.translate_batch(
                        batch, group_langs, platform=platform,
                        glossary=glossary, context=project_context,
                        fallback_translators=fallback_translators,
                    )
                    if result:
                        all_translations.update(result)
                    progress.advance(task)

    # Update cache
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
    parser = build_parser()
    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")

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
        sys.exit(130)
    except SystemExit:
        raise
    except PermissionError as exc:
        ui.show_error(f"Permission denied: {exc.filename}")
        sys.exit(1)
    except Exception as exc:
        if args.debug:
            import traceback
            traceback.print_exc()
        ui.show_error(f"Unexpected error: {exc}")
        sys.exit(1)
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
