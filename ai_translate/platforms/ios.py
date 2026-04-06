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


_chosen_ios_format: tuple[str, Path | None] | None = None


def _detect_format(project_root: Path) -> tuple[str, Path | None]:
    """Return ("xcstrings", path) or ("strings", lproj_parent) or ("unknown", None).

    If multiple .xcstrings files or .lproj locations exist, prompts user.
    """
    global _chosen_ios_format

    if _chosen_ios_format is not None:
        return _chosen_ios_format

    # Collect all .xcstrings files
    xcstrings_files: list[Path] = []
    for dirpath, _, filenames in walk_project(project_root, extra_skip=_SWIFT_SKIP_DIRS):
        for f in filenames:
            if f.endswith(".xcstrings"):
                xcstrings_files.append(dirpath / f)

    if xcstrings_files:
        if len(xcstrings_files) == 1:
            chosen = xcstrings_files[0]
        else:
            from ai_translate.cli.ui import prompt_choose_path
            def _detail(p: Path) -> str:
                try:
                    import json
                    data = json.loads(p.read_text())
                    count = len(data.get("strings", {}))
                    return f"{count} string keys"
                except Exception:
                    return ""
            chosen = prompt_choose_path(
                ".xcstrings file", xcstrings_files, detail_fn=_detail,
                pref_key="ios_xcstrings_file", project_root=project_root,
            )
        _chosen_ios_format = ("xcstrings", chosen)
        return _chosen_ios_format

    # Collect all .lproj parent dirs
    lproj_parents: list[Path] = []
    for dirpath, dirs, _ in walk_project(project_root, extra_skip=_SWIFT_SKIP_DIRS):
        for d in dirs:
            if d.endswith(".lproj") and dirpath not in lproj_parents:
                lproj_parents.append(dirpath)

    if lproj_parents:
        # Filter to only dirs with actual .strings files inside .lproj subdirs
        with_strings = []
        for parent in lproj_parents:
            strings_count = 0
            for lproj_dir in parent.iterdir():
                if lproj_dir.is_dir() and lproj_dir.name.endswith(".lproj"):
                    strings_count += sum(1 for _ in lproj_dir.glob("*.strings"))
            if strings_count > 0:
                with_strings.append((parent, strings_count))

        if with_strings:
            # Sort by .strings file count (most files = best candidate)
            with_strings.sort(key=lambda x: x[1], reverse=True)
            candidates = [p for p, _ in with_strings]
        else:
            candidates = lproj_parents

        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            from ai_translate.cli.ui import prompt_choose_path
            def _detail_lproj(p: Path) -> str:
                count = sum(1 for d in p.iterdir() if d.name.endswith(".lproj"))
                strings = sum(
                    sum(1 for _ in d.glob("*.strings"))
                    for d in p.iterdir() if d.is_dir() and d.name.endswith(".lproj")
                )
                return f"{count} .lproj dirs, {strings} .strings files"
            chosen = prompt_choose_path(
                "iOS localization directory", candidates, detail_fn=_detail_lproj,
                pref_key="ios_lproj_dir", project_root=project_root,
            )
        _chosen_ios_format = ("strings", chosen)
        return _chosen_ios_format

    _chosen_ios_format = ("unknown", None)
    return _chosen_ios_format


# ── .strings parsing ──────────────────────────────────────────────────


def _parse_strings_file(path: Path) -> dict[str, str]:
    """Parse a .strings file, handling UTF-8, UTF-16LE, and UTF-16BE encodings.

    Apple .strings files can be in any of these encodings.
    We try UTF-8 first, then fall back to UTF-16.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return {}

    # Detect encoding from BOM or try multiple
    content = ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        # Has BOM — UTF-16
        try:
            content = raw.decode("utf-16")
        except (UnicodeDecodeError, ValueError):
            content = raw.decode("utf-8", errors="ignore")
    else:
        # Try UTF-8 first, then UTF-16LE (common Apple default without BOM)
        try:
            content = raw.decode("utf-8")
            # Verify it actually parsed (UTF-16 read as UTF-8 produces garbage)
            if content and _STRINGS_RE.search(content):
                pass  # UTF-8 worked
            else:
                # Try UTF-16LE
                try:
                    alt = raw.decode("utf-16-le")
                    if _STRINGS_RE.search(alt):
                        content = alt
                except (UnicodeDecodeError, ValueError):
                    pass
        except UnicodeDecodeError:
            try:
                content = raw.decode("utf-16")
            except (UnicodeDecodeError, ValueError):
                content = raw.decode("latin-1")

    return dict(_STRINGS_RE.findall(content))


def _escape_strings_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")


def _write_strings_file(path: Path, entries: dict[str, str]) -> None:
    lines = [f'"{k}" = "{_escape_strings_value(v)}";' for k, v in sorted(entries.items())]

    def _validate_strings(tmp: Path) -> None:
        content = tmp.read_text(encoding="utf-8")
        found = _STRINGS_RE.findall(content)
        if len(entries) > 0 and len(found) == 0:
            raise ValueError(f"Invalid .strings: wrote {len(entries)} entries but none parse back")

    atomic_write_text(path, "\n".join(lines) + "\n", validate=_validate_strings)


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
    """Detect languages — xcodeproj/xcstrings config first, then .lproj dirs.

    Priority:
      1. .xcstrings JSON localizations — embedded config
      2. .xcodeproj knownRegions — Xcode project config
      3. .lproj dirs with actual .strings files — real translations
      4. .lproj empty dirs — scaffolded but empty
    """
    from ai_translate.cli.ui import COMMON_LANGUAGES

    fmt, path = _detect_format(project_root)
    langs: dict[str, str] = {}

    # ── Priority 1: xcstrings JSON localizations ─────────────────────
    if fmt == "xcstrings" and path:
        data = _parse_xcstrings(path)
        source = data.get("sourceLanguage", "en")
        for key, info in data.get("strings", {}).items():
            for lc in info.get("localizations", {}):
                if lc != source:
                    langs[lc] = COMMON_LANGUAGES.get(lc, lc)
            break  # Only need first entry to get all locale keys
        if langs:
            return langs

    # ── Priority 2: Xcode project knownRegions ───────────────────────
    xcode_langs: dict[str, str] = {}
    for code in _detect_languages_from_xcodeproj(project_root):
        xcode_langs[code] = COMMON_LANGUAGES.get(code, code)
    if xcode_langs:
        return xcode_langs

    # ── Priority 3 & 4: .lproj directories ───────────────────────────
    if fmt == "strings" and path:
        dirs_with_strings: dict[str, str] = {}
        dirs_empty: dict[str, str] = {}
        for child in sorted(path.iterdir()):
            if child.is_dir() and child.name.endswith(".lproj"):
                code = child.name.replace(".lproj", "")
                if code not in ("en", "Base"):
                    name = COMMON_LANGUAGES.get(code, code)
                    if any(child.glob("*.strings")):
                        dirs_with_strings[code] = name
                    else:
                        dirs_empty[code] = name
        if dirs_with_strings:
            return dirs_with_strings

    return langs


def scaffold_languages(
    project_root: Path, languages: dict[str, str],
) -> list[str]:
    """Create .lproj directories for each language (strings format).

    For xcstrings format, no scaffolding needed — languages are added
    inside the JSON file when translations are written.
    """
    fmt, path = _detect_format(project_root)
    scaffolded: list[str] = []

    if fmt == "xcstrings" and path:
        # xcstrings: add empty localizations for each language in the JSON
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            strings_data = data.get("strings", {})
            changed = False
            for code in languages:
                # Add locale to at least one key so it's detected next time
                for key, info in strings_data.items():
                    locs = info.setdefault("localizations", {})
                    if code not in locs:
                        locs[code] = {"stringUnit": {"state": "new", "value": ""}}
                        changed = True
                    break  # Only need first key
                scaffolded.append(code)
            if changed:
                atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not scaffold xcstrings languages: %s", exc)

    elif fmt == "strings" and path:
        # .strings format: create <lang>.lproj/Localizable.strings
        for code in languages:
            lproj = path / f"{code}.lproj"
            strings_file = lproj / "Localizable.strings"
            if not strings_file.is_file():
                lproj.mkdir(parents=True, exist_ok=True)
                atomic_write_text(strings_file, "/* Auto-generated by ai-translate */\n")
                scaffolded.append(code)

    else:
        # Unknown format — create .lproj/Localizable.strings at project root
        for code in languages:
            lproj = project_root / f"{code}.lproj"
            strings_file = lproj / "Localizable.strings"
            if not strings_file.is_file():
                lproj.mkdir(parents=True, exist_ok=True)
                atomic_write_text(strings_file, "/* Auto-generated by ai-translate */\n")
                scaffolded.append(code)

    return scaffolded


def update_language_config(
    project_root: Path, languages: dict[str, str],
) -> list[str]:
    """Add new languages to knownRegions in .xcodeproj/project.pbxproj.

    Preserves existing regions — only adds codes not already present.
    Returns list of language codes added.
    """
    # Find project.pbxproj
    pbxproj: Path | None = None
    for child in project_root.iterdir():
        if child.name.endswith(".xcodeproj") and child.is_dir():
            candidate = child / "project.pbxproj"
            if candidate.is_file():
                pbxproj = candidate
                break

    if not pbxproj:
        return []

    try:
        content = pbxproj.read_text(errors="ignore")
    except OSError:
        return []

    # Parse existing knownRegions
    existing_codes: set[str] = {"en", "Base"}
    kr_match = re.search(
        r"(knownRegions\s*=\s*\()(.+?)(\))",
        content, re.DOTALL,
    )
    if kr_match:
        for m in re.finditer(r"\b([a-zA-Z]{2}(?:-[a-zA-Z]+)?)\b", kr_match.group(2)):
            existing_codes.add(m.group(1))

    new_codes = [code for code in languages if code not in existing_codes]
    if not new_codes:
        return []

    if kr_match:
        # Append new regions before closing )
        block = kr_match.group(2).rstrip()
        # Detect indentation style
        indent = "\t\t\t\t"
        lines = block.strip().split("\n")
        if lines:
            m = re.match(r"(\s+)", lines[0])
            if m:
                indent = m.group(1)
        new_entries = "".join(f"{indent}{code},\n" for code in new_codes)
        # Insert before closing )
        updated = content[:kr_match.end(2)] + new_entries + content[kr_match.end(2):]
    else:
        return []  # No knownRegions found — don't modify pbxproj blindly

    try:
        pbxproj.write_text(updated, encoding="utf-8")
    except OSError:
        return []

    return new_codes


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

        def _validate_xcstrings(tmp: Path) -> None:
            d = json.loads(tmp.read_text(encoding="utf-8"))
            if "strings" not in d:
                raise ValueError("Invalid .xcstrings: missing 'strings' key")

        atomic_write_text(path, content, validate=_validate_xcstrings)

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
