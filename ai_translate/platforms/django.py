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
    "venv", ".venv", "env", "node_modules", ".git",
    "__pycache__", "build", "dist", ".tox",
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


def detect_target_languages(project_root: Path) -> dict[str, str]:
    """Detect languages from the chosen locale directory or settings.py."""
    from ai_translate.cli.ui import COMMON_LANGUAGES

    locale_dir = _find_locale_dir(project_root)
    langs: dict[str, str] = {}

    if locale_dir.is_dir():
        for child in sorted(locale_dir.iterdir()):
            if child.is_dir() and child.name not in ("en", "en_US", "__pycache__"):
                code = child.name
                if code not in langs:
                    langs[code] = COMMON_LANGUAGES.get(code, code)

    # Try settings.py LANGUAGES
    if not langs:
        for dirpath, _, filenames in walk_project(project_root):
            if "settings.py" in filenames:
                try:
                    content = (dirpath / "settings.py").read_text(errors="ignore")
                    # Simple extraction of LANGUAGES tuples
                    import re
                    for m in re.finditer(r"\(\s*['\"]([a-z]{2}(?:-[a-zA-Z]+)?)['\"]", content):
                        code = m.group(1)
                        if code not in ("en", "en-us"):
                            langs[code] = COMMON_LANGUAGES.get(code, code)
                except OSError:
                    pass
                break

    return langs


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
        except Exception:
            pass
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
