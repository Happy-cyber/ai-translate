"""Rich terminal UI — Style 4: Gradient Modern — production-grade."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

console = Console()

# ── Output mode ──────────────────────────────────────────────────────

_output_mode: str = "normal"  # "normal", "quiet", "json"


def set_output_mode(mode: str) -> None:
    global _output_mode
    if mode not in ("normal", "quiet", "json"):
        raise ValueError(f"Invalid output mode: {mode!r}")
    _output_mode = mode


def _should_suppress() -> bool:
    return _output_mode in ("quiet", "json")


# ── Brand ─────────────────────────────────────────────────────────────

from ai_translate import __version__ as VERSION

# ── Semantic icons ────────────────────────────────────────────────────
_OK = "[bold green]\u2713[/]"          # ✓
_FAIL = "[bold red]\u2717[/]"          # ✗
_WARN = "[bold yellow]\u25b3[/]"       # △
_INFO = "[dim]\u203a[/]"              # ›
_ARROW = "[bold bright_cyan]\u276f[/]" # ❯

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


# ── Sign-off ─────────────────────────────────────────────────────────


def _show_signoff() -> None:
    if _should_suppress():
        return
    console.print()
    t = Text()
    t.append("  \u2500\u2500 ", style="dim")
    t.append("Thank you ", style="bright_cyan")
    t.append("for using ", style="dim")
    t.append("AI Translate", style="bright_magenta")
    t.append("! ", style="dim")
    t.append("\u2b50", style="bright_yellow")
    console.print(t)
    console.print("  [dim]Star us \u2192 github.com/Happy-cyber/ai-translate[/]")
    console.print("  [dim]Made with[/] [red]\u2665[/] [dim]by Happy-Cyber[/]")
    console.print()


# ── Boot sequence ─────────────────────────────────────────────────────


def show_boot_sequence() -> None:
    if _should_suppress():
        return
    console.print()
    t = Text()
    t.append("  \u2500\u2500\u2500 AI ", style="bright_cyan")
    t.append("Translate ", style="bright_magenta")
    t.append("\u2500" * 42, style="dim")
    console.print(t)
    console.print(f"  [dim]v{VERSION}  \u2022  Zero-config AI localization  \u2022  by Happy-Cyber[/]")
    console.print()
    console.print("  [black on bright_green] \u2713 READY [/]  [dim]Engine \u2022 Environment \u2022 Loaded[/]")
    console.print()


# ── API key status ────────────────────────────────────────────────────


def show_env_key_status(key_status: dict[str, str | None], labels: dict[str, str]) -> None:
    if _should_suppress():
        return
    console.print("  [bold]API Keys[/]")
    active_parts = []
    inactive_parts = []
    for key, label in labels.items():
        val = key_status.get(key)
        if val:
            masked = val[:4] + "\u2022" * 6 + val[-4:] if len(val) > 12 else "\u2022" * 8
            active_parts.append(f"[black on green] \u2713 {label} [/] [dim]{masked}[/]")
        else:
            inactive_parts.append(f"[black on bright_black] \u25cb {label} [/]")
    console.print("  " + "  ".join(active_parts + inactive_parts))
    console.print()


# ── Interactive provider selection ────────────────────────────────────


def prompt_provider_selection() -> str:
    if _should_suppress():
        return PROVIDER_MENU[0]["key"]
    console.print("  [bold bright_cyan]Select Translation Engine[/]")
    console.print()
    for idx, p in enumerate(PROVIDER_MENU, 1):
        console.print(
            f"  [bold white][{idx}][/]  {p['icon']}  "
            f"[bold {p['color']}]{p['label']}[/]  "
            f"[dim]\u00b7 {p['tag']}[/]"
        )
    console.print()
    while True:
        choice = IntPrompt.ask(
            f"  {_ARROW} [bold]Choice[/]",
            choices=[str(i) for i in range(1, len(PROVIDER_MENU) + 1)],
        )
        selected = PROVIDER_MENU[choice - 1]
        console.print(f"  {_OK} [bold]{selected['label']}[/]")
        console.print()
        return selected["key"]


def show_openrouter_model_selection(
    models: list[dict[str, str]],
    model_flag: str | None = None,
) -> str:
    if model_flag:
        if not _should_suppress():
            console.print(f"  {_INFO} Model: [bold]{model_flag}[/] [dim](from --model flag)[/]")
        return model_flag
    if _should_suppress():
        return models[0]["id"] if models else ""
    console.print("  [bold bright_yellow]Select OpenRouter Model[/]")
    console.print()
    for idx, m in enumerate(models, 1):
        console.print(
            f"  [bold white][{idx:2d}][/]  "
            f"[bold {m['color']}]{m['name']}[/]  "
            f"[dim]\u00b7 {m['tag']}[/]"
        )
        console.print(f"       [dim]{m['id']}[/]")
    console.print()
    while True:
        choice = IntPrompt.ask(
            f"  {_ARROW} [bold]Select model[/]",
            choices=[str(i) for i in range(1, len(models) + 1)],
        )
        selected = models[choice - 1]
        console.print(f"  {_OK} [bold]{selected['name']}[/] [dim]({selected['id']})[/]")
        console.print()
        return selected["id"]


# ── API key prompt & help ─────────────────────────────────────────────


def show_api_key_help(provider: str, url: str) -> None:
    if _should_suppress():
        return
    label = next((p["label"] for p in PROVIDER_MENU if p["key"] == provider), provider)
    console.print(
        Panel(
            f"  [bold]Get your {label} key:[/]\n"
            f"  [bold underline bright_blue]{url}[/]\n\n"
            f"  [dim]The key will be saved to .env automatically.[/]",
            title="[bold bright_cyan]\U0001f511 API Key Required[/]",
            border_style="bright_cyan",
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )


def prompt_api_key(provider_name: str, env_var: str) -> str:
    while True:
        key = Prompt.ask(f"  {_ARROW} [bold]{provider_name} API key[/]")
        key = key.strip()
        if key:
            return key
        console.print(f"  {_FAIL} Key cannot be empty.")


# ── Status helpers ────────────────────────────────────────────────────


def prompt_choose_path(
    label: str,
    paths: list[Any],
    detail_fn: Any = None,
    pref_key: str = "",
    project_root: Any = None,
) -> Any:
    """Prompt user to choose one path from a list.

    If a saved preference exists for this project and the path is still
    valid, it is used automatically (no prompt). Otherwise, prompts the
    user and saves their choice for future runs.

    Args:
        label: What the user is choosing (e.g. "locale directory", ".env file")
        paths: List of Path objects
        detail_fn: Optional callable(path) -> str for extra info per path
        pref_key: Key to store the choice in project prefs (e.g. "locale_dir")
        project_root: Project root Path (needed for saving prefs)

    Returns:
        The chosen path.
    """
    from pathlib import Path as _Path

    # Check saved preference
    if pref_key and project_root:
        from ai_translate.services.cache import load_prefs
        prefs = load_prefs(_Path(str(project_root)))
        saved = prefs.get(pref_key)
        if saved:
            saved_path = _Path(saved)
            # Verify saved path is still valid and in the list
            for p in paths:
                if _Path(str(p)).resolve() == saved_path.resolve() and saved_path.exists():
                    if not _should_suppress():
                        console.print(f"  {_OK} Using saved {label}: [bold]{p}[/]")
                    return p
            # Saved path no longer valid — will re-prompt

    if _should_suppress():
        return paths[0]

    console.print()
    console.print(f"  [bold yellow]\u25b3[/] [yellow]Multiple {label}s found. Please choose one:[/]")
    console.print()

    for idx, p in enumerate(paths, 1):
        detail = ""
        if detail_fn:
            try:
                detail = detail_fn(p)
                if detail:
                    detail = f"  [dim]{detail}[/]"
            except Exception:
                pass
        console.print(f"  [bold white][{idx}][/]  [bold]{p}[/]{detail}")

    console.print()

    while True:
        choice = IntPrompt.ask(
            f"  {_ARROW} [bold]Choose {label}[/]",
            choices=[str(i) for i in range(1, len(paths) + 1)],
        )
        chosen = paths[choice - 1]
        console.print(f"  {_OK} Using: [bold]{chosen}[/]")
        console.print()

        # Save choice for future runs
        if pref_key and project_root:
            from ai_translate.services.cache import save_pref
            save_pref(_Path(str(project_root)), pref_key, str(chosen))
            console.print(f"  {_INFO} Choice saved — won't ask again for this project.")
            console.print()

        return chosen


def show_success(msg: str) -> None:
    if _should_suppress():
        return
    console.print(f"  {_OK} {msg}")


def show_warning(msg: str) -> None:
    if _should_suppress():
        return
    console.print(f"  {_WARN} [yellow]{msg}[/]")


def show_error(msg: str) -> None:
    if _should_suppress():
        return
    console.print(f"  {_FAIL} [red]{msg}[/]")


def show_info(msg: str) -> None:
    if _should_suppress():
        return
    console.print(f"  {_INFO} [dim]{msg}[/]")


def show_step(step_num: int, title: str) -> None:
    if _should_suppress():
        return
    console.print()
    console.print(f"  [bold bright_cyan]\u2500\u2500 {title}[/] [dim]step {step_num}[/]")
    console.print()


# ── Auto-config summary ──────────────────────────────────────────────


def show_auto_config(decisions: list[tuple[str, str]]) -> None:
    if _should_suppress():
        return
    tbl = Table(box=None, show_header=False, padding=(0, 2), show_edge=False, expand=False)
    tbl.add_column("key", style="dim", min_width=12, no_wrap=True)
    tbl.add_column("val", style="bold")
    for label, value in decisions:
        tbl.add_row(label, value)
    console.print(
        Panel(tbl, title="[bold bright_cyan]Configuration[/]", border_style="bright_cyan",
              box=box.ROUNDED, padding=(0, 1))
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
            f"  [bold bright_magenta]{icon}[/]  [dim]\u2502[/]  "
            f"[bold bright_cyan]{lang_count}[/] [dim]languages[/]  [dim]\u2502[/]  "
            f"[dim]{project_path}[/]",
            border_style="bright_magenta",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )
    console.print()


def show_languages(target_languages: dict[str, str]) -> None:
    if _should_suppress():
        return
    tags = "  ".join(
        f"[bold]{name}[/][dim]({code})[/]" for code, name in target_languages.items()
    )
    console.print(f"  {_INFO} {tags}")
    console.print()


# ── Scan results ──────────────────────────────────────────────────────


def show_scan_results(source_count: int, missing_count: int, lang_count: int) -> None:
    if _should_suppress():
        return
    pct = (missing_count / source_count * 100) if source_count else 0
    console.print(
        f"  [bold bright_magenta]{source_count:,}[/] strings found  "
        f"[dim]\u2502[/]  [bold yellow]{missing_count:,}[/] need translation "
        f"[dim]({pct:.0f}%)[/]  "
        f"[dim]\u2502[/]  [bold]{lang_count}[/] languages"
    )


def show_cache_stats(cached: int, uncached: int) -> None:
    if _should_suppress():
        return
    total = cached + uncached
    if cached:
        pct = cached / total * 100 if total else 0
        console.print(
            f"  [black on bright_cyan] CACHE [/] [bold green]{cached:,}[/] hits [dim]({pct:.0f}%)[/]   "
            f"[black on bright_yellow] NEW [/] [bold]{uncached:,}[/] strings"
        )
    else:
        console.print(
            f"  [black on bright_yellow] NEW [/] [bold]{uncached:,}[/] strings to translate [dim](no cache hits)[/]"
        )


# ── Dry-run ───────────────────────────────────────────────────────────


def show_dry_run_banner() -> None:
    if _should_suppress():
        return
    console.print(
        f"  [bold yellow on black] DRY RUN [/] [dim]No API calls, no file writes[/]"
    )
    console.print()


def show_dry_run_messages(messages: list[str], lang_count: int) -> None:
    if _should_suppress():
        return
    preview = messages[:30]
    lines = "\n".join(f"  [dim]\u2502[/] {m}" for m in preview)
    remaining = len(messages) - len(preview)
    if remaining > 0:
        lines += f"\n  [dim]\u2502 ... and {remaining:,} more[/]"
    console.print()
    console.print(
        Panel(
            f"  [bold]Would translate:[/] [bold bright_magenta]{len(messages):,}[/] strings "
            f"\u00d7 [bold]{lang_count}[/] languages\n\n{lines}",
            title="[bold yellow]Dry Run Preview[/]",
            border_style="yellow",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )
    console.print()
    console.print(f"  {_OK} [bold green]Dry run complete.[/] No changes made.")
    _show_signoff()


# ── Auth failure ──────────────────────────────────────────────────────


def show_auth_failure(provider_name: str) -> None:
    if _should_suppress():
        return
    console.print(
        Panel(
            f"  {_FAIL} API key validation failed for [bold]{provider_name}[/]\n\n"
            f"  [dim]Possible causes:[/]\n"
            f"  [dim]  \u2022 Key is invalid, expired, or has wrong permissions[/]\n"
            f"  [dim]  \u2022 Network connectivity issue[/]\n"
            f"  [dim]  \u2022 Provider service outage[/]\n\n"
            f"  [bold]Fix:[/] Check your API key in [bold].env[/] or re-run to enter a new one.",
            title="[bold red]Authentication Failed[/]",
            border_style="red",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )
    _show_signoff()


# ── Progress bar ──────────────────────────────────────────────────────


def create_translation_progress() -> Progress:
    return Progress(
        SpinnerColumn("dots", style="bright_cyan"),
        TextColumn("[bold bright_cyan]Translating[/]"),
        BarColumn(bar_width=35, style="dim", complete_style="bright_magenta", finished_style="bright_green"),
        TaskProgressColumn(),
        TextColumn("[dim]\u2502[/]"),
        TimeElapsedColumn(),
        TextColumn("[dim]\u2192[/]"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


# ── Translation results ──────────────────────────────────────────────


def show_translation_results(stats: dict[str, int]) -> None:
    if _should_suppress():
        return
    if not stats:
        return
    items = [f"[bold]{lang}[/] [dim]({count})[/]" for lang, count in stats.items()]
    console.print(f"  {_OK} Updated: {', '.join(items)}")


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

    console.print()
    console.print(f"  [bold bright_cyan]\u2500\u2500 REPORT[/]")
    console.print()

    icon = PLATFORM_ICONS.get(platform, platform)
    speed = f"{translated / elapsed:.1f}/s" if elapsed > 0 and translated > 0 else "n/a"

    # Perfectly aligned report — two columns side by side
    # Compute value width dynamically from longest value
    v1_vals = [icon, provider, f"{source_count:,} total", f"{elapsed:.1f}s"]
    V = max(len(v) for v in v1_vals) + 2  # padding
    W = 14  # label width

    rows = [
        ("Platform", icon, "Missing", f"{missing_count:,}"),
        ("Provider", provider, "Translated", f"{translated:,}"),
        ("Strings", f"{source_count:,} total", "Languages", f"{lang_count}"),
        ("Time", f"{elapsed:.1f}s", "Speed", speed),
    ]
    for k1, v1, k2, v2 in rows:
        console.print(
            f"  [dim]{k1:<{W}}[/][bold]{v1:<{V}}[/]"
            f"[dim]{k2:<{W}}[/][bold bright_magenta]{v2}[/]"
        )
    console.print()

    # Status banner
    if success and translated > 0:
        total_entries = translated * lang_count
        console.print(
            Panel(
                f"[bold bright_green]\u2713[/] [bold white]COMPLETE[/]\n\n"
                f"  Your app now speaks [bold bright_magenta]{lang_count}[/] languages.\n"
                f"  [bold]{total_entries:,}[/] translation entries generated.",
                border_style="bright_green",
                box=box.ROUNDED,
                padding=(0, 2),
            )
        )
    elif translated == 0 and missing_count == 0:
        console.print(
            Panel(
                f"[bold bright_cyan]\u2713[/] [bold white]ALL UP TO DATE[/]\n\n"
                f"  Every string is translated. Nothing to do.",
                border_style="bright_cyan",
                box=box.ROUNDED,
                padding=(0, 2),
            )
        )
    else:
        console.print(
            Panel(
                f"[bold yellow]\u25b3[/] [bold white]PARTIALLY COMPLETE[/]\n\n"
                f"  Translated [bold]{translated}[/] of [bold]{missing_count}[/] strings.\n"
                f"  Run again to retry failed translations \u2014 progress is cached.",
                border_style="yellow",
                box=box.ROUNDED,
                padding=(0, 2),
            )
        )

    _show_signoff()


# ── Target language prompt ────────────────────────────────────────────


def prompt_target_languages() -> dict[str, str]:
    if _should_suppress():
        return {}
    console.print(f"  {_WARN} [yellow]No target languages detected.[/]")
    console.print("  [dim]Enter language codes separated by commas (e.g. es,fr,de,ja,ko,zh)[/]")
    console.print()
    while True:
        raw = Prompt.ask(f"  {_ARROW} [bold]Target languages[/]")
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        if codes:
            return {code: COMMON_LANGUAGES.get(code, code) for code in codes}
        console.print(f"  {_FAIL} Please enter at least one language code.")


# ── Interactive review mode ──────────────────────────────────────────


def show_review_mode(
    translations: dict[str, str],
    scores: dict[str, float] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"accepted": {}, "edited": {}, "skipped": []}
    if _should_suppress():
        result["accepted"] = dict(translations)
        return result
    total = len(translations)
    if total == 0:
        show_info("No translations to review.")
        return result

    console.print()
    console.print(f"  [bold bright_cyan]Translation Review[/]  [dim]\u2502[/]  [bold]{total}[/] strings")
    console.print()

    if scores:
        high = sum(1 for s in scores.values() if s >= 0.8)
        mid = sum(1 for s in scores.values() if 0.5 <= s < 0.8)
        low = sum(1 for s in scores.values() if s < 0.5)
        console.print(
            f"  [green]\u2588[/] High \u2265 80%: [bold]{high}[/]  "
            f"[yellow]\u2588[/] Medium: [bold]{mid}[/]  "
            f"[red]\u2588[/] Low < 50%: [bold]{low}[/]"
        )
        console.print()

    console.print("  [bold]Review mode:[/]")
    console.print("    [bold white]A[/] Accept all    [bold white]H[/] Accept high-confidence only")
    console.print("    [bold white]I[/] Interactive    [bold white]Q[/] Skip all")
    console.print()

    mode = Prompt.ask(
        f"  {_ARROW} [bold]Choice[/]",
        choices=["a", "h", "i", "q"],
        default="a",
    ).lower()

    if mode == "q":
        result["skipped"] = list(translations.keys())
        show_warning("Review cancelled. All translations skipped.")
        return result
    if mode == "a":
        result["accepted"] = dict(translations)
        show_success(f"Accepted all {total} translations.")
        return result
    if mode == "h":
        if not scores:
            show_warning("No scores available. Falling back to interactive mode.")
            mode = "i"
        else:
            review_keys = []
            for key, text in translations.items():
                if scores.get(key, 0.0) >= 0.8:
                    result["accepted"][key] = text
                else:
                    review_keys.append(key)
            show_success(f"Auto-accepted {len(result['accepted'])} high-confidence translations.")
            if not review_keys:
                show_info("No low-confidence translations to review.")
                return result
            show_info(f"Reviewing {len(review_keys)} remaining...")
            console.print()
            _interactive_review(translations, scores, review_keys, result)
            return result

    review_keys = list(translations.keys())
    _interactive_review(translations, scores, review_keys, result)
    return result


def _interactive_review(
    translations: dict[str, str],
    scores: dict[str, float] | None,
    keys: list[str],
    result: dict[str, Any],
) -> None:
    total = len(keys)
    for idx, key in enumerate(keys, 1):
        text = translations[key]
        score = scores.get(key) if scores else None
        score_str = ""
        if score is not None:
            color = "green" if score >= 0.8 else ("yellow" if score >= 0.5 else "red")
            score_str = f"  [{color}]{score:.0%}[/]"
        console.print(
            f"  [dim]({idx}/{total})[/]  [bold]{key}[/]{score_str}\n"
            f"  [bright_cyan]{text}[/]"
        )
        choice = Prompt.ask(
            f"  {_ARROW} [bold][A]ccept [E]dit [S]kip[/]",
            choices=["a", "e", "s"],
            default="a",
        ).lower()
        if choice == "a":
            result["accepted"][key] = text
        elif choice == "e":
            new_text = Prompt.ask(f"  {_ARROW} [bold]New translation[/]")
            result["edited"][key] = new_text.strip()
        else:
            result["skipped"].append(key)
        console.print()

    console.print(
        f"  {_OK} Review done: [bold green]{len(result['accepted'])}[/] accepted, "
        f"[bold bright_cyan]{len(result['edited'])}[/] edited, "
        f"[bold yellow]{len(result['skipped'])}[/] skipped"
    )


# ── Cost estimate ────────────────────────────────────────────────────


def show_cost_estimate(
    estimates: list[dict[str, Any]],
    uncached_count: int,
    lang_count: int,
) -> None:
    if _should_suppress():
        return
    console.print()
    console.print(
        f"  [bold bright_cyan]Cost Estimate[/]  [dim]\u2502[/]  "
        f"[bold bright_magenta]{uncached_count:,}[/] strings \u00d7 "
        f"[bold]{lang_count}[/] languages"
    )
    console.print()

    tbl = Table(box=box.ROUNDED, border_style="bright_cyan", show_header=True, padding=(0, 2))
    tbl.add_column("Provider", style="bold white", min_width=24)
    tbl.add_column("Cost", justify="right", min_width=16)
    tbl.add_column("Time", justify="center", min_width=8)
    tbl.add_column("Quality", justify="center", min_width=10)

    best_cost = min((e["cost"] for e in estimates), default=0)
    for est in estimates:
        cost = est["cost"]
        quality = est.get("quality", "N/A")
        qcolors = {"excellent": "bold green", "best": "bold green",
                    "good": "bright_cyan", "high": "bright_cyan",
                    "fair": "yellow", "medium": "yellow"}
        qstyle = qcolors.get(quality.lower(), "dim")
        cost_str = f"${cost:.4f}"
        if cost == best_cost and len(estimates) > 1:
            cost_str = f"[bold green]{cost_str} \u2190 best[/]"
        tbl.add_row(est["provider"], cost_str, est.get("time", "N/A"), f"[{qstyle}]{quality}[/]")

    console.print(tbl)
    console.print()
    console.print(f"  {_OK} [bold green]Estimate complete.[/]")
    _show_signoff()


# ── Quality report ───────────────────────────────────────────────────


def show_quality_report(scores: dict[str, float], threshold: float) -> None:
    if _should_suppress():
        return
    above = {k: v for k, v in scores.items() if v >= threshold}
    below = {k: v for k, v in scores.items() if v < threshold}
    console.print()
    console.print(
        f"  [bold bright_cyan]Quality Gate[/] [dim]threshold {threshold:.0%}[/]  "
        f"[dim]\u2502[/]  [bold green]{len(above)}[/] passed  "
        f"[dim]\u2502[/]  [bold red]{len(below)}[/] flagged"
    )
    if below:
        console.print()
        for key, score in sorted(below.items(), key=lambda x: x[1])[:10]:
            color = "red" if score < 0.5 else "yellow"
            console.print(f"  [{color}]{score:.0%}[/]  [dim]{key[:60]}[/]")
        if len(below) > 10:
            console.print(f"  [dim]... and {len(below) - 10} more[/]")
    else:
        console.print(f"  {_OK} All translations passed.")
    console.print()


# ── Regression / drift report ────────────────────────────────────────


def show_regression_report(drifted: list[dict[str, Any]]) -> None:
    if _should_suppress():
        return
    console.print()
    if not drifted:
        console.print(
            Panel(
                f"  {_OK} [bold green]No drift detected.[/] All translations match the baseline.",
                border_style="bright_green",
                box=box.ROUNDED,
                padding=(0, 2),
            )
        )
        console.print(f"  {_OK} [bold green]Regression check complete.[/]")
        _show_signoff()
        return

    console.print(
        f"  {_WARN} [bold yellow]Translation drift detected:[/] "
        f"[bold red]{len(drifted)}[/] string(s)"
    )
    console.print()

    tbl = Table(box=box.ROUNDED, border_style="yellow", show_header=True, padding=(0, 1))
    tbl.add_column("Key", style="bold white", max_width=25, overflow="ellipsis")
    tbl.add_column("Lang", justify="center", style="dim cyan", min_width=6)
    tbl.add_column("Old", style="red", max_width=30, overflow="ellipsis")
    tbl.add_column("New", style="green", max_width=30, overflow="ellipsis")
    for entry in drifted[:20]:
        tbl.add_row(entry["key"], str(entry.get("lang", "-")), str(entry["old"]), str(entry["new"]))
    if len(drifted) > 20:
        tbl.add_row("[dim]...[/]", "", f"[dim]+{len(drifted) - 20} more[/]", "")
    console.print(tbl)
    console.print()
    console.print(f"  {_WARN} [yellow]Review drifted translations. Run with --force to overwrite.[/]")
    _show_signoff()


# ── JSON report output ───────────────────────────────────────────────


def show_json_report(report_data: Any) -> None:
    output = json.dumps(report_data, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write(output + "\n")
    sys.stdout.flush()


# ── Glossary loaded ──────────────────────────────────────────────────


def show_glossary_loaded(term_count: int, lang_count: int) -> None:
    if _should_suppress():
        return
    console.print(f"  {_OK} Glossary: [bold]{term_count}[/] terms \u00d7 [bold]{lang_count}[/] languages")


# ── Changed-only stats ───────────────────────────────────────────────


def show_changed_only_stats(changed_files: list[str], new_strings: int) -> None:
    if _should_suppress():
        return
    console.print(
        f"  {_INFO} Changed-only: [bold]{len(changed_files)}[/] files, [bold]{new_strings}[/] strings"
    )
