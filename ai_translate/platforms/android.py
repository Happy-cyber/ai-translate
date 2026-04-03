"""Android platform handler — XML strings parsing + Kotlin/Java scanning."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ai_translate.platforms import SKIP_DIRS, atomic_write_text, walk_project

log = logging.getLogger(__name__)

_SOURCE_SKIP_DIRS = frozenset({
    "build", ".gradle", ".idea", "test", "androidTest",
    "debug", "release", "generated", "intermediates",
})

_ANDROID_PATTERNS = [
    re.compile(r'R\.string\.(\w+)'),
    re.compile(r'getString\(\s*R\.string\.(\w+)'),
    re.compile(r'\.getString\(\s*R\.string\.(\w+)'),
    re.compile(r'@string/(\w+)'),
]

_ARRAY_SEPARATOR = "\x00|||\x00"


# ── XML parsing ───────────────────────────────────────────────────────


def _get_element_text(elem: ET.Element) -> str:
    parts = [elem.text or ""]
    for child in elem:
        parts.append(ET.tostring(child, encoding="unicode", method="html"))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def _find_source_values_dir(project_root: Path) -> Path | None:
    """Find the English values/ directory."""
    standard = project_root / "app" / "src" / "main" / "res" / "values"
    if standard.is_dir():
        return standard

    for dirpath, dirs, _ in walk_project(project_root, extra_skip=_SOURCE_SKIP_DIRS):
        if "values" in dirs:
            candidate = dirpath / "values"
            for f in candidate.iterdir():
                if f.suffix == ".xml":
                    return candidate

    return None


def _parse_string_xmls(values_dir: Path) -> dict[str, str]:
    """Parse all *.xml files in a values/ directory."""
    strings: dict[str, str] = {}

    for xml_file in sorted(values_dir.glob("*.xml")):
        try:
            tree = ET.parse(xml_file)
        except ET.ParseError:
            continue

        root = tree.getroot()
        for elem in root:
            if elem.tag == "string":
                name = elem.get("name", "")
                if elem.get("translatable") == "false":
                    continue
                text = _get_element_text(elem)
                if name and text:
                    strings[name] = text

            elif elem.tag == "string-array":
                name = elem.get("name", "")
                items = []
                for item in elem.findall("item"):
                    items.append(item.text or "")
                if name and items:
                    strings[f"__array__{name}"] = _ARRAY_SEPARATOR.join(items)

            elif elem.tag == "plurals":
                name = elem.get("name", "")
                quantities = {}
                for item in elem.findall("item"):
                    qty = item.get("quantity", "")
                    quantities[qty] = item.text or ""
                if name and quantities:
                    import json
                    strings[f"__plural__{name}"] = json.dumps(quantities, ensure_ascii=False)

    return strings


def _scan_android_sources(project_root: Path) -> dict[str, str]:
    """Scan Kotlin/Java/XML for R.string references."""
    keys: set[str] = set()
    skip = SKIP_DIRS | _SOURCE_SKIP_DIRS

    for dirpath, _, filenames in walk_project(project_root, extra_skip=skip):
        for fname in filenames:
            if not fname.endswith((".kt", ".java", ".xml")):
                continue
            fpath = dirpath / fname
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pat in _ANDROID_PATTERNS:
                for m in pat.finditer(content):
                    keys.add(m.group(1))

    return {k: k.replace("_", " ").capitalize() for k in keys if len(k) >= 2}


# ── Public interface ──────────────────────────────────────────────────


def scan_source(project_root: Path) -> dict[str, str]:
    values_dir = _find_source_values_dir(project_root)
    if values_dir:
        strings = _parse_string_xmls(values_dir)
        if strings:
            return strings

    return _scan_android_sources(project_root)


def detect_target_languages(project_root: Path) -> dict[str, str]:
    from ai_translate.cli.ui import COMMON_LANGUAGES

    langs: dict[str, str] = {}
    values_dir = _find_source_values_dir(project_root)

    if values_dir:
        res_dir = values_dir.parent
        for child in sorted(res_dir.iterdir()):
            if child.is_dir() and child.name.startswith("values-"):
                code = child.name.split("values-", 1)[1]
                if code and code not in ("en", "en-rUS"):
                    code_normalized = code.replace("-r", "-")
                    langs[code] = COMMON_LANGUAGES.get(code_normalized, code)

    # Fallback: check build.gradle resConfigs
    if not langs:
        for gradle_name in ("app/build.gradle", "app/build.gradle.kts"):
            gradle = project_root / gradle_name
            if gradle.is_file():
                content = gradle.read_text(errors="ignore")
                for m in re.finditer(r'resConfigs?\s+["\']([a-z]{2})["\']', content):
                    code = m.group(1)
                    if code != "en":
                        langs[code] = COMMON_LANGUAGES.get(code, code)
                break

    return langs


def get_missing_translations(
    project_root: Path,
    source_strings: dict[str, str],
    target_languages: dict[str, str],
) -> dict[str, list[str]]:
    values_dir = _find_source_values_dir(project_root)
    missing: dict[str, list[str]] = {}

    if not values_dir:
        return {lang: list(source_strings.keys()) for lang in target_languages}

    res_dir = values_dir.parent

    for lang_code in target_languages:
        lang_dir = res_dir / f"values-{lang_code}"
        existing_keys: set[str] = set()
        if lang_dir.is_dir():
            existing = _parse_string_xmls(lang_dir)
            existing_keys = {k for k, v in existing.items() if v.strip()}

        lang_missing = [k for k in source_strings if k not in existing_keys]
        if lang_missing:
            missing[lang_code] = lang_missing

    return missing


def write_translations(
    project_root: Path,
    translations: dict[str, dict[str, str]],
    source_strings: dict[str, str],
) -> dict[str, int]:
    values_dir = _find_source_values_dir(project_root)
    if not values_dir:
        values_dir = project_root / "app" / "src" / "main" / "res" / "values"
    res_dir = values_dir.parent

    stats: dict[str, int] = {}
    all_langs: set[str] = set()
    for lang_map in translations.values():
        all_langs.update(lang_map.keys())

    for lang_code in sorted(all_langs):
        lang_dir = res_dir / f"values-{lang_code}"
        lang_dir.mkdir(parents=True, exist_ok=True)
        target_path = lang_dir / "strings.xml"

        # Load existing
        existing: dict[str, str] = {}
        if target_path.is_file():
            existing = _parse_string_xmls(lang_dir)

        root = ET.Element("resources")
        added = 0

        for key, lang_map in translations.items():
            translated = lang_map.get(lang_code)
            if not translated:
                continue
            if key in existing and existing[key].strip():
                continue

            if key.startswith("__array__"):
                name = key[len("__array__"):]
                arr_elem = ET.SubElement(root, "string-array", name=name)
                for item_text in translated.split(_ARRAY_SEPARATOR):
                    item_elem = ET.SubElement(arr_elem, "item")
                    item_elem.text = item_text
            elif key.startswith("__plural__"):
                name = key[len("__plural__"):]
                import json
                try:
                    quantities = json.loads(translated)
                except (json.JSONDecodeError, TypeError):
                    quantities = {"other": translated}
                plural_elem = ET.SubElement(root, "plurals", name=name)
                for qty, text in quantities.items():
                    item = ET.SubElement(plural_elem, "item", quantity=qty)
                    item.text = text
            else:
                elem = ET.SubElement(root, "string", name=key)
                elem.text = translated

            added += 1

        # Merge with existing entries
        if target_path.is_file():
            try:
                existing_tree = ET.parse(target_path)
                existing_root = existing_tree.getroot()
                for new_elem in root:
                    existing_root.append(new_elem)
                root = existing_root
            except ET.ParseError:
                pass

        ET.indent(ET.ElementTree(root), space="    ")
        xml_str = ET.tostring(root, encoding="unicode", xml_declaration=False)
        content = '<?xml version="1.0" encoding="utf-8"?>\n' + xml_str + "\n"
        atomic_write_text(target_path, content)
        stats[lang_code] = added

    return stats


def compile_messages(project_root: Path) -> bool:
    """No compilation needed for this platform."""
    return True
