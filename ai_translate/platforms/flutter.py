"""Flutter platform handler — ARB file parsing + Dart source scanning."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ai_translate.platforms import SKIP_DIRS, atomic_write_text, walk_project

log = logging.getLogger(__name__)

_SOURCE_LOCALES = {"en", "en_US", "en_GB"}

_DART_SKIP_DIRS = frozenset({
    "build", ".dart_tool", ".fvm", ".pub-cache",
    "test", "integration_test", "generated", ".symlinks",
})

_DART_PATTERNS = [
    re.compile(r'AppLocalizations\.of\(\w+\)\.(\w+)'),
    re.compile(r'context\.l10n\.(\w+)'),
    re.compile(r'"([^"]{2,})"\s*\.tr'),
    re.compile(r"\btr\(\s*\"([^\"]{2,})\""),
    re.compile(r'S\.of\(\w+\)\.(\w+)'),
    re.compile(r'S\.current\.(\w+)'),
    re.compile(r'LocaleKeys\.(\w+)'),
]

_JUNK = [
    re.compile(r'^https?://'),
    re.compile(r'^%[ds@]$'),
    re.compile(r'^[a-z][a-zA-Z]+\.[a-z]'),
]


# ── ARB discovery ─────────────────────────────────────────────────────


def _discover_arb_files(project_root: Path) -> tuple[Path | None, Path | None]:
    """Find (arb_dir, template_arb) by walking the project.

    If multiple directories with .arb files exist, prompts the user to choose.
    """
    arb_dirs: list[Path] = []
    arb_dir_templates: dict[str, Path | None] = {}

    for dirpath, _, filenames in walk_project(project_root, extra_skip=_DART_SKIP_DIRS):
        arb_files = [f for f in filenames if f.endswith(".arb")]
        if not arb_files:
            continue

        arb_dirs.append(dirpath)
        template = None

        for fname in arb_files:
            fpath = dirpath / fname
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                if data.get("@@locale") in _SOURCE_LOCALES:
                    template = fpath
                    break
            except (json.JSONDecodeError, OSError):
                pass

        if not template:
            for fname in arb_files:
                if "_en." in fname or fname.startswith("app_en"):
                    template = dirpath / fname
                    break

        if not template and len(arb_files) == 1:
            template = dirpath / arb_files[0]

        arb_dir_templates[str(dirpath)] = template

    if not arb_dirs:
        return None, None

    if len(arb_dirs) == 1:
        chosen = arb_dirs[0]
    else:
        from ai_translate.cli.ui import prompt_choose_path
        def _detail(p: Path) -> str:
            count = sum(1 for f in p.iterdir() if f.suffix == ".arb")
            return f"{count} .arb files"
        chosen = prompt_choose_path(
            "ARB directory", arb_dirs, detail_fn=_detail,
            pref_key="flutter_arb_dir", project_root=project_root,
        )

    return chosen, arb_dir_templates.get(str(chosen))


def _get_config(project_root: Path) -> tuple[Path | None, Path | None]:
    """Read l10n.yaml or auto-discover ARBs."""
    l10n = project_root / "l10n.yaml"
    if l10n.is_file():
        try:
            import yaml
            cfg = yaml.safe_load(l10n.read_text()) or {}
            arb_dir = project_root / cfg.get("arb-dir", "lib/l10n")
            template = cfg.get("template-arb-file", "app_en.arb")
            return arb_dir, arb_dir / template
        except Exception:
            pass

    return _discover_arb_files(project_root)


# ── Dart source scanner ──────────────────────────────────────────────


def _scan_dart_sources(project_root: Path) -> dict[str, str]:
    """Fallback: scan .dart files for localizable patterns."""
    results: dict[str, str] = {}
    skip = SKIP_DIRS | _DART_SKIP_DIRS

    for dirpath, _, filenames in walk_project(project_root, extra_skip=skip):
        for fname in filenames:
            if not fname.endswith(".dart"):
                continue
            fpath = dirpath / fname
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("//") or "print(" in stripped or "debugPrint(" in stripped:
                    continue
                for pat in _DART_PATTERNS:
                    for match in pat.finditer(line):
                        val = match.group(1)
                        if any(j.search(val) for j in _JUNK):
                            continue
                        if len(val) >= 2:
                            results[val] = val

    return results


# ── Public interface ──────────────────────────────────────────────────


def scan_source(project_root: Path) -> dict[str, str]:
    arb_dir, template = _get_config(project_root)

    if template and template.is_file():
        try:
            data = json.loads(template.read_text(encoding="utf-8"))
            strings: dict[str, str] = {}
            for key, val in data.items():
                if key.startswith("@"):
                    continue
                if isinstance(val, str) and len(val) >= 2:
                    strings[key] = val
            if strings:
                return strings
        except (json.JSONDecodeError, OSError):
            pass

    return _scan_dart_sources(project_root)


def detect_target_languages(project_root: Path) -> dict[str, str]:
    from ai_translate.cli.ui import COMMON_LANGUAGES

    arb_dir, _ = _get_config(project_root)
    langs: dict[str, str] = {}

    if arb_dir and arb_dir.is_dir():
        for f in sorted(arb_dir.iterdir()):
            if not f.name.endswith(".arb"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                locale = data.get("@@locale", "")
            except (json.JSONDecodeError, OSError):
                locale = ""

            if not locale:
                m = re.search(r"_([a-z]{2}(?:_[A-Z]{2})?)\.arb$", f.name)
                if m:
                    locale = m.group(1)

            if locale and locale not in _SOURCE_LOCALES:
                langs[locale] = COMMON_LANGUAGES.get(locale, locale)

    return langs


def scaffold_languages(
    project_root: Path, languages: dict[str, str],
) -> list[str]:
    """Create empty ARB files (lib/l10n/app_<lang>.arb) for each language."""
    arb_dir, template = _get_config(project_root)
    if not arb_dir:
        arb_dir = project_root / "lib" / "l10n"
    arb_dir.mkdir(parents=True, exist_ok=True)

    # Detect template filename pattern (e.g. "app_en.arb" → prefix "app_")
    prefix = "app_"
    if template and template.is_file():
        m = re.match(r"^(.+?)(?:en|en_US|en_GB)\.arb$", template.name)
        if m:
            prefix = m.group(1)

    scaffolded: list[str] = []
    for code in languages:
        arb_file = arb_dir / f"{prefix}{code}.arb"
        if not arb_file.is_file():
            arb_file.write_text(
                json.dumps({"@@locale": code}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            scaffolded.append(code)
    return scaffolded


def update_language_config(
    project_root: Path, languages: dict[str, str],
) -> list[str]:
    """For Flutter, .arb files ARE the language config.

    scaffold_languages() already creates the .arb files.
    This function is a no-op to maintain cross-platform consistency.
    Returns empty list (config is the .arb files themselves).
    """
    return []


def get_missing_translations(
    project_root: Path,
    source_strings: dict[str, str],
    target_languages: dict[str, str],
) -> dict[str, list[str]]:
    arb_dir, _ = _get_config(project_root)
    missing: dict[str, list[str]] = {}

    for lang_code in target_languages:
        existing_keys: set[str] = set()
        if arb_dir:
            for f in arb_dir.iterdir():
                if not f.name.endswith(".arb"):
                    continue
                # Match locale from @@locale field first, then filename
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                file_locale = data.get("@@locale", "")
                if not file_locale:
                    # Fallback: extract from filename (app_es.arb, strings_pt_BR.arb, es.arb)
                    m = re.search(r"(?:^|_)([a-z]{2}(?:[_-][A-Z]{2})?)\.arb$", f.name)
                    file_locale = m.group(1) if m else ""
                if file_locale == lang_code:
                    for k, v in data.items():
                        if not k.startswith("@") and isinstance(v, str) and v.strip():
                            existing_keys.add(k)

        lang_missing = [k for k in source_strings if k not in existing_keys]
        if lang_missing:
            missing[lang_code] = lang_missing

    return missing


def write_translations(
    project_root: Path,
    translations: dict[str, dict[str, str]],
    source_strings: dict[str, str],
) -> dict[str, int]:
    arb_dir, template = _get_config(project_root)
    if not arb_dir:
        arb_dir = project_root / "lib" / "l10n"
    arb_dir.mkdir(parents=True, exist_ok=True)

    stats: dict[str, int] = {}
    all_langs: set[str] = set()
    for lang_map in translations.values():
        all_langs.update(lang_map.keys())

    for lang_code in sorted(all_langs):
        # Find or create target ARB file
        target_name = f"app_{lang_code}.arb"
        if template:
            # Extract prefix by removing the locale suffix (handles app_en, app_en_US, etc.)
            import re as _re
            stem = template.stem
            m = _re.match(r'^(.+?)_(?:en|en_US|en_GB|[a-z]{2}(?:_[A-Z]{2})?)$', stem)
            prefix = m.group(1) if m else stem.rsplit("_", 1)[0]
            target_name = f"{prefix}_{lang_code}.arb"

        target_path = arb_dir / target_name
        existing: dict[str, object] = {}
        if target_path.is_file():
            try:
                existing = json.loads(target_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        added = 0
        for source_key, lang_map in translations.items():
            translated = lang_map.get(lang_code)
            if not translated:
                continue
            if source_key in existing and existing[source_key]:
                continue
            existing[source_key] = translated
            added += 1

        existing["@@locale"] = lang_code

        content = json.dumps(existing, ensure_ascii=False, indent=2) + "\n"

        def _validate_arb(tmp: Path) -> None:
            data = json.loads(tmp.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "@@locale" not in data:
                raise ValueError(f"Invalid ARB: missing @@locale in {target_path.name}")

        atomic_write_text(target_path, content, validate=_validate_arb)
        stats[lang_code] = added

    return stats


def compile_messages(project_root: Path) -> bool:
    """No compilation needed for this platform."""
    return True
