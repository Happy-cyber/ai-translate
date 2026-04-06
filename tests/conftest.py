"""Shared pytest fixtures for ai-translate test suite."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Django
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_platform_state():
    """Reset cached platform choices between tests."""
    yield
    # Django
    try:
        from ai_translate.platforms.django import _reset_locale_choice
        _reset_locale_choice()
    except ImportError:
        pass
    # Android
    try:
        import ai_translate.platforms.android as andr
        andr._chosen_values_dir = None
    except (ImportError, AttributeError):
        pass
    # Flask/FastAPI
    try:
        import ai_translate.platforms.flask_fastapi as ff
        ff._chosen_trans_dir = None
    except (ImportError, AttributeError):
        pass
    # iOS
    try:
        import ai_translate.platforms.ios as iosp
        iosp._chosen_ios_format = None
    except (ImportError, AttributeError):
        pass
    # Env manager
    try:
        import ai_translate.services.env_manager as em
        em._chosen_env = None
    except (ImportError, AttributeError):
        pass


@pytest.fixture()
def tmp_django_project(tmp_path: Path) -> Path:
    """Create a minimal Django project layout with translatable strings."""
    # manage.py
    (tmp_path / "manage.py").write_text(
        "#!/usr/bin/env python\nimport sys\n", encoding="utf-8"
    )

    # myapp/views.py with _() translatable strings
    app_dir = tmp_path / "myapp"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "views.py").write_text(
        textwrap.dedent("""\
            from django.utils.translation import gettext as _

            def index(request):
                title = _("Welcome to our site")
                subtitle = _("Please log in")
                msg = _("Thank you for visiting")
                return title
        """),
        encoding="utf-8",
    )

    # locale dirs for es and fr — with actual .po files
    for lang in ("es", "fr"):
        lc_dir = tmp_path / "locale" / lang / "LC_MESSAGES"
        lc_dir.mkdir(parents=True)
        # Create a minimal valid .po file so language detection finds it
        po_content = (
            f'msgid ""\nmsgstr ""\n'
            f'"Content-Type: text/plain; charset=UTF-8\\n"\n'
            f'"Language: {lang}\\n"\n\n'
        )
        (lc_dir / "django.po").write_text(po_content, encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# Flutter
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_flutter_project(tmp_path: Path) -> Path:
    """Create a minimal Flutter project with ARB files."""
    (tmp_path / "pubspec.yaml").write_text(
        "name: test_app\ndescription: A test app\n", encoding="utf-8"
    )

    l10n_dir = tmp_path / "lib" / "l10n"
    l10n_dir.mkdir(parents=True)

    # English template ARB with 3 strings
    en_arb = {
        "@@locale": "en",
        "hello": "Hello",
        "welcome": "Welcome to the app",
        "logout": "Log out",
    }
    (l10n_dir / "app_en.arb").write_text(
        json.dumps(en_arb, indent=2) + "\n", encoding="utf-8"
    )

    # Empty Spanish ARB (target)
    es_arb = {"@@locale": "es"}
    (l10n_dir / "app_es.arb").write_text(
        json.dumps(es_arb, indent=2) + "\n", encoding="utf-8"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Android
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_android_project(tmp_path: Path) -> Path:
    """Create a minimal Android project with strings.xml."""
    # Gradle file
    gradle_dir = tmp_path / "app"
    gradle_dir.mkdir()
    (gradle_dir / "build.gradle").write_text(
        "apply plugin: 'com.android.application'\n", encoding="utf-8"
    )

    # Source strings.xml
    values_dir = tmp_path / "app" / "src" / "main" / "res" / "values"
    values_dir.mkdir(parents=True)
    (values_dir / "strings.xml").write_text(
        textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <resources>
                <string name="app_name">Test App</string>
                <string name="hello">Hello World</string>
                <string name="welcome">Welcome</string>
            </resources>
        """),
        encoding="utf-8",
    )

    # Target language directory (empty)
    es_dir = tmp_path / "app" / "src" / "main" / "res" / "values-es"
    es_dir.mkdir(parents=True)

    return tmp_path


# ---------------------------------------------------------------------------
# iOS
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_ios_project(tmp_path: Path) -> Path:
    """Create a minimal iOS project with Localizable.strings."""
    # .xcodeproj marker
    (tmp_path / "Test.xcodeproj").mkdir()

    # English .lproj with Localizable.strings
    en_lproj = tmp_path / "en.lproj"
    en_lproj.mkdir()
    (en_lproj / "Localizable.strings").write_text(
        textwrap.dedent("""\
            "hello" = "Hello";
            "welcome" = "Welcome to the app";
            "logout" = "Log out";
        """),
        encoding="utf-8",
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_flask_project(tmp_path: Path) -> Path:
    """Create a minimal Flask project."""
    (tmp_path / "app.py").write_text(
        textwrap.dedent("""\
            from flask import Flask
            from flask_babel import _

            app = Flask(__name__)

            @app.route("/")
            def index():
                return _("Hello from Flask")
        """),
        encoding="utf-8",
    )

    # translations directory — with actual .po file
    es_lc = tmp_path / "translations" / "es" / "LC_MESSAGES"
    es_lc.mkdir(parents=True)
    po_content = (
        'msgid ""\nmsgstr ""\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Language: es\\n"\n\n'
    )
    (es_lc / "messages.po").write_text(po_content, encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_fastapi_project(tmp_path: Path) -> Path:
    """Create a minimal FastAPI project."""
    (tmp_path / "main.py").write_text(
        textwrap.dedent("""\
            from fastapi import FastAPI

            app = FastAPI()

            @app.get("/")
            async def root():
                return {"message": "Hello World"}
        """),
        encoding="utf-8",
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_glossary(tmp_path: Path) -> Path:
    """Create a temporary glossary JSON file with 3 terms."""
    glossary = {
        "Dashboard": {"es": "Panel de control", "fr": "Tableau de bord"},
        "Settings": {"es": "Configuracion", "fr": "Parametres"},
        "Profile": {"es": "Perfil", "fr": "Profil"},
    }
    path = tmp_path / ".ai-translate-glossary.json"
    path.write_text(json.dumps(glossary, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Sample translations dict
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_translations() -> dict[str, dict[str, str]]:
    """Return a sample translations dict."""
    return {
        "Hello": {"es": "Hola", "fr": "Bonjour"},
        "Goodbye": {"es": "Adios", "fr": "Au revoir"},
        "Thank you": {"es": "Gracias", "fr": "Merci"},
    }
