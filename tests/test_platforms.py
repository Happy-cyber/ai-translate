"""Tests for platform detection and platform-specific handlers."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import polib
import pytest

from ai_translate.platforms import detect_platform
from ai_translate.platforms.django import (
    detect_target_languages as django_detect_languages,
    get_missing_translations as django_missing,
    scan_source as django_scan,
    write_translations as django_write,
)
from ai_translate.platforms.flutter import (
    detect_target_languages as flutter_detect_languages,
    scan_source as flutter_scan,
    write_translations as flutter_write,
)
from ai_translate.platforms.android import (
    scan_source as android_scan,
    write_translations as android_write,
)


# ======================================================================
# Platform detection
# ======================================================================


class TestDetectPlatform:
    """Tests for the top-level detect_platform function."""

    def test_detect_django(self, tmp_django_project: Path) -> None:
        assert detect_platform(tmp_django_project) == "django"

    def test_detect_flutter(self, tmp_flutter_project: Path) -> None:
        assert detect_platform(tmp_flutter_project) == "flutter"

    def test_detect_android(self, tmp_android_project: Path) -> None:
        assert detect_platform(tmp_android_project) == "android"

    def test_detect_ios(self, tmp_ios_project: Path) -> None:
        assert detect_platform(tmp_ios_project) == "ios"

    def test_detect_flask(self, tmp_flask_project: Path) -> None:
        assert detect_platform(tmp_flask_project) == "flask"

    def test_detect_fastapi(self, tmp_fastapi_project: Path) -> None:
        assert detect_platform(tmp_fastapi_project) == "fastapi"

    def test_detect_unknown_returns_none(self, tmp_path: Path) -> None:
        # An empty directory should not match any platform
        assert detect_platform(tmp_path) is None

    def test_detect_priority_django_over_flask(self, tmp_path: Path) -> None:
        """A project with both manage.py and a Flask app.py should be Django."""
        (tmp_path / "manage.py").write_text(
            "#!/usr/bin/env python\nimport sys\n", encoding="utf-8"
        )
        (tmp_path / "app.py").write_text(
            "from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8"
        )
        assert detect_platform(tmp_path) == "django"


# ======================================================================
# Django handler
# ======================================================================


class TestDjangoHandler:
    """Tests for the Django platform handler."""

    def test_django_scan_source(self, tmp_django_project: Path) -> None:
        strings = django_scan(tmp_django_project)
        assert isinstance(strings, dict)
        assert len(strings) >= 3
        values = set(strings.values())
        assert "Welcome to our site" in values
        assert "Please log in" in values
        assert "Thank you for visiting" in values

    def test_django_detect_languages(self, tmp_django_project: Path) -> None:
        langs = django_detect_languages(tmp_django_project)
        assert isinstance(langs, dict)
        assert "es" in langs
        assert "fr" in langs
        # English should not appear
        assert "en" not in langs

    def test_django_missing_translations(self, tmp_django_project: Path) -> None:
        strings = django_scan(tmp_django_project)
        langs = django_detect_languages(tmp_django_project)
        missing = django_missing(tmp_django_project, strings, langs)
        assert isinstance(missing, dict)
        # All strings should be missing initially (no PO files written yet)
        for lang_code in langs:
            assert lang_code in missing
            assert len(missing[lang_code]) == len(strings)

    def test_django_write_translations(self, tmp_django_project: Path) -> None:
        translations = {
            "Welcome to our site": {"es": "Bienvenido a nuestro sitio", "fr": "Bienvenue sur notre site"},
            "Please log in": {"es": "Por favor inicia sesion", "fr": "Veuillez vous connecter"},
        }
        source_strings = {
            "Welcome to our site": "Welcome to our site",
            "Please log in": "Please log in",
        }

        stats = django_write(tmp_django_project, translations, source_strings)
        assert isinstance(stats, dict)
        assert "es" in stats
        assert "fr" in stats
        assert stats["es"] >= 2
        assert stats["fr"] >= 2

        # Verify the PO file was actually written and is parseable
        po_path = tmp_django_project / "locale" / "es" / "LC_MESSAGES" / "django.po"
        assert po_path.is_file()
        po = polib.pofile(str(po_path))
        msgids = {entry.msgid for entry in po}
        assert "Welcome to our site" in msgids
        msgstrs = {entry.msgstr for entry in po if entry.msgid == "Welcome to our site"}
        assert "Bienvenido a nuestro sitio" in msgstrs


# ======================================================================
# Flutter handler
# ======================================================================


class TestFlutterHandler:
    """Tests for the Flutter platform handler."""

    def test_flutter_scan_source(self, tmp_flutter_project: Path) -> None:
        strings = flutter_scan(tmp_flutter_project)
        assert isinstance(strings, dict)
        assert len(strings) >= 3
        assert "hello" in strings
        assert strings["hello"] == "Hello"
        assert "welcome" in strings
        assert "logout" in strings

    def test_flutter_detect_languages(self, tmp_flutter_project: Path) -> None:
        langs = flutter_detect_languages(tmp_flutter_project)
        assert isinstance(langs, dict)
        assert "es" in langs
        # English source should not appear as a target
        assert "en" not in langs

    def test_flutter_write_translations(self, tmp_flutter_project: Path) -> None:
        translations = {
            "hello": {"es": "Hola"},
            "welcome": {"es": "Bienvenido a la app"},
            "logout": {"es": "Cerrar sesion"},
        }
        source_strings = {
            "hello": "Hello",
            "welcome": "Welcome to the app",
            "logout": "Log out",
        }

        stats = flutter_write(tmp_flutter_project, translations, source_strings)
        assert isinstance(stats, dict)
        assert "es" in stats
        assert stats["es"] >= 3

        # Verify the ARB file is valid JSON
        arb_path = tmp_flutter_project / "lib" / "l10n" / "app_es.arb"
        assert arb_path.is_file()
        data = json.loads(arb_path.read_text(encoding="utf-8"))
        assert data.get("@@locale") == "es"
        assert data["hello"] == "Hola"
        assert data["welcome"] == "Bienvenido a la app"
        assert data["logout"] == "Cerrar sesion"


# ======================================================================
# Android handler
# ======================================================================


class TestAndroidHandler:
    """Tests for the Android platform handler."""

    def test_android_scan_source(self, tmp_android_project: Path) -> None:
        strings = android_scan(tmp_android_project)
        assert isinstance(strings, dict)
        assert len(strings) >= 3
        assert "app_name" in strings
        assert strings["app_name"] == "Test App"
        assert "hello" in strings
        assert "welcome" in strings

    def test_android_write_translations(self, tmp_android_project: Path) -> None:
        translations = {
            "app_name": {"es": "App de Prueba"},
            "hello": {"es": "Hola Mundo"},
            "welcome": {"es": "Bienvenido"},
        }
        source_strings = {
            "app_name": "Test App",
            "hello": "Hello World",
            "welcome": "Welcome",
        }

        stats = android_write(tmp_android_project, translations, source_strings)
        assert isinstance(stats, dict)
        assert "es" in stats
        assert stats["es"] >= 3

        # Verify the strings.xml was written and is valid XML
        xml_path = (
            tmp_android_project
            / "app" / "src" / "main" / "res" / "values-es" / "strings.xml"
        )
        assert xml_path.is_file()
        tree = ET.parse(xml_path)
        root = tree.getroot()
        assert root.tag == "resources"
        names = {elem.get("name") for elem in root.iter("string")}
        assert "app_name" in names
        assert "hello" in names
