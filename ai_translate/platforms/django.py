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


def _find_locale_dir(project_root: Path) -> Path:
    """Find or create the locale directory."""
    # Check common locations
    for candidate in (
        project_root / "locale",
        project_root / "conf" / "locale",
    ):
        if candidate.is_dir():
            return candidate

    # Walk to find any existing locale dir
    for dirpath, dirs, _ in walk_project(project_root):
        if "locale" in dirs:
            return dirpath / "locale"

    # Create default
    locale = project_root / "locale"
    locale.mkdir(parents=True, exist_ok=True)
    return locale


def detect_target_languages(project_root: Path) -> dict[str, str]:
    """Detect languages from existing locale/ subdirectories or settings.py."""
    from ai_translate.cli.ui import COMMON_LANGUAGES

    locale_dir = _find_locale_dir(project_root)
    langs: dict[str, str] = {}

    # From existing locale folders
    if locale_dir.is_dir():
        for child in sorted(locale_dir.iterdir()):
            if child.is_dir() and child.name not in ("en", "en_US"):
                code = child.name
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



def get_missing_translations(
    project_root: Path,
    source_strings: dict[str, str],
    target_languages: dict[str, str],
) -> dict[str, list[str]]:
    """Return ``{lang_code: [missing_msgids]}``."""
    locale_dir = _find_locale_dir(project_root)
    missing: dict[str, list[str]] = {}

    for lang_code in target_languages:
        path = _po_path(locale_dir, lang_code)
        existing: set[str] = set()
        if path.is_file():
            try:
                po = polib.pofile(str(path))
                for entry in po:
                    if entry.msgid_plural:
                        # Plural entry: check if msgstr_plural has forms
                        if entry.msgstr_plural and any(v.strip() for v in entry.msgstr_plural.values()):
                            existing.add(encode_plural(entry.msgid, entry.msgid_plural))
                    elif entry.msgstr and entry.msgstr.strip():
                        existing.add(entry.msgid)
            except Exception:
                pass
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
