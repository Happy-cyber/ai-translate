"""Plural support tests — covers all 6 platforms."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import polib
import pytest


# ── Shared utilities ──────────────────────────────────────────────────


class TestPluralUtilities:
    """Tests for _shared.py plural functions."""

    def test_encode_decode_roundtrip(self):
        from ai_translate.platforms._shared import decode_plural, encode_plural

        encoded = encode_plural("%(count)d item", "%(count)d items")
        assert encoded.startswith("::plural::")
        result = decode_plural(encoded)
        assert result == ("%(count)d item", "%(count)d items")

    def test_decode_non_plural_returns_none(self):
        from ai_translate.platforms._shared import decode_plural

        assert decode_plural("Hello world") is None
        assert decode_plural("") is None

    def test_get_nplurals_common_languages(self):
        from ai_translate.platforms._shared import get_nplurals

        assert get_nplurals("en") == 2
        assert get_nplurals("fr") == 2
        assert get_nplurals("ja") == 1
        assert get_nplurals("ru") == 3
        assert get_nplurals("ar") == 6
        assert get_nplurals("pl") == 3
        assert get_nplurals("zh") == 1

    def test_get_nplurals_unknown_defaults_to_2(self):
        from ai_translate.platforms._shared import get_nplurals

        assert get_nplurals("xx-unknown") == 2

    def test_plural_forms_dict_has_common_langs(self):
        from ai_translate.platforms._shared import PLURAL_FORMS

        for code in ("en", "es", "fr", "de", "ja", "ko", "ru", "ar", "zh", "pt", "it"):
            assert code in PLURAL_FORMS, f"Missing plural form for {code}"

    def test_ensure_po_has_plural_forms_header(self):
        from ai_translate.platforms._shared import ensure_po

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.po"
            po = ensure_po(path, "ru")
            assert "Plural-Forms" in po.metadata
            assert "nplurals=3" in po.metadata["Plural-Forms"]

    def test_ensure_po_has_plural_forms_for_japanese(self):
        from ai_translate.platforms._shared import ensure_po

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.po"
            po = ensure_po(path, "ja")
            assert "nplurals=1" in po.metadata["Plural-Forms"]


# ── Django plural support ─────────────────────────────────────────────


class TestDjangoPlurals:
    """Tests for Django ngettext detection and PO plural writing."""

    def test_scan_detects_ngettext(self):
        from ai_translate.platforms import django as dj
        from ai_translate.platforms._shared import PLURAL_MARKER

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").touch()
            app = root / "myapp"
            app.mkdir()
            (app / "__init__.py").touch()
            (app / "views.py").write_text(
                'from django.utils.translation import ngettext\n'
                'msg = ngettext("%(count)d item", "%(count)d items", count)\n'
            )
            strings = dj.scan_source(root)
            plural_keys = [k for k in strings if k.startswith(PLURAL_MARKER)]
            assert len(plural_keys) == 1
            assert "%(count)d item" in plural_keys[0]
            assert "%(count)d items" in plural_keys[0]

    def test_scan_detects_both_singular_and_ngettext(self):
        from ai_translate.platforms import django as dj
        from ai_translate.platforms._shared import PLURAL_MARKER

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").touch()
            app = root / "myapp"
            app.mkdir()
            (app / "__init__.py").touch()
            (app / "views.py").write_text(
                'from django.utils.translation import gettext_lazy as _, ngettext\n'
                'title = _("Shopping Cart")\n'
                'msg = ngettext("%(count)d item", "%(count)d items", count)\n'
            )
            strings = dj.scan_source(root)
            singular_keys = [k for k in strings if not k.startswith(PLURAL_MARKER)]
            plural_keys = [k for k in strings if k.startswith(PLURAL_MARKER)]
            assert len(singular_keys) == 1
            assert "Shopping Cart" in singular_keys
            assert len(plural_keys) == 1

    def test_write_plural_creates_msgid_plural(self):
        from ai_translate.platforms import django as dj
        from ai_translate.platforms._shared import encode_plural

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").touch()
            (root / "locale" / "es" / "LC_MESSAGES").mkdir(parents=True)

            encoded_key = encode_plural("%(count)d item", "%(count)d items")
            translations = {
                encoded_key: {"es": '["%(count)d artículo", "%(count)d artículos"]'},
                "Submit": {"es": "Enviar"},
            }
            source = {encoded_key: encoded_key, "Submit": "Submit"}
            stats = dj.write_translations(root, translations, source)
            assert stats["es"] == 2

            po = polib.pofile(str(root / "locale" / "es" / "LC_MESSAGES" / "django.po"))

            # Find the plural entry
            plural_entry = None
            singular_entry = None
            for entry in po:
                if entry.msgid_plural:
                    plural_entry = entry
                elif entry.msgid == "Submit":
                    singular_entry = entry

            assert singular_entry is not None
            assert singular_entry.msgstr == "Enviar"

            assert plural_entry is not None
            assert plural_entry.msgid == "%(count)d item"
            assert plural_entry.msgid_plural == "%(count)d items"
            assert plural_entry.msgstr_plural[0] == "%(count)d artículo"
            assert plural_entry.msgstr_plural[1] == "%(count)d artículos"

    def test_write_plural_with_dict_format(self):
        from ai_translate.platforms import django as dj
        from ai_translate.platforms._shared import encode_plural

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").touch()
            (root / "locale" / "es" / "LC_MESSAGES").mkdir(parents=True)

            encoded_key = encode_plural("%(count)d day", "%(count)d days")
            translations = {
                encoded_key: {"es": '{"one": "%(count)d día", "other": "%(count)d días"}'},
            }
            source = {encoded_key: encoded_key}
            dj.write_translations(root, translations, source)

            po = polib.pofile(str(root / "locale" / "es" / "LC_MESSAGES" / "django.po"))
            plural_entry = [e for e in po if e.msgid_plural][0]
            assert plural_entry.msgstr_plural[0] == "%(count)d día"
            assert plural_entry.msgstr_plural[1] == "%(count)d días"

    def test_po_file_has_plural_forms_header(self):
        from ai_translate.platforms import django as dj
        from ai_translate.platforms._shared import encode_plural

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").touch()
            (root / "locale" / "ru" / "LC_MESSAGES").mkdir(parents=True)

            encoded_key = encode_plural("%(count)d item", "%(count)d items")
            translations = {
                encoded_key: {"ru": '["%(count)d предмет", "%(count)d предмета", "%(count)d предметов"]'},
            }
            source = {encoded_key: encoded_key}
            dj.write_translations(root, translations, source)

            po = polib.pofile(str(root / "locale" / "ru" / "LC_MESSAGES" / "django.po"))
            assert "Plural-Forms" in po.metadata
            assert "nplurals=3" in po.metadata["Plural-Forms"]

            # Russian has 3 forms
            plural_entry = [e for e in po if e.msgid_plural][0]
            assert plural_entry.msgstr_plural[0] == "%(count)d предмет"
            assert plural_entry.msgstr_plural[1] == "%(count)d предмета"
            assert plural_entry.msgstr_plural[2] == "%(count)d предметов"


# ── Flask/FastAPI plural support ──────────────────────────────────────


class TestFlaskPlurals:
    """Tests for Flask/FastAPI ngettext detection."""

    def test_scan_detects_ngettext(self):
        from ai_translate.platforms import flask_fastapi as ff
        from ai_translate.platforms._shared import PLURAL_MARKER

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "app.py").write_text(
                'from flask import Flask\n'
                'from flask_babel import ngettext\n'
                'msg = ngettext("%(num)d file", "%(num)d files", num)\n'
            )
            (root / "translations").mkdir()
            strings = ff.scan_source(root)
            plural_keys = [k for k in strings if k.startswith(PLURAL_MARKER)]
            assert len(plural_keys) == 1

    def test_write_plural_creates_msgid_plural(self):
        from ai_translate.platforms import flask_fastapi as ff
        from ai_translate.platforms._shared import encode_plural

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "translations" / "fr" / "LC_MESSAGES").mkdir(parents=True)

            encoded = encode_plural("%(num)d file", "%(num)d files")
            translations = {
                encoded: {"fr": '["%(num)d fichier", "%(num)d fichiers"]'},
            }
            source = {encoded: encoded}
            stats = ff.write_translations(root, translations, source)
            assert stats["fr"] == 1

            po = polib.pofile(str(root / "translations" / "fr" / "LC_MESSAGES" / "messages.po"))
            plural_entry = [e for e in po if e.msgid_plural][0]
            assert plural_entry.msgid == "%(num)d file"
            assert plural_entry.msgid_plural == "%(num)d files"
            assert plural_entry.msgstr_plural[0] == "%(num)d fichier"
            assert plural_entry.msgstr_plural[1] == "%(num)d fichiers"


# ── Flutter ICU plural support ────────────────────────────────────────


class TestFlutterPlurals:
    """Tests for Flutter ICU MessageFormat plural handling."""

    def test_icu_plural_scanned_as_string(self):
        from ai_translate.platforms import flutter as fl

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pubspec.yaml").write_text("name: test")
            l10n = root / "lib" / "l10n"
            l10n.mkdir(parents=True)
            (l10n / "app_en.arb").write_text(json.dumps({
                "@@locale": "en",
                "itemCount": "{count, plural, =0{No items} =1{1 item} other{{count} items}}",
                "@itemCount": {"placeholders": {"count": {"type": "int"}}},
                "hello": "Hello",
            }))

            strings = fl.scan_source(root)
            assert "itemCount" in strings
            assert "hello" in strings
            # ICU plural is stored as a plain string
            assert "{count, plural" in strings["itemCount"]

    def test_icu_plural_written_as_string(self):
        from ai_translate.platforms import flutter as fl

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pubspec.yaml").write_text("name: test")
            l10n = root / "lib" / "l10n"
            l10n.mkdir(parents=True)
            (l10n / "app_en.arb").write_text(json.dumps({
                "@@locale": "en",
                "itemCount": "{count, plural, =0{No items} =1{1 item} other{{count} items}}",
            }))
            (l10n / "app_es.arb").write_text(json.dumps({"@@locale": "es"}))

            translations = {
                "itemCount": {
                    "es": "{count, plural, =0{Sin artículos} =1{1 artículo} other{{count} artículos}}"
                }
            }
            source = {"itemCount": "{count, plural, =0{No items} =1{1 item} other{{count} items}}"}
            stats = fl.write_translations(root, translations, source)
            assert stats["es"] == 1

            result = json.loads((l10n / "app_es.arb").read_text())
            assert "{count, plural" in result["itemCount"]
            assert "artículo" in result["itemCount"]


# ── Android XML plural support ────────────────────────────────────────


class TestAndroidPlurals:
    """Tests for Android XML <plurals> handling."""

    def test_scan_detects_plurals(self):
        from ai_translate.platforms import android as an

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            res = root / "app" / "src" / "main" / "res"
            (res / "values").mkdir(parents=True)
            (root / "app" / "build.gradle").write_text("android {}")
            (res / "values" / "strings.xml").write_text(
                '<?xml version="1.0" encoding="utf-8"?>\n'
                "<resources>\n"
                '    <string name="hello">Hello</string>\n'
                '    <plurals name="item_count">\n'
                '        <item quantity="one">%d item</item>\n'
                '        <item quantity="other">%d items</item>\n'
                "    </plurals>\n"
                "</resources>"
            )
            strings = an.scan_source(root)
            assert "hello" in strings
            assert "__plural__item_count" in strings
            plural_data = json.loads(strings["__plural__item_count"])
            assert plural_data["one"] == "%d item"
            assert plural_data["other"] == "%d items"

    def test_write_plural_creates_xml_plurals(self):
        from ai_translate.platforms import android as an

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            res = root / "app" / "src" / "main" / "res"
            (res / "values").mkdir(parents=True)
            (res / "values-es").mkdir(parents=True)
            (root / "app" / "build.gradle").write_text("android {}")
            (res / "values" / "strings.xml").write_text(
                '<?xml version="1.0"?><resources>'
                '<plurals name="item_count">'
                '<item quantity="one">%d item</item>'
                '<item quantity="other">%d items</item>'
                '</plurals></resources>'
            )

            translations = {
                "__plural__item_count": {
                    "es": '{"one": "%d artículo", "other": "%d artículos"}'
                }
            }
            source = {"__plural__item_count": '{"one": "%d item", "other": "%d items"}'}
            stats = an.write_translations(root, translations, source)
            assert stats["es"] == 1

            # Parse the output XML
            import xml.etree.ElementTree as ET
            tree = ET.parse(res / "values-es" / "strings.xml")
            plurals = tree.getroot().findall("plurals")
            assert len(plurals) == 1
            assert plurals[0].get("name") == "item_count"
            items = plurals[0].findall("item")
            qty_map = {i.get("quantity"): i.text for i in items}
            assert qty_map["one"] == "%d artículo"
            assert qty_map["other"] == "%d artículos"


# ── iOS .xcstrings plural support ─────────────────────────────────────


class TestiOSPlurals:
    """Tests for iOS .xcstrings plural variant handling."""

    def test_scan_detects_xcstrings_plurals(self):
        from ai_translate.platforms import ios

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Test.xcodeproj").mkdir()
            (root / "Localizable.xcstrings").write_text(json.dumps({
                "sourceLanguage": "en",
                "strings": {
                    "hello": {
                        "localizations": {
                            "en": {"stringUnit": {"state": "translated", "value": "Hello"}}
                        }
                    },
                    "item_count": {
                        "localizations": {
                            "en": {
                                "variations": {
                                    "plural": {
                                        "one": {"stringUnit": {"state": "translated", "value": "%lld item"}},
                                        "other": {"stringUnit": {"state": "translated", "value": "%lld items"}}
                                    }
                                }
                            }
                        }
                    }
                }
            }))

            strings = ios.scan_source(root)
            assert "hello" in strings
            assert "__plural__item_count" in strings
            plural_data = json.loads(strings["__plural__item_count"])
            assert plural_data["one"] == "%lld item"
            assert plural_data["other"] == "%lld items"

    def test_write_xcstrings_plural_creates_variations(self):
        from ai_translate.platforms import ios

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Test.xcodeproj").mkdir()
            xcstrings_path = root / "Localizable.xcstrings"
            xcstrings_path.write_text(json.dumps({
                "sourceLanguage": "en",
                "strings": {
                    "item_count": {
                        "localizations": {
                            "en": {
                                "variations": {
                                    "plural": {
                                        "one": {"stringUnit": {"state": "translated", "value": "%lld item"}},
                                        "other": {"stringUnit": {"state": "translated", "value": "%lld items"}}
                                    }
                                }
                            }
                        }
                    }
                }
            }))

            translations = {
                "__plural__item_count": {
                    "es": '{"one": "%lld artículo", "other": "%lld artículos"}'
                }
            }
            source = {"__plural__item_count": '{"one": "%lld item", "other": "%lld items"}'}
            stats = ios.write_translations(root, translations, source)
            assert stats.get("es", 0) == 1

            data = json.loads(xcstrings_path.read_text())
            es_loc = data["strings"]["item_count"]["localizations"]["es"]
            plural = es_loc["variations"]["plural"]
            assert plural["one"]["stringUnit"]["value"] == "%lld artículo"
            assert plural["other"]["stringUnit"]["value"] == "%lld artículos"


# ── Prompt plural rules ──────────────────────────────────────────────


class TestPromptPluralRules:
    """Tests that build_prompt includes plural handling rules."""

    def test_django_prompt_mentions_ngettext(self):
        from ai_translate.services.translators.base import build_prompt

        prompt = build_prompt(["test"], {"es": "Spanish"}, platform="django")
        assert "plural" in prompt.lower()

    def test_android_prompt_mentions_plural_quantities(self):
        from ai_translate.services.translators.base import build_prompt

        prompt = build_prompt(["test"], {"es": "Spanish"}, platform="android")
        assert "plural" in prompt.lower()

    def test_ios_prompt_mentions_plural_cldr(self):
        from ai_translate.services.translators.base import build_prompt

        prompt = build_prompt(["test"], {"es": "Spanish"}, platform="ios")
        assert "plural" in prompt.lower()

    def test_flutter_prompt_mentions_icu_plural(self):
        from ai_translate.services.translators.base import build_prompt

        prompt = build_prompt(["test"], {"es": "Spanish"}, platform="flutter")
        assert "plural" in prompt.lower()

    def test_general_rules_mention_plural(self):
        from ai_translate.services.translators.base import build_prompt

        prompt = build_prompt(["test"], {"es": "Spanish"})
        assert "plural" in prompt.lower()
