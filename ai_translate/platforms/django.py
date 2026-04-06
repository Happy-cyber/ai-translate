"""Django platform handler — AST-based string extraction + PO file management."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import polib

from ai_translate.platforms import SKIP_DIRS, walk_project
from ai_translate.platforms._shared import (
    atomic_save_po,
    compile_po_files,
    ensure_po,
    extract_string,
    should_skip_message,
    NGETTEXT_CALLS,
    encode_plural,
    decode_plural,
    get_nplurals,
    PLURAL_FORMS,
)

log = logging.getLogger(__name__)

# ── Translatable call names ───────────────────────────────────────────

_TRANSLATABLE_CALLS = frozenset({
    "_", "gettext", "gettext_lazy", "ugettext", "ugettext_lazy", "ValidationError",
    "ngettext", "ngettext_lazy", "ungettext", "ungettext_lazy",
})

_SKIP_CALLS = frozenset({
    "logger", "logging", "print", "log",
    "warning", "error", "info", "debug", "critical", "exception",
})

_EXTRA_SKIP_DIRS = frozenset({"migrations", "static", "media", "locale", "staticfiles"})


# ── AST extraction (uses shared utilities) ────────────────────────────


def scan_source(project_root: Path) -> dict[str, str]:
    """AST-parse all .py files and extract translatable strings."""
    messages: dict[str, str] = {}
    skip = SKIP_DIRS | _EXTRA_SKIP_DIRS

    for dirpath, _, filenames in walk_project(project_root, extra_skip=skip):
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = dirpath / fname
            try:
                source = fpath.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(fpath))
            except (SyntaxError, OSError):
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name is None or name in _SKIP_CALLS:
                    continue
                if name not in _TRANSLATABLE_CALLS:
                    continue
                if not node.args:
                    continue
                # Handle ngettext (dual-form: singular, plural)
                if name in NGETTEXT_CALLS and len(node.args) >= 2:
                    singular = extract_string(node.args[0])
                    plural = extract_string(node.args[1])
                    if singular and plural and not should_skip_message(singular):
                        encoded = encode_plural(singular, plural)
                        messages[encoded] = encoded
                    continue
                msg = extract_string(node.args[0])
                if msg and not should_skip_message(msg):
                    norm = " ".join(msg.split())
                    messages[norm] = norm

    return messages


# ── Language detection ────────────────────────────────────────────────


_LOCALE_SKIP_DIRS = frozenset({
    "venv", ".venv", "env", "virtualenv", "node_modules", ".git",
    "__pycache__", "build", "dist", ".tox", "site-packages",
    "htmlcov", ".mypy_cache", ".ruff_cache",
    "static", "media", "staticfiles", "collected_static",
    ".idea", ".vscode", "Pods", "DerivedData",
})


def _find_all_locale_dirs(project_root: Path) -> list[Path]:
    """Find ALL locale directories in the project.

    Django projects often have multiple locale dirs:
      - project_root/locale/
      - project_root/myapp/locale/
      - project_root/conf/locale/

    Uses its own walk (NOT walk_project) because walk_project
    skips 'locale' dirs by design (for source scanning).

    Returns all found, sorted deepest first (app-level before root-level),
    so app-specific translations take priority.
    """
    found: list[Path] = []

    import os
    for dirpath_str, dirnames, _ in os.walk(project_root):
        # Skip irrelevant directories
        dirnames[:] = [
            d for d in dirnames
            if d not in _LOCALE_SKIP_DIRS and not d.endswith(".egg-info")
        ]

        dirpath = Path(dirpath_str)
        if dirpath.name == "locale" and dirpath != project_root:
            found.append(dirpath)
            dirnames.clear()  # don't recurse into locale/

    # Sort: deeper paths first (app/locale before root/locale)
    found.sort(key=lambda p: len(p.parts), reverse=True)

    return found


# The chosen locale dir for this session (reset between runs/tests)
_chosen_locale: Path | None = None


def _reset_locale_choice() -> None:
    """Reset the cached locale choice. Used in tests."""
    global _chosen_locale
    _chosen_locale = None


def _count_po_translations(locale_dir: Path) -> str:
    """Return a summary of how many .po files and translations a locale dir has."""
    po_count = 0
    translated = 0
    for po_file in locale_dir.rglob("*.po"):
        po_count += 1
        try:
            po = polib.pofile(str(po_file))
            translated += len([e for e in po if e.msgstr and e.msgstr.strip()])
        except Exception:
            pass
    if po_count:
        return f"{po_count} .po files, {translated:,} translations"
    return "empty (no .po files)"


def _find_locale_dir(project_root: Path) -> Path:
    """Find the locale directory, prompting user if multiple exist.

    If multiple locale dirs with .po files are found, asks the user
    to choose. Caches the choice for the session.
    """
    global _chosen_locale

    if _chosen_locale and _chosen_locale.is_dir():
        return _chosen_locale

    all_dirs = _find_all_locale_dirs(project_root)

    if not all_dirs:
        locale = project_root / "locale"
        locale.mkdir(parents=True, exist_ok=True)
        _chosen_locale = locale
        return locale

    # Filter to dirs that actually have .po files
    dirs_with_po = []
    for loc in all_dirs:
        if any(loc.rglob("*.po")):
            dirs_with_po.append(loc)

    if len(dirs_with_po) == 1:
        _chosen_locale = dirs_with_po[0]
        return _chosen_locale

    if len(dirs_with_po) > 1:
        # Multiple dirs with translations — ask the user (saved per project)
        from ai_translate.cli.ui import prompt_choose_path
        _chosen_locale = prompt_choose_path(
            "locale directory",
            dirs_with_po,
            detail_fn=_count_po_translations,
            pref_key="django_locale_dir",
            project_root=project_root,
        )
        return _chosen_locale

    # No dirs with .po — use first found
    _chosen_locale = all_dirs[0]
    return _chosen_locale


def _find_settings_files(project_root: Path) -> list[Path]:
    """Find Django settings files (manage.py → DJANGO_SETTINGS_MODULE → file)."""
    import re

    settings_files: list[Path] = []

    # Extract DJANGO_SETTINGS_MODULE from manage.py
    manage_py = project_root / "manage.py"
    if manage_py.is_file():
        try:
            manage_content = manage_py.read_text(errors="ignore")
            m = re.search(
                r"DJANGO_SETTINGS_MODULE['\"],\s*['\"]([^'\"]+)['\"]",
                manage_content,
            )
            if m:
                module_path = m.group(1)  # e.g. "config.settings.production"
                parts = module_path.replace(".", "/")
                candidate = project_root / (parts + ".py")
                if candidate.is_file():
                    settings_files.append(candidate)
                # Also check all .py files in the settings directory
                # (handles split settings: base.py, common.py, etc.)
                settings_dir = candidate.parent
                if settings_dir.is_dir():
                    for sibling in sorted(settings_dir.glob("*.py")):
                        if sibling not in settings_files and sibling.name != "__init__.py":
                            settings_files.append(sibling)
        except OSError:
            pass

    # Fallback: look for any settings.py in the project
    if not settings_files:
        for dirpath, _, filenames in walk_project(project_root):
            if "settings.py" in filenames:
                settings_files.append(dirpath / "settings.py")
                break

    return settings_files


def _parse_languages_from_settings(settings_files: list[Path]) -> dict[str, str]:
    """Parse LANGUAGES = [...] from Django settings files."""
    import re
    from ai_translate.cli.ui import COMMON_LANGUAGES

    langs: dict[str, str] = {}
    for fpath in settings_files:
        try:
            content = fpath.read_text(errors="ignore")
            lang_block = re.search(
                r"^LANGUAGES\s*=\s*\[(.+?)\]",
                content, re.MULTILINE | re.DOTALL,
            )
            if not lang_block:
                continue
            block = lang_block.group(1)
            for m2 in re.finditer(r"['\"]([a-z]{2}(?:-[a-zA-Z]+)?)['\"]", block):
                code = m2.group(1)
                if code not in ("en", "en-us"):
                    langs[code] = COMMON_LANGUAGES.get(code, code)
            if langs:
                break
        except OSError:
            pass
    return langs


def detect_target_languages(project_root: Path) -> dict[str, str]:
    """Detect languages — settings.py LANGUAGES first, then locale/ directory.

    Priority:
      1. LANGUAGES in settings.py — the user's explicit configuration
      2. locale/ dirs with actual .po files — real translation work exists
      3. locale/ dirs with LC_MESSAGES/ but no .po — scaffolded but empty
    """
    from ai_translate.cli.ui import COMMON_LANGUAGES

    # ── Priority 1: Check settings.py for LANGUAGES ──────────────────
    settings_files = _find_settings_files(project_root)
    langs = _parse_languages_from_settings(settings_files)
    if langs:
        return langs

    # ── Priority 2: Scan locale/ for dirs with actual .po files ──────
    locale_dir = _find_locale_dir(project_root)
    dirs_with_po: dict[str, str] = {}
    dirs_with_lc: dict[str, str] = {}

    if locale_dir.is_dir():
        for child in sorted(locale_dir.iterdir()):
            if child.is_dir() and child.name not in ("en", "en_US", "__pycache__"):
                has_lc = (child / "LC_MESSAGES").is_dir()
                has_po = has_lc and any((child / "LC_MESSAGES").glob("*.po"))
                code = child.name
                name = COMMON_LANGUAGES.get(code, code)
                if has_po:
                    dirs_with_po[code] = name
                elif has_lc:
                    dirs_with_lc[code] = name

    return dirs_with_po


def scaffold_languages(
    project_root: Path, languages: dict[str, str],
) -> list[str]:
    """Create locale/<lang>/LC_MESSAGES/django.po for each language.

    Creates both the directory structure and an empty .po file so
    the language is auto-detected on the next run.
    """
    locale_dir = _find_locale_dir(project_root)
    scaffolded: list[str] = []
    for code in languages:
        po_file = _po_path(locale_dir, code)
        if not po_file.is_file():
            po_file.parent.mkdir(parents=True, exist_ok=True)
            po = ensure_po(po_file, code)
            atomic_save_po(po, po_file)
            scaffolded.append(code)
    return scaffolded


def update_language_config(
    project_root: Path, languages: dict[str, str],
) -> list[str]:
    """Add new languages to LANGUAGES in Django settings.py.

    Preserves existing languages — only adds codes that are not already present.
    If no LANGUAGES variable exists, creates one.
    Returns list of language codes that were actually added to the config.
    """
    import re
    from ai_translate.cli.ui import COMMON_LANGUAGES

    settings_files = _find_settings_files(project_root)
    if not settings_files:
        return []

    # Find the settings file that has LANGUAGES, or use the first one
    target_file: Path | None = None
    for fpath in settings_files:
        try:
            content = fpath.read_text(errors="ignore")
            if re.search(r"^LANGUAGES\s*=\s*\[", content, re.MULTILINE):
                target_file = fpath
                break
        except OSError:
            continue

    if not target_file:
        target_file = settings_files[0]

    try:
        content = target_file.read_text(errors="ignore")
    except OSError:
        return []

    # Parse existing language codes from LANGUAGES = [...]
    existing_codes: set[str] = {"en", "en-us"}  # always consider en as existing
    lang_match = re.search(
        r"^LANGUAGES\s*=\s*\[(.+?)\]",
        content, re.MULTILINE | re.DOTALL,
    )
    if lang_match:
        block = lang_match.group(1)
        for m in re.finditer(r"['\"]([a-z]{2}(?:-[a-zA-Z]+)?)['\"]", block):
            existing_codes.add(m.group(1))

    # Determine which codes are new
    new_codes = [code for code in languages if code not in existing_codes]
    if not new_codes:
        return []

    # Build new language entries
    new_entries = []
    for code in new_codes:
        name = COMMON_LANGUAGES.get(code, code.title())
        new_entries.append(f"    ('{code}', '{name}'),")
    new_block = "\n".join(new_entries)

    if lang_match:
        # LANGUAGES exists — insert new entries before the closing ]
        # Find the position of the closing ] for the LANGUAGES block
        block_start = lang_match.start()
        block_end = lang_match.end()
        # Insert before the closing ]
        before_bracket = content[:block_end - 1].rstrip()
        # Ensure trailing comma on last existing entry
        if before_bracket and not before_bracket.endswith(","):
            before_bracket += ","
        updated = before_bracket + "\n" + new_block + "\n" + content[block_end - 1:]
    else:
        # No LANGUAGES variable — add one at the end
        all_entries = [f"    ('en', 'English'),"]
        for code in new_codes:
            name = COMMON_LANGUAGES.get(code, code.title())
            all_entries.append(f"    ('{code}', '{name}'),")
        languages_block = "\nLANGUAGES = [\n" + "\n".join(all_entries) + "\n]\n"
        updated = content.rstrip() + "\n" + languages_block

    try:
        target_file.write_text(updated, encoding="utf-8")
        log.debug("Updated LANGUAGES in %s: added %s", target_file, new_codes)
    except OSError as exc:
        log.warning("Could not update settings: %s", exc)
        return []

    return new_codes


# ── PO file management ────────────────────────────────────────────────


def _po_path(locale_dir: Path, lang_code: str) -> Path:
    return locale_dir / lang_code / "LC_MESSAGES" / "django.po"



def _collect_existing_translations(locale_dirs: list[Path], lang_code: str) -> set[str]:
    """Collect all translated msgids from ALL locale dirs for a language.

    Scans every locale directory for .po files and merges them.
    A string is considered translated if ANY locale dir has it translated.
    """
    existing: set[str] = set()
    for locale_dir in locale_dirs:
        path = _po_path(locale_dir, lang_code)
        if not path.is_file():
            continue
        try:
            po = polib.pofile(str(path))
            for entry in po:
                if entry.msgid_plural:
                    if entry.msgstr_plural and any(v.strip() for v in entry.msgstr_plural.values()):
                        existing.add(encode_plural(entry.msgid, entry.msgid_plural))
                elif entry.msgstr and entry.msgstr.strip():
                    existing.add(entry.msgid)
        except Exception as exc:
            log.debug("Could not parse PO file %s: %s", path, exc)
    return existing


def get_missing_translations(
    project_root: Path,
    source_strings: dict[str, str],
    target_languages: dict[str, str],
) -> dict[str, list[str]]:
    """Return ``{lang_code: [missing_msgids]}``.

    Uses the user's chosen locale directory only.
    """
    locale_dir = _find_locale_dir(project_root)
    missing: dict[str, list[str]] = {}

    for lang_code in target_languages:
        existing = _collect_existing_translations([locale_dir], lang_code)
        lang_missing = [msg for msg in source_strings if msg not in existing]
        if lang_missing:
            missing[lang_code] = lang_missing

    return missing


def write_translations(
    project_root: Path,
    translations: dict[str, dict[str, str]],
    source_strings: dict[str, str],
) -> dict[str, int]:
    """Write translated strings to .po files. Returns ``{lang: count}``."""
    locale_dir = _find_locale_dir(project_root)
    stats: dict[str, int] = {}

    all_langs: set[str] = set()
    for langs in translations.values():
        all_langs.update(langs.keys())

    for lang_code in sorted(all_langs):
        path = _po_path(locale_dir, lang_code)
        po = ensure_po(path, lang_code)
        existing_ids = {e.msgid for e in po}
        added = 0

        for msgid, lang_map in translations.items():
            translated = lang_map.get(lang_code)
            if not translated:
                continue

            plural_parts = decode_plural(msgid)
            if plural_parts:
                singular, plural_str = plural_parts
                # Check if already exists
                already = False
                for entry in po:
                    if entry.msgid == singular and entry.msgid_plural == plural_str:
                        already = True
                        if not any(v.strip() for v in (entry.msgstr_plural or {}).values()):
                            # Fill empty plural forms
                            nplurals = get_nplurals(lang_code)
                            if isinstance(translated, str):
                                try:
                                    import json
                                    forms = json.loads(translated)
                                    if isinstance(forms, list):
                                        for i, form in enumerate(forms[:nplurals]):
                                            entry.msgstr_plural[i] = form
                                    elif isinstance(forms, dict):
                                        entry.msgstr_plural[0] = forms.get("one", forms.get("singular", translated))
                                        entry.msgstr_plural[1] = forms.get("other", forms.get("plural", translated))
                                except (json.JSONDecodeError, TypeError):
                                    entry.msgstr_plural[0] = translated
                                    entry.msgstr_plural[1] = translated
                            added += 1
                        break
                if not already:
                    nplurals = get_nplurals(lang_code)
                    entry = polib.POEntry(msgid=singular, msgid_plural=plural_str, msgstr_plural={})
                    if isinstance(translated, str):
                        try:
                            import json
                            forms = json.loads(translated)
                            if isinstance(forms, list):
                                for i, form in enumerate(forms[:nplurals]):
                                    entry.msgstr_plural[i] = form
                            elif isinstance(forms, dict):
                                entry.msgstr_plural[0] = forms.get("one", forms.get("singular", translated))
                                entry.msgstr_plural[1] = forms.get("other", forms.get("plural", translated))
                        except (json.JSONDecodeError, TypeError):
                            entry.msgstr_plural[0] = translated
                            entry.msgstr_plural[1] = translated
                    po.append(entry)
                    added += 1
            else:
                if msgid in existing_ids:
                    for entry in po:
                        if entry.msgid == msgid and not entry.msgstr.strip():
                            entry.msgstr = translated
                            added += 1
                            break
                else:
                    entry = polib.POEntry(msgid=msgid, msgstr=translated)
                    po.append(entry)
                    added += 1

        if added:
            atomic_save_po(po, path)
        stats[lang_code] = added

    return stats


def compile_messages(project_root: Path) -> bool:
    """Compile .po to .mo using msgfmt."""
    locale_dir = _find_locale_dir(project_root)
    return compile_po_files(locale_dir)
