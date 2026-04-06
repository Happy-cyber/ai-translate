"""Flask / FastAPI platform handler — Babel-style PO file management.

Flask and FastAPI both use Babel/gettext for i18n with .po files.
Detection differs, but the translation pipeline is identical.
"""

from __future__ import annotations

import ast
import logging
import re
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
    "_", "gettext", "ngettext", "lazy_gettext", "N_",
    "ngettext", "ngettext_lazy", "ungettext", "ungettext_lazy",
})

_SKIP_CALLS = frozenset({
    "logger", "logging", "print", "log",
    "warning", "error", "info", "debug", "critical", "exception",
})

_EXTRA_SKIP_DIRS = frozenset({"static", "templates", "instance"})


# ── String extraction (uses shared utilities) ─────────────────────────


def scan_source(project_root: Path) -> dict[str, str]:
    """AST-parse .py files for translatable strings."""
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
                if name is None or name in _SKIP_CALLS or name not in _TRANSLATABLE_CALLS:
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

    # Also scan Jinja2 templates for _("...")
    for dirpath, _, filenames in walk_project(project_root, extra_skip=SKIP_DIRS):
        for fname in filenames:
            if not fname.endswith((".html", ".jinja2", ".j2", ".jinja", ".txt")):
                continue
            fpath = dirpath / fname
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'_\(\s*["\'](.+?)["\']', content, re.DOTALL):
                    msg = m.group(1).strip()
                    if msg and not should_skip_message(msg):
                        messages[msg] = msg
            except OSError:
                pass

    return messages


# ── Language detection ────────────────────────────────────────────────


_chosen_trans_dir: Path | None = None


def _find_translations_dir(project_root: Path) -> Path:
    """Find translations directory, prompting user if multiple exist."""
    global _chosen_trans_dir

    if _chosen_trans_dir and _chosen_trans_dir.is_dir():
        return _chosen_trans_dir

    found: list[Path] = []

    # Check root-level and one level deep
    for name in ("translations", "locale", "locales"):
        candidate = project_root / name
        if candidate.is_dir():
            found.append(candidate)
        # Also check subdirectories (Flask app packages)
        for child in project_root.iterdir() if project_root.is_dir() else []:
            if child.is_dir() and child.name not in (
                "venv", ".venv", "env", "node_modules", ".git",
                "__pycache__", "static", "templates", "instance",
            ):
                sub = child / name
                if sub.is_dir() and sub not in found:
                    found.append(sub)

    if not found:
        translations = project_root / "translations"
        translations.mkdir(parents=True, exist_ok=True)
        _chosen_trans_dir = translations
        return translations

    # Filter to dirs that have .po files
    with_po = [d for d in found if any(d.rglob("*.po"))]

    if len(with_po) == 1:
        _chosen_trans_dir = with_po[0]
    elif len(with_po) > 1:
        from ai_translate.cli.ui import prompt_choose_path
        def _detail(p: Path) -> str:
            count = sum(1 for _ in p.rglob("*.po"))
            return f"{count} .po files"
        _chosen_trans_dir = prompt_choose_path(
            "translations directory", with_po, detail_fn=_detail,
            pref_key="flask_translations_dir", project_root=project_root,
        )
    else:
        _chosen_trans_dir = found[0]

    return _chosen_trans_dir


def detect_target_languages(project_root: Path) -> dict[str, str]:
    """Detect languages — config files first, then translations/ directory.

    Priority:
      1. LANGUAGES/SUPPORTED_LANGUAGES in config files — user's explicit config
      2. translations/ dirs with actual .po files — real translation work
      3. translations/ dirs with LC_MESSAGES/ but no .po — scaffolded but empty
    """
    from ai_translate.cli.ui import COMMON_LANGUAGES
    import re

    # ── Priority 1: Check config files for LANGUAGES setting ─────────
    langs: dict[str, str] = {}
    _CONFIG_NAMES = frozenset({
        "config.py", "settings.py", "base.py", "app.py",
        "__init__.py", "common.py", "defaults.py",
    })
    for dirpath, _, filenames in walk_project(project_root):
        for fname in filenames:
            if fname not in _CONFIG_NAMES:
                continue
            try:
                content = (dirpath / fname).read_text(errors="ignore")
                lang_block = re.search(
                    r"(?:LANGUAGES|SUPPORTED_LANGUAGES|BABEL_LANGUAGES)\s*=\s*\[(.+?)\]",
                    content, re.MULTILINE | re.DOTALL,
                )
                if not lang_block:
                    continue
                for m in re.finditer(r"['\"]([a-z]{2}(?:-[a-zA-Z]+)?)['\"]", lang_block.group(1)):
                    code = m.group(1)
                    if code not in ("en", "en-us"):
                        langs[code] = COMMON_LANGUAGES.get(code, code)
                if langs:
                    return langs
            except OSError:
                pass

    # ── Priority 2: Scan translations/ for dirs with actual .po files ─
    trans_dir = _find_translations_dir(project_root)
    dirs_with_po: dict[str, str] = {}
    dirs_with_lc: dict[str, str] = {}

    if trans_dir.is_dir():
        for child in sorted(trans_dir.iterdir()):
            if child.is_dir() and child.name not in ("en", "en_US"):
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
    """Create translations/<lang>/LC_MESSAGES/messages.po for each language."""
    trans_dir = _find_translations_dir(project_root)
    scaffolded: list[str] = []
    for code in languages:
        po_file = _po_path(trans_dir, code)
        if not po_file.is_file():
            po_file.parent.mkdir(parents=True, exist_ok=True)
            po = ensure_po(po_file, code)
            atomic_save_po(po, po_file)
            scaffolded.append(code)
    return scaffolded


def update_language_config(
    project_root: Path, languages: dict[str, str],
) -> list[str]:
    """Add new languages to LANGUAGES/SUPPORTED_LANGUAGES in Flask/FastAPI config.

    Preserves existing languages — only adds codes not already present.
    Returns list of language codes added to the config.
    """
    import re
    from ai_translate.cli.ui import COMMON_LANGUAGES

    _CONFIG_NAMES = frozenset({
        "config.py", "settings.py", "base.py", "app.py",
        "__init__.py", "common.py", "defaults.py",
    })

    target_file: Path | None = None
    existing_codes: set[str] = {"en", "en-us"}

    # Find config file with LANGUAGES variable
    for dirpath, _, filenames in walk_project(project_root):
        for fname in filenames:
            if fname not in _CONFIG_NAMES:
                continue
            fpath = dirpath / fname
            try:
                content = fpath.read_text(errors="ignore")
                lang_match = re.search(
                    r"(?:LANGUAGES|SUPPORTED_LANGUAGES|BABEL_LANGUAGES)\s*=\s*\[(.+?)\]",
                    content, re.MULTILINE | re.DOTALL,
                )
                if lang_match:
                    target_file = fpath
                    for m in re.finditer(r"['\"]([a-z]{2}(?:-[a-zA-Z]+)?)['\"]", lang_match.group(1)):
                        existing_codes.add(m.group(1))
                    break
            except OSError:
                continue
        if target_file:
            break

    # If no config file with LANGUAGES, try to find main config file
    if not target_file:
        for dirpath, _, filenames in walk_project(project_root):
            for fname in ("config.py", "settings.py", "app.py"):
                if fname in filenames:
                    target_file = dirpath / fname
                    break
            if target_file:
                break

    if not target_file:
        return []

    new_codes = [code for code in languages if code not in existing_codes]
    if not new_codes:
        return []

    try:
        content = target_file.read_text(errors="ignore")
    except OSError:
        return []

    lang_match = re.search(
        r"((?:LANGUAGES|SUPPORTED_LANGUAGES|BABEL_LANGUAGES)\s*=\s*\[)(.+?)(\])",
        content, re.MULTILINE | re.DOTALL,
    )

    new_entries = ", ".join(f"'{code}'" for code in new_codes)

    if lang_match:
        # Insert new codes before closing ]
        block = lang_match.group(2).rstrip()
        if block and not block.rstrip().endswith(","):
            block += ","
        updated = content[:lang_match.start(2)] + block + " " + new_entries + content[lang_match.end(2):]
    else:
        # Add new SUPPORTED_LANGUAGES variable
        all_codes = ["'en'"] + [f"'{code}'" for code in new_codes]
        languages_block = f"\nSUPPORTED_LANGUAGES = [{', '.join(all_codes)}]\n"
        updated = content.rstrip() + "\n" + languages_block

    try:
        target_file.write_text(updated, encoding="utf-8")
    except OSError:
        return []

    return new_codes


# ── PO file management ────────────────────────────────────────────────


def _po_path(trans_dir: Path, lang_code: str) -> Path:
    return trans_dir / lang_code / "LC_MESSAGES" / "messages.po"




def get_missing_translations(
    project_root: Path,
    source_strings: dict[str, str],
    target_languages: dict[str, str],
) -> dict[str, list[str]]:
    trans_dir = _find_translations_dir(project_root)
    missing: dict[str, list[str]] = {}

    for lang_code in target_languages:
        path = _po_path(trans_dir, lang_code)
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
            except Exception as exc:
                log.debug("Could not parse PO file %s: %s", path, exc)
        lang_missing = [msg for msg in source_strings if msg not in existing]
        if lang_missing:
            missing[lang_code] = lang_missing

    return missing


def write_translations(
    project_root: Path,
    translations: dict[str, dict[str, str]],
    source_strings: dict[str, str],
) -> dict[str, int]:
    trans_dir = _find_translations_dir(project_root)
    stats: dict[str, int] = {}

    all_langs: set[str] = set()
    for langs in translations.values():
        all_langs.update(langs.keys())

    for lang_code in sorted(all_langs):
        path = _po_path(trans_dir, lang_code)
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
    trans_dir = _find_translations_dir(project_root)
    return compile_po_files(trans_dir)
