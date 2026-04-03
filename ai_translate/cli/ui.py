"""Rich terminal UI — clean, modern, production-grade."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.prompt import IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

console = Console()

# ── Output mode ──────────────────────────────────────────────────────

_output_mode: str = "normal"  # "normal", "quiet", "json"


def set_output_mode(mode: str) -> None:
    """Set the global output mode: 'normal', 'quiet', or 'json'."""
    global _output_mode
    if mode not in ("normal", "quiet", "json"):
        raise ValueError(f"Invalid output mode: {mode!r}. Must be 'normal', 'quiet', or 'json'.")
    _output_mode = mode


def _should_suppress() -> bool:
    """Return True if normal Rich output should be suppressed."""
    return _output_mode in ("quiet", "json")


# ── Brand ─────────────────────────────────────────────────────────────

LOGO = r"""
     _    ___   _____                    _       _
    / \  |_ _| |_   _| __ __ _ _ __  ___| | __ _| |_ ___
   / _ \  | |    | || '__/ _` | '_ \/ __| |/ _` | __/ _ \
  / ___ \ | |    | || | | (_| | | | \__ \ | (_| | ||  __/
 /_/   \_\___|   |_||_|  \__,_|_| |_|___/_|\__,_|\__\___|
"""

VERSION = "1.0.0"

# ── Provider menu ─────────────────────────────────────────────────────

PROVIDER_MENU: list[dict[str, str]] = [
    {"key": "claude", "label": "Claude (Anthropic)", "tag": "Recommended", "icon": "\u26a1", "color": "bright_cyan"},
    {"key": "openai", "label": "OpenAI GPT", "tag": "Powerful", "icon": "\U0001f9e0", "color": "bright_green"},
    {"key": "openrouter", "label": "OpenRouter", "tag": "100+ Models", "icon": "\U0001f525", "color": "bright_yellow"},
    {"key": "gemini", "label": "Google Gemini", "tag": "Ultra Fast", "icon": "\u2726", "color": "yellow"},
    {"key": "mistral", "label": "Mistral AI", "tag": "EU Languages", "icon": "\U0001f1ea\U0001f1fa", "color": "bright_magenta"},
]

COMMON_LANGUAGES: dict[str, str] = {
    "ar": "Arabic", "bg": "Bulgarian", "bn": "Bengali", "ca": "Catalan",
    "cs": "Czech", "da": "Danish", "de": "German", "el": "Greek",
    "en": "English", "es": "Spanish", "et": "Estonian", "fa": "Persian",
    "fi": "Finnish", "fr": "French", "he": "Hebrew", "hi": "Hindi",
    "hr": "Croatian", "hu": "Hungarian", "id": "Indonesian", "it": "Italian",
    "ja": "Japanese", "ko": "Korean", "lt": "Lithuanian", "lv": "Latvian",
    "ms": "Malay", "nb": "Norwegian Bokmal", "nl": "Dutch", "no": "Norwegian",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "sk": "Slovak", "sl": "Slovenian", "sr": "Serbian", "sv": "Swedish",
    "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu",
    "vi": "Vietnamese", "zh": "Chinese", "zh-TW": "Chinese Traditional",
    "zh-Hans": "Chinese Simplified", "zh-Hant": "Chinese Traditional",
    "pt-BR": "Portuguese Brazilian", "es-MX": "Spanish Mexican",
    "fr-CA": "French Canadian", "en-GB": "English UK",
}

PLATFORM_ICONS: dict[str, str] = {
    "django": "\U0001f40d Django",
    "flask": "\U0001f3f6\ufe0f  Flask",
    "fastapi": "\u26a1 FastAPI",
    "flutter": "\U0001f426 Flutter",
    "android": "\U0001f4f1 Android",
    "ios": "\U0001f34e iOS",
}


# ── Typing effect ─────────────────────────────────────────────────────


def _type_effect(text: str, style: str = "bright_green", delay: float = 0.015) -> None:
    if _should_suppress():
        return
    rendered = Text()
    with Live(rendered, console=console, refresh_per_second=60, transient=True) as live:
        for ch in text:
            rendered.append(ch, style=style)
            live.update(rendered)
            time.sleep(delay)
    console.print(rendered)


# ── Boot sequence ─────────────────────────────────────────────────────


def show_boot_sequence() -> None:
    if _should_suppress():
        return
    console.print()
    console.print(
        Panel(
            f"[bold bright_cyan]{LOGO}[/]\n"
            f"[bold white]  Zero-config AI localization for developers[/]\n"
            f"[dim]  v{VERSION} \u2014 by Happy-Cyber[/]",
            border_style="bright_cyan",
            box=box.DOUBLE,
            padding=(0, 2),
        )
    )
    console.print()

    steps = [
        ("SYS.INIT", "Initializing core engine"),
        ("SYS.ENV", "Loading environment"),
        ("SYS.READY", "Translation engine online"),
    ]
    for code, label in steps:
        console.print(f"  [bold green]\u25cf[/] [dim cyan]{code}[/]  [green]{label}[/]")
        time.sleep(0.08)
    console.print()


# ── API key status matrix ─────────────────────────────────────────────


def show_env_key_status(key_status: dict[str, str | None], labels: dict[str, str]) -> None:
    if _should_suppress():
        return
    table = Table(
        title="[bold bright_cyan]\u26a1 API Key Status[/]",
        box=box.ROUNDED,
        border_style="cyan",
        show_lines=False,
        padding=(0, 1),
        title_justify="center",
    )
    table.add_column("Provider", style="bold white", min_width=26)
    table.add_column("Env Variable", style="dim cyan")
    table.add_column("Status", justify="center", min_width=18)

    for key, label in labels.items():
        val = key_status.get(key)
        if val:
            masked = val[:4] + "\u2022" * 8 + val[-4:] if len(val) > 12 else "\u2022" * 12
            status = f"[bold green]\u25cf ACTIVE[/]  [dim]{masked}[/]"
        else:
            status = "[dim red]\u25cb NOT SET[/]"
        table.add_row(f"  {label}", f"[dim]{key}[/]", status)

    console.print(table)
    console.print()


# ── Interactive provider selection (core new feature) ─────────────────


def prompt_provider_selection() -> str:
    """Show provider menu and return the selected provider key.

    This is the NEW interactive flow when no API key is detected.
    """
    if _should_suppress():
        return PROVIDER_MENU[0]["key"]
    console.print(
        Panel(
            "[bold bright_cyan]SELECT TRANSLATION ENGINE[/]",
            border_style="cyan",
            box=box.HEAVY,
            padding=(0, 2),
        )
    )
    console.print()

    for idx, p in enumerate(PROVIDER_MENU, 1):
        console.print(
            f"  [bold white][{idx}][/]  {p['icon']}  "
            f"[bold {p['color']}]{p['label']}[/]  "
            f"[{p['color']}]\u00b7 {p['tag']}[/]"
        )

    console.print()

    while True:
        choice = IntPrompt.ask(
            "  [bold yellow]\u2192 Enter your choice[/]",
            choices=[str(i) for i in range(1, len(PROVIDER_MENU) + 1)],
        )
        selected = PROVIDER_MENU[choice - 1]
        console.print(f"\n  [bold green]\u25cf[/] Selected: [bold]{selected['label']}[/]\n")
        return selected["key"]


def show_openrouter_model_selection(
    models: list[dict[str, str]],
    model_flag: str | None = None,
) -> str:
    """Show OpenRouter model submenu; return selected model ID."""
    if model_flag:
        if not _should_suppress():
            console.print(f"  [bold cyan]\u2699 Model:[/] [bold]{model_flag}[/] (from --model flag)")
        return model_flag

    if _should_suppress():
        return models[0]["id"] if models else ""

    console.print(
        Panel(
            "[bold bright_yellow]SELECT OPENROUTER MODEL[/]",
            border_style="yellow",
            box=box.HEAVY,
            padding=(0, 2),
        )
    )
    console.print()

    for idx, m in enumerate(models, 1):
        console.print(
            f"  [bold white][{idx:2d}][/]  "
            f"[bold {m['color']}]{m['name']}[/]  "
            f"[{m['color']}]\u00b7 {m['tag']}[/]"
        )
        console.print(f"       [dim]{m['id']}[/]")

    console.print()

    while True:
        choice = IntPrompt.ask(
            "  [bold yellow]\u2192 Select model[/]",
            choices=[str(i) for i in range(1, len(models) + 1)],
        )
        selected = models[choice - 1]
        console.print(
            f"\n  [bold green]\u25cf[/] Model: [bold]{selected['name']}[/] "
            f"[dim]({selected['id']})[/]\n"
        )
        return selected["id"]


# ── API key prompt & help ─────────────────────────────────────────────


def show_api_key_help(provider: str, url: str) -> None:
    """Show where to get an API key for the selected provider."""
    if _should_suppress():
        return
    label = next((p["label"] for p in PROVIDER_MENU if p["key"] == provider), provider)
    console.print(
        Panel(
            f"[bold cyan]\U0001f511 {label} API Key Required[/]\n\n"
            f"  Get your key at:\n"
            f"  [bold underline blue]{url}[/]\n\n"
            f"  [dim]The key will be saved to .env automatically.\n"
            f"  You won't need to enter it again.[/]",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )


def prompt_api_key(provider_name: str, env_var: str) -> str:
    """Prompt the user to paste their API key. Returns the stripped key."""
    while True:
        key = Prompt.ask(f"  [bold cyan]\u2192 Enter your {provider_name} API key[/]")
        key = key.strip()
        if key:
            return key
        console.print("  [red]Key cannot be empty. Try again.[/]")


# ── Status helpers ────────────────────────────────────────────────────


def show_success(msg: str) -> None:
    if _should_suppress():
        return
    console.print(f"  [bold green]\u25cf[/] [green]{msg}[/]")


def show_warning(msg: str) -> None:
    if _should_suppress():
        return
    console.print(f"  [bold yellow]\u25b2[/] [yellow]{msg}[/]")


def show_error(msg: str) -> None:
    if _should_suppress():
        return
    console.print(f"  [bold red]\u2717[/] [red]{msg}[/]")


def show_info(msg: str) -> None:
    if _should_suppress():
        return
    console.print(f"  [bold cyan]\u2139[/] [cyan]{msg}[/]")


def show_step(step_num: int, title: str) -> None:
    if _should_suppress():
        return
    console.print()
    console.rule(f"[bold bright_cyan] STEP {step_num} \u2503 {title} [/]", style="cyan")
    console.print()


# ── Auto-config summary ──────────────────────────────────────────────


def show_auto_config(decisions: list[tuple[str, str]]) -> None:
    if _should_suppress():
        return
    panel_rows = "\n".join(
        f"  [bold white]{label}:[/]  [bright_cyan]{value}[/]"
        for label, value in decisions
    )
    console.print(
        Panel(
            f"[bold bright_cyan]\u25c6 Auto-Detected Configuration[/]\n\n{panel_rows}",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()


# ── Platform detection ────────────────────────────────────────────────


def show_platform_detected(
    platform: str,
    project_path: str,
    lang_count: int,
) -> None:
    if _should_suppress():
        return
    icon = PLATFORM_ICONS.get(platform, platform)
    console.print(
        Panel(
            f"  [bold white]Platform:[/]   [bold bright_cyan]{icon}[/]\n"
            f"  [bold white]Project:[/]    [dim]{project_path}[/]\n"
            f"  [bold white]Languages:[/]  [bold]{lang_count}[/] detected",
            border_style="bright_cyan",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()


def show_languages(target_languages: dict[str, str]) -> None:
    if _should_suppress():
        return
    lang_str = "  ".join(
        f"[bold]{name}[/]([dim]{code}[/])" for code, name in target_languages.items()
    )
    console.print(f"  [bold cyan]\u2139[/] Languages: {lang_str}")
    console.print()


# ── Scan results ──────────────────────────────────────────────────────


def show_scan_results(source_count: int, missing_count: int, lang_count: int) -> None:
    if _should_suppress():
        return
    console.print(f"  [bold green]\u25cf[/] Source strings:   [bold]{source_count}[/]")
    console.print(f"  [bold green]\u25cf[/] Need translation: [bold]{missing_count}[/]")
    console.print(f"  [bold green]\u25cf[/] Languages:        [bold]{lang_count}[/]")


def show_cache_stats(cached: int, uncached: int) -> None:
    if _should_suppress():
        return
    if cached:
        console.print(f"  [bold green]\u25cf[/] Cache hit: [bold]{cached}[/] strings from previous runs")
    console.print(f"  [bold cyan]\u2139[/] Need translation: [bold]{uncached}[/] new strings")


# ── Dry-run ───────────────────────────────────────────────────────────


def show_dry_run_banner() -> None:
    if _should_suppress():
        return
    console.print(
        Panel(
            "[bold yellow]DRY RUN MODE[/] \u2014 No API calls, no file writes.",
            border_style="yellow",
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )
    console.print()


def show_dry_run_messages(messages: list[str], lang_count: int) -> None:
    if _should_suppress():
        return
    preview = messages[:50]
    lines = "\n".join(f"  [dim]\u2022[/] {m}" for m in preview)
    if len(messages) > 50:
        lines += f"\n  [dim]... and {len(messages) - 50} more[/]"
    console.print(
        Panel(
            f"[bold yellow]Would translate {len(messages)} strings \u00d7 {lang_count} languages:[/]\n\n{lines}",
            border_style="yellow",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )


# ── Auth failure ──────────────────────────────────────────────────────


def show_auth_failure(provider_name: str) -> None:
    if _should_suppress():
        return
    console.print(
        Panel(
            f"[bold red]\u2717 Authentication Failed[/]\n\n"
            f"  API key validation failed for [bold]{provider_name}[/].\n\n"
            f"  Possible causes:\n"
            f"  [dim]\u2022 API key is invalid, expired, or has wrong permissions\n"
            f"  \u2022 Network connectivity issue\n"
            f"  \u2022 Provider service outage[/]",
            border_style="red",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )


# ── Progress bar ──────────────────────────────────────────────────────


def create_translation_progress() -> Progress:
    return Progress(
        SpinnerColumn("dots", style="bright_cyan"),
        TextColumn("[bold bright_cyan]Translating[/]"),
        BarColumn(bar_width=30, style="cyan", complete_style="bright_cyan"),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


# ── Translation results ──────────────────────────────────────────────


def show_translation_results(stats: dict[str, int]) -> None:
    if _should_suppress():
        return
    for lang, count in stats.items():
        console.print(f"  [bold green]\u25cf[/] Updated: [bold]{lang}[/] \u2192 {count} entries")


# ── Final report ──────────────────────────────────────────────────────


def show_final_report(
    *,
    platform: str,
    source_count: int,
    missing_count: int,
    translated: int,
    lang_count: int,
    elapsed: float,
    provider: str,
    success: bool,
    project_path: str,
) -> None:
    if _should_suppress():
        return
    table = Table(
        box=box.SIMPLE,
        show_header=False,
        padding=(0, 2),
        border_style="cyan",
    )
    table.add_column("Metric", style="dim", min_width=24)
    table.add_column("Value", style="bold white")

    icon = PLATFORM_ICONS.get(platform, platform)
    table.add_row("Platform", icon)
    table.add_row("Source strings", str(source_count))
    table.add_row("Missing translations", str(missing_count))
    table.add_row("Successfully translated", str(translated))
    table.add_row("Languages processed", str(lang_count))
    table.add_row("Translation engine", provider)
    table.add_row("Execution time", f"{elapsed:.1f}s")

    console.print()
    console.rule("[bold bright_cyan] TRANSLATION REPORT [/]", style="cyan")
    console.print(table)
    console.print()

    if success and translated > 0:
        total_entries = translated * lang_count
        console.print(
            Panel(
                f"[bold green]\u2705  MISSION COMPLETE[/]\n\n"
                f"  Your app now speaks [bold]{lang_count}[/] languages.\n"
                f"  [bold]{total_entries}[/] translation entries generated.\n"
                f"  All translation files updated.",
                border_style="green",
                box=box.DOUBLE,
                padding=(1, 2),
            )
        )
    elif translated == 0 and missing_count == 0:
        console.print(
            Panel(
                "[bold bright_cyan]\u2705  ALL UP TO DATE[/]\n\n"
                "  All translations are complete. Nothing to do.",
                border_style="cyan",
                box=box.DOUBLE,
                padding=(1, 2),
            )
        )
    else:
        console.print(
            Panel(
                f"[bold yellow]\u26a0  PARTIALLY COMPLETE[/]\n\n"
                f"  Translated {translated}/{missing_count} strings.\n"
                f"  Run again to retry failed translations.",
                border_style="yellow",
                box=box.DOUBLE,
                padding=(1, 2),
            )
        )

    console.print()
    console.print(
        "[dim]  Made with \u2764 by Happy-Cyber  \u00b7  "
        "https://github.com/Happy-cyber/ai-translate[/]"
    )
    console.print()


# ── Target language prompt ────────────────────────────────────────────


def prompt_target_languages() -> dict[str, str]:
    """Interactively ask for target language codes."""
    if _should_suppress():
        return {}
    console.print(
        Panel(
            "[bold yellow]No target languages detected.[/]\n\n"
            "  Enter language codes separated by commas.\n"
            "  [dim]Examples: es,fr,de,ja,ko,zh[/]",
            border_style="yellow",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    while True:
        raw = Prompt.ask("  [bold cyan]\u2192 Target languages[/]")
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        if codes:
            return {code: COMMON_LANGUAGES.get(code, code) for code in codes}
        console.print("  [red]Please enter at least one language code.[/]")


# ══════════════════════════════════════════════════════════════════════
# NEW FUNCTIONS
# ══════════════════════════════════════════════════════════════════════


# ── Interactive review mode ──────────────────────────────────────────


def show_review_mode(
    translations: dict[str, str],
    scores: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Interactive review UI for translations.

    Args:
        translations: Mapping of string key -> translated text.
        scores: Optional mapping of string key -> confidence score (0.0-1.0).

    Returns:
        {"accepted": {key: text, ...}, "edited": {key: text, ...}, "skipped": [key, ...]}
    """
    result: dict[str, Any] = {"accepted": {}, "edited": {}, "skipped": []}

    if _should_suppress():
        # In quiet/json mode, accept everything by default.
        result["accepted"] = dict(translations)
        return result

    total = len(translations)
    if total == 0:
        show_info("No translations to review.")
        return result

    # Show summary panel
    console.print()
    console.print(
        Panel(
            f"[bold bright_cyan]TRANSLATION REVIEW[/]\n\n"
            f"  [bold white]Total strings:[/]  [bold]{total}[/]\n"
            f"  [bold white]With scores:[/]    [bold]{'Yes' if scores else 'No'}[/]",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()

    # Show score distribution if available
    if scores:
        high = sum(1 for s in scores.values() if s >= 0.8)
        mid = sum(1 for s in scores.values() if 0.5 <= s < 0.8)
        low = sum(1 for s in scores.values() if s < 0.5)
        console.print(f"  [bold green]\u25cf[/] High confidence (>=0.8): [bold]{high}[/]")
        console.print(f"  [bold yellow]\u25cf[/] Medium confidence:       [bold]{mid}[/]")
        console.print(f"  [bold red]\u25cf[/] Low confidence (<0.5):   [bold]{low}[/]")
        console.print()

    # Main mode selection
    console.print("  [bold white]Review options:[/]")
    console.print("    [bold white][A][/] Accept all translations")
    console.print("    [bold white][H][/] Accept high confidence only (>=0.8), review rest")
    console.print("    [bold white][I][/] Interactive — review one by one")
    console.print("    [bold white][S][/] Save review report (accept all)")
    console.print("    [bold white][Q][/] Quit review (skip all)")
    console.print()

    mode = Prompt.ask(
        "  [bold yellow]\u2192 Choose review mode[/]",
        choices=["a", "h", "i", "s", "q"],
        default="a",
    ).lower()

    if mode == "q":
        result["skipped"] = list(translations.keys())
        show_warning("Review cancelled. All translations skipped.")
        return result

    if mode == "a" or mode == "s":
        result["accepted"] = dict(translations)
        show_success(f"Accepted all {total} translations.")
        return result

    if mode == "h":
        if not scores:
            show_warning("No scores available. Falling back to interactive mode.")
            mode = "i"
        else:
            # Auto-accept high confidence, queue rest for interactive review
            review_keys = []
            for key, text in translations.items():
                score = scores.get(key, 0.0)
                if score >= 0.8:
                    result["accepted"][key] = text
                else:
                    review_keys.append(key)
            show_success(f"Auto-accepted {len(result['accepted'])} high-confidence translations.")
            if not review_keys:
                show_info("No low-confidence translations to review.")
                return result
            show_info(f"Reviewing {len(review_keys)} remaining translations...")
            console.print()
            # Fall through to interactive for remaining
            _interactive_review(translations, scores, review_keys, result)
            return result

    # mode == "i": full interactive
    review_keys = list(translations.keys())
    _interactive_review(translations, scores, review_keys, result)
    return result


def _interactive_review(
    translations: dict[str, str],
    scores: dict[str, float] | None,
    keys: list[str],
    result: dict[str, Any],
) -> None:
    """Review translations one by one interactively."""
    total = len(keys)
    for idx, key in enumerate(keys, 1):
        text = translations[key]
        score = scores.get(key) if scores else None

        # Build display
        score_str = ""
        if score is not None:
            if score >= 0.8:
                score_str = f"  [bold green]Score: {score:.0%}[/]"
            elif score >= 0.5:
                score_str = f"  [bold yellow]Score: {score:.0%}[/]"
            else:
                score_str = f"  [bold red]Score: {score:.0%}[/]"

        console.print(
            Panel(
                f"[dim]({idx}/{total})[/]  [bold white]{key}[/]{score_str}\n\n"
                f"  [bright_cyan]{text}[/]",
                border_style="cyan",
                box=box.ROUNDED,
                padding=(0, 2),
            )
        )

        choice = Prompt.ask(
            "  [bold yellow]\u2192 [A]ccept / [E]dit / [S]kip[/]",
            choices=["a", "e", "s"],
            default="a",
        ).lower()

        if choice == "a":
            result["accepted"][key] = text
            console.print(f"  [bold green]\u25cf[/] Accepted\n")
        elif choice == "e":
            new_text = Prompt.ask("  [bold cyan]\u2192 Enter new translation[/]")
            result["edited"][key] = new_text.strip()
            console.print(f"  [bold cyan]\u25cf[/] Edited\n")
        else:
            result["skipped"].append(key)
            console.print(f"  [bold yellow]\u25cf[/] Skipped\n")

    # Summary
    console.print()
    show_success(
        f"Review complete: {len(result['accepted'])} accepted, "
        f"{len(result['edited'])} edited, {len(result['skipped'])} skipped."
    )


# ── Cost estimate ────────────────────────────────────────────────────


def show_cost_estimate(
    estimates: list[dict[str, Any]],
    uncached_count: int,
    lang_count: int,
) -> None:
    """Display cost comparison table across all providers.

    Args:
        estimates: List of dicts with keys: provider, cost, time, quality.
            - provider: str (display name)
            - cost: float (estimated USD)
            - time: str (estimated duration, e.g. "~2m")
            - quality: str (rating, e.g. "Excellent", "Good", "Fair")
        uncached_count: Number of strings that need translation.
        lang_count: Number of target languages.
    """
    if _should_suppress():
        return

    console.print()
    console.print(
        Panel(
            f"[bold bright_cyan]COST ESTIMATE[/]\n\n"
            f"  [bold white]Strings to translate:[/]  [bold]{uncached_count}[/]\n"
            f"  [bold white]Target languages:[/]      [bold]{lang_count}[/]\n"
            f"  [bold white]Total API calls:[/]       [bold]{uncached_count * lang_count}[/]",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()

    table = Table(
        title="[bold bright_cyan]Provider Comparison[/]",
        box=box.ROUNDED,
        border_style="cyan",
        show_lines=True,
        padding=(0, 1),
        title_justify="center",
    )
    table.add_column("Provider", style="bold white", min_width=20)
    table.add_column("Est. Cost", justify="right", style="bold")
    table.add_column("Est. Time", justify="center")
    table.add_column("Quality", justify="center")

    best_cost = min((e["cost"] for e in estimates), default=0)

    for est in estimates:
        cost = est["cost"]
        quality = est.get("quality", "N/A")

        # Color-code quality
        if quality.lower() in ("excellent", "best"):
            quality_str = f"[bold green]{quality}[/]"
        elif quality.lower() in ("good", "high"):
            quality_str = f"[bold bright_cyan]{quality}[/]"
        elif quality.lower() in ("fair", "medium"):
            quality_str = f"[bold yellow]{quality}[/]"
        else:
            quality_str = f"[dim]{quality}[/]"

        # Highlight cheapest
        cost_str = f"${cost:.4f}"
        if cost == best_cost and len(estimates) > 1:
            cost_str = f"[bold green]{cost_str} *[/]"
        else:
            cost_str = f"[white]{cost_str}[/]"

        table.add_row(
            est["provider"],
            cost_str,
            est.get("time", "N/A"),
            quality_str,
        )

    console.print(table)

    # Show recommendation
    if estimates:
        best = min(estimates, key=lambda e: e["cost"])
        console.print()
        console.print(
            f"  [bold green]\u25cf[/] [bold]Recommendation:[/] "
            f"[bold bright_cyan]{best['provider']}[/] "
            f"— lowest cost at [bold]${best['cost']:.4f}[/]"
        )
    console.print()


# ── Quality report ───────────────────────────────────────────────────


def show_quality_report(
    scores: dict[str, float],
    threshold: float,
) -> None:
    """Show quality gate results.

    Args:
        scores: Mapping of string key -> quality score (0.0-1.0).
        threshold: Minimum acceptable score.
    """
    if _should_suppress():
        return

    above = {k: v for k, v in scores.items() if v >= threshold}
    below = {k: v for k, v in scores.items() if v < threshold}

    console.print()
    console.print(
        Panel(
            f"[bold bright_cyan]QUALITY GATE REPORT[/]\n\n"
            f"  [bold white]Threshold:[/]     [bold]{threshold:.0%}[/]\n"
            f"  [bold white]Total scored:[/]  [bold]{len(scores)}[/]\n"
            f"  [bold green]Passed:[/]        [bold green]{len(above)}[/]\n"
            f"  [bold red]Flagged:[/]       [bold red]{len(below)}[/]",
            border_style="cyan" if not below else "yellow",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )

    if below:
        console.print()
        table = Table(
            title="[bold yellow]Flagged Translations[/]",
            box=box.ROUNDED,
            border_style="yellow",
            show_lines=True,
            padding=(0, 1),
            title_justify="center",
        )
        table.add_column("Key", style="bold white", min_width=30)
        table.add_column("Score", justify="center", min_width=10)
        table.add_column("Status", justify="center")

        for key, score in sorted(below.items(), key=lambda x: x[1]):
            if score < 0.3:
                score_style = "bold red"
                status = "[bold red]POOR[/]"
            elif score < 0.5:
                score_style = "red"
                status = "[red]LOW[/]"
            else:
                score_style = "yellow"
                status = "[yellow]BELOW THRESHOLD[/]"
            table.add_row(key, f"[{score_style}]{score:.0%}[/]", status)

        console.print(table)
    else:
        console.print()
        show_success("All translations passed the quality gate.")

    console.print()


# ── Regression / drift report ────────────────────────────────────────


def show_regression_report(drifted: list[dict[str, Any]]) -> None:
    """Show translation drift detection results.

    Args:
        drifted: List of dicts with keys: key, old, new, lang (optional).
    """
    if _should_suppress():
        return

    console.print()

    if not drifted:
        console.print(
            Panel(
                "[bold green]NO DRIFT DETECTED[/]\n\n"
                "  All translations match the expected baseline.",
                border_style="green",
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )
        console.print()
        return

    console.print(
        Panel(
            f"[bold yellow]TRANSLATION DRIFT DETECTED[/]\n\n"
            f"  [bold white]Drifted strings:[/]  [bold red]{len(drifted)}[/]",
            border_style="yellow",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()

    table = Table(
        title="[bold yellow]Drifted Translations[/]",
        box=box.ROUNDED,
        border_style="yellow",
        show_lines=True,
        padding=(0, 1),
        title_justify="center",
    )
    table.add_column("Key", style="bold white", min_width=20)
    table.add_column("Language", justify="center", style="dim cyan", min_width=8)
    table.add_column("Old", style="red", min_width=25)
    table.add_column("New", style="green", min_width=25)

    for entry in drifted:
        lang = entry.get("lang", "-")
        table.add_row(
            entry["key"],
            str(lang),
            str(entry["old"]),
            str(entry["new"]),
        )

    console.print(table)
    console.print()
    show_warning(
        "Review drifted translations above. "
        "Run with --force to overwrite, or update your baseline."
    )
    console.print()


# ── JSON report output ───────────────────────────────────────────────


def show_json_report(report_data: Any) -> None:
    """Print structured JSON to stdout.

    This function works in ALL output modes, including json and quiet.
    It writes directly to sys.stdout to avoid Rich formatting.
    """
    output = json.dumps(report_data, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write(output + "\n")
    sys.stdout.flush()


# ── Glossary loaded ──────────────────────────────────────────────────


def show_glossary_loaded(term_count: int, lang_count: int) -> None:
    """Show glossary info after loading."""
    if _should_suppress():
        return

    console.print(
        Panel(
            f"[bold bright_cyan]GLOSSARY LOADED[/]\n\n"
            f"  [bold white]Terms:[/]      [bold]{term_count}[/]\n"
            f"  [bold white]Languages:[/]  [bold]{lang_count}[/]",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )
    console.print()


# ── Changed-only stats ───────────────────────────────────────────────


def show_changed_only_stats(changed_files: list[str], new_strings: int) -> None:
    """Show stats for --changed-only mode.

    Args:
        changed_files: List of file paths that have changes.
        new_strings: Number of new/changed strings detected.
    """
    if _should_suppress():
        return

    console.print()
    console.print(
        Panel(
            f"[bold bright_cyan]CHANGED-ONLY MODE[/]\n\n"
            f"  [bold white]Changed files:[/]   [bold]{len(changed_files)}[/]\n"
            f"  [bold white]New strings:[/]     [bold]{new_strings}[/]",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )

    if changed_files:
        console.print()
        for f in changed_files[:20]:
            console.print(f"  [dim]\u2022[/] [cyan]{f}[/]")
        if len(changed_files) > 20:
            console.print(f"  [dim]... and {len(changed_files) - 20} more files[/]")

    console.print()
