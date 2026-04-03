"""iOS platform handler — .strings / .xcstrings parsing + Swift scanning."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ai_translate.platforms import SKIP_DIRS, atomic_write_text, walk_project

log = logging.getLogger(__name__)

_SWIFT_SKIP_DIRS = frozenset({
    "Pods", "Carthage", "DerivedData", ".build", "build",
    "Tests", "UITests", "UnitTests", "ThirdPartyLibrary",
})

_STRINGS_RE = re.compile(r'"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)"\s*;')

_SWIFT_PATTERNS = [
    re.compile(r'"([^"]{2,})"\s*\.localized'),
    re.compile(r'NSLocalizedString\(\s*"([^"]{2,})"'),
    re.compile(r'String\(\s*localized:\s*"([^"]{2,})"'),
    re.compile(r'LocalizedStringKey\(\s*"([^"]{2,})"'),
]

_JUNK = [
    re.compile(r'^https?://'),
    re.compile(r'^%[ds@]$'),
    re.compile(r'^[a-z][a-zA-Z]+\.[a-z]'),
]


# ── Format detection ──────────────────────────────────────────────────


def _detect_format(project_root: Path) -> tuple[str, Path | None]:
    """Return ("xcstrings", path) or ("strings", lproj_parent) or ("unknown", None)."""
    for dirpath, _, filenames in walk_project(project_root, extra_skip=_SWIFT_SKIP_DIRS):
        for f in filenames:
            if f.endswith(".xcstrings"):
                return "xcstrings", dirpath / f

    for dirpath, dirs, _ in walk_project(project_root, extra_skip=_SWIFT_SKIP_DIRS):
        for d in dirs:
            if d.endswith(".lproj"):
                return "strings", dirpath

    return "unknown", None


# ── .strings parsing ──────────────────────────────────────────────────


def _parse_strings_file(path: Path) -> dict[str, str]:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    return dict(_STRINGS_RE.findall(content))


def _escape_strings_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")


def _write_strings_file(path: Path, entries: dict[str, str]) -> None:
    lines = [f'"{k}" = "{_escape_strings_value(v)}";' for k, v in sorted(entries.items())]
    atomic_write_text(path, "\n".join(lines) + "\n")


# ── .xcstrings parsing ───────────────────────────────────────────────


def _parse_xcstrings(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# ── Swift source scanner ─────────────────────────────────────────────


def _scan_swift_sources(project_root: Path) -> dict[str, str]:
    results: dict[str, str] = {}
    skip = SKIP_DIRS | _SWIFT_SKIP_DIRS

    for dirpath, _, filenames in walk_project(project_root, extra_skip=skip):
        for fname in filenames:
            if not fname.endswith(".swift"):
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
                for pat in _SWIFT_PATTERNS:
                    for m in pat.finditer(line):
                        val = m.group(1)
                        if any(j.search(val) for j in _JUNK):
                            continue
                        if len(val) >= 2:
                            results[val] = val

    return results


# ── Language detection helpers ────────────────────────────────────────


def _detect_languages_from_xcodeproj(project_root: Path) -> list[str]:
    for child in project_root.iterdir():
        if child.name.endswith(".xcodeproj"):
            pbxproj = child / "project.pbxproj"
            if pbxproj.is_file():
                content = pbxproj.read_text(errors="ignore")
                m = re.search(r'knownRegions\s*=\s*\(([^)]+)\)', content)
                if m:
                    raw = m.group(1)
                    codes = re.findall(r'"?([a-zA-Z-]+)"?', raw)
                    return [c for c in codes if c not in ("en", "Base", "en-GB")]
    return []


# ── Public interface ──────────────────────────────────────────────────


def scan_source(project_root: Path) -> dict[str, str]:
    fmt, path = _detect_format(project_root)

    if fmt == "xcstrings" and path:
        data = _parse_xcstrings(path)
        strings_data = data.get("strings", {})
        result: dict[str, str] = {}
        source_lang = data.get("sourceLanguage", "en")
        for key, info in strings_data.items():
            locs = info.get("localizations", {})
            source_loc = locs.get(source_lang, {})
            # Check for plural variations first
            variations = source_loc.get("variations", {})
            plural_var = variations.get("plural", {})
            if plural_var:
                # Extract "other" form as the representative value
                other_unit = plural_var.get("other", {}).get("stringUnit", {})
                value = other_unit.get("value", key)
                if len(value) >= 2:
                    result[f"__plural__{key}"] = json.dumps(
                        {q: u.get("stringUnit", {}).get("value", "") for q, u in plural_var.items()},
                        ensure_ascii=False,
                    )
                continue
            # Regular string
            unit = source_loc.get("stringUnit", {})
            value = unit.get("value", key)
            if len(value) >= 2:
                result[key] = value
        if result:
            return result

    if fmt == "strings" and path:
        for lproj in ("en.lproj", "Base.lproj"):
            lproj_dir = path / lproj
            if lproj_dir.is_dir():
                merged: dict[str, str] = {}
                for sf in lproj_dir.glob("*.strings"):
                    merged.update(_parse_strings_file(sf))
                if merged:
                    return merged

    return _scan_swift_sources(project_root)


def detect_target_languages(project_root: Path) -> dict[str, str]:
    from ai_translate.cli.ui import COMMON_LANGUAGES

    fmt, path = _detect_format(project_root)
    langs: dict[str, str] = {}

    if fmt == "xcstrings" and path:
        data = _parse_xcstrings(path)
        source = data.get("sourceLanguage", "en")
        for key, info in data.get("strings", {}).items():
            for lc in info.get("localizations", {}):
                if lc != source:
                    langs[lc] = COMMON_LANGUAGES.get(lc, lc)
            break  # Only need first entry to get all locale keys

    if fmt == "strings" and path:
        for child in sorted(path.iterdir()):
            if child.is_dir() and child.name.endswith(".lproj"):
                code = child.name.replace(".lproj", "")
                if code not in ("en", "Base"):
                    langs[code] = COMMON_LANGUAGES.get(code, code)

    if not langs:
        for code in _detect_languages_from_xcodeproj(project_root):
            langs[code] = COMMON_LANGUAGES.get(code, code)

    return langs


def get_missing_translations(
    project_root: Path,
    source_strings: dict[str, str],
    target_languages: dict[str, str],
) -> dict[str, list[str]]:
    fmt, path = _detect_format(project_root)
    missing: dict[str, list[str]] = {}

    if fmt == "xcstrings" and path:
        data = _parse_xcstrings(path)
        strings_data = data.get("strings", {})
        for lang_code in target_languages:
            lang_missing = []
            for key in source_strings:
                real_key = key.removeprefix("__plural__")
                info = strings_data.get(real_key, {})
                locs = info.get("localizations", {})
                loc = locs.get(lang_code, {})
                # Check both regular string and plural variations
                variations = loc.get("variations", {})
                plural_var = variations.get("plural", {})
                if plural_var:
                    # Has plural — check if "other" form is filled
                    other_unit = plural_var.get("other", {}).get("stringUnit", {})
                    if not other_unit.get("value", "").strip():
                        lang_missing.append(key)
                else:
                    unit = loc.get("stringUnit", {})
                    if not unit.get("value", "").strip():
                        lang_missing.append(key)
            if lang_missing:
                missing[lang_code] = lang_missing

    elif fmt == "strings" and path:
        for lang_code in target_languages:
            lproj = path / f"{lang_code}.lproj"
            existing: dict[str, str] = {}
            if lproj.is_dir():
                for sf in lproj.glob("*.strings"):
                    existing.update(_parse_strings_file(sf))
            lang_missing = [k for k in source_strings if k not in existing or not existing[k].strip()]
            if lang_missing:
                missing[lang_code] = lang_missing

    else:
        for lang_code in target_languages:
            missing[lang_code] = list(source_strings.keys())

    return missing


def write_translations(
    project_root: Path,
    translations: dict[str, dict[str, str]],
    source_strings: dict[str, str],
) -> dict[str, int]:
    fmt, path = _detect_format(project_root)
    stats: dict[str, int] = {}

    if fmt == "xcstrings" and path:
        data = _parse_xcstrings(path)
        if "strings" not in data:
            data["strings"] = {}
        strings_data = data["strings"]

        for source_key, lang_map in translations.items():
            # Strip __plural__ prefix for the actual xcstrings key
            real_key = source_key.removeprefix("__plural__")
            if real_key not in strings_data:
                strings_data[real_key] = {"localizations": {}}
            locs = strings_data[real_key].setdefault("localizations", {})

            for lang_code, translated in lang_map.items():
                if not translated:
                    continue
                if source_key.startswith("__plural__"):
                    # Write plural variations
                    try:
                        quantities = json.loads(translated) if isinstance(translated, str) else translated
                    except (json.JSONDecodeError, TypeError):
                        quantities = {"other": translated}
                    if isinstance(quantities, dict):
                        plural_data = {}
                        for qty, text in quantities.items():
                            plural_data[qty] = {"stringUnit": {"state": "translated", "value": str(text)}}
                        locs[lang_code] = {"variations": {"plural": plural_data}}
                    else:
                        locs[lang_code] = {"stringUnit": {"state": "translated", "value": str(translated)}}
                else:
                    existing = locs.get(lang_code, {}).get("stringUnit", {}).get("value", "")
                    if existing and existing.strip():
                        continue
                    locs[lang_code] = {"stringUnit": {"state": "translated", "value": translated}}
                stats[lang_code] = stats.get(lang_code, 0) + 1

        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(path, content)

    else:
        # .strings format
        if not path:
            path = project_root
        all_langs: set[str] = set()
        for lang_map in translations.values():
            all_langs.update(lang_map.keys())

        for lang_code in sorted(all_langs):
            lproj = path / f"{lang_code}.lproj"
            lproj.mkdir(parents=True, exist_ok=True)
            strings_path = lproj / "Localizable.strings"

            existing: dict[str, str] = {}
            if strings_path.is_file():
                existing = _parse_strings_file(strings_path)

            added = 0
            for source_key, lang_map in translations.items():
                translated = lang_map.get(lang_code)
                if not translated:
                    continue
                if source_key in existing and existing[source_key].strip():
                    continue
                existing[source_key] = translated
                added += 1

            _write_strings_file(strings_path, existing)
            stats[lang_code] = added

    return stats


def compile_messages(project_root: Path) -> bool:
    """No compilation needed for this platform."""
    return True
