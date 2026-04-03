"""Tests for the 4 critical business logic scenarios."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


# ── Scenario 1: Global shared cache ──────────────────────────────────


def _patch_home(monkeypatch, tmp_path):
    from ai_translate.services import cache as mod
    home = tmp_path / "ai_home"
    monkeypatch.setattr(mod, "AI_TRANSLATE_HOME", home)
    monkeypatch.setattr(mod, "GLOBAL_CACHE_FILE", home / "global_cache.json")
    monkeypatch.setattr(mod, "PROJECTS_DIR", home / "projects")
    return home


class TestGlobalCache:
    """Cache should be shared across projects to reduce cost."""

    def test_save_creates_global_and_project_cache(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        home = _patch_home(monkeypatch, tmp_path)
        project = tmp_path / "project1"
        project.mkdir()

        cache_mod.save_cache(project, {"Hello": {"es": "Hola"}})

        # Global cache created
        assert (home / "global_cache.json").is_file()
        gd = json.loads((home / "global_cache.json").read_text())
        assert gd["Hello"]["es"] == "Hola"

        # Project cache in ~/.ai-translate/projects/<hash>/
        h = cache_mod._project_hash(project)
        assert (home / "projects" / h / "cache.json").is_file()

        # NOTHING in project folder
        assert list(project.iterdir()) == []

    def test_new_project_benefits_from_global(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        _patch_home(monkeypatch, tmp_path)

        project_a = tmp_path / "project_a"
        project_a.mkdir()
        cache_mod.save_cache(project_a, {"Submit": {"es": "Enviar"}})

        project_b = tmp_path / "project_b"
        project_b.mkdir()
        merged = cache_mod.load_cache(project_b)
        assert merged["Submit"]["es"] == "Enviar"

    def test_project_overrides_global(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        home = _patch_home(monkeypatch, tmp_path)

        home.mkdir(parents=True)
        (home / "global_cache.json").write_text(json.dumps({"Cancel": {"es": "Cancelar"}}))

        project = tmp_path / "myproject"
        project.mkdir()
        cache_mod.save_cache(project, {"Cancel": {"es": "Anular"}})

        merged = cache_mod.load_cache(project)
        assert merged["Cancel"]["es"] == "Anular"

    def test_lookup_finds_globally_cached(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod
        from ai_translate.services.cache import lookup_cached

        home = _patch_home(monkeypatch, tmp_path)

        home.mkdir(parents=True)
        (home / "global_cache.json").write_text(json.dumps({
            "Submit": {"es": "Enviar"},
            "Cancel": {"es": "Cancelar"},
        }))

        project = tmp_path / "new_project"
        project.mkdir()
        cache = cache_mod.load_cache(project)

        cached, uncached, _ = lookup_cached(
            cache, ["Submit", "Cancel", "Hello"], {"es": "Spanish"}
        )
        assert "Submit" in cached
        assert "Cancel" in cached
        assert "Hello" in uncached


# ── Scenario 2: New project (no locale files) ────────────────────────


class TestNewProject:
    """Tool should handle brand-new projects with no locale files."""

    def test_django_scan_works_without_locale_dir(self):
        from ai_translate.platforms import django as dj

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").touch()
            app = root / "myapp"
            app.mkdir()
            (app / "__init__.py").touch()
            (app / "views.py").write_text(
                'from django.utils.translation import gettext_lazy as _\n'
                'title = _("Welcome")\n'
            )
            # NO locale/ directory
            strings = dj.scan_source(root)
            assert len(strings) >= 1
            assert "Welcome" in strings

    def test_django_write_creates_locale_dir(self):
        from ai_translate.platforms import django as dj

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").touch()
            # NO locale/ dir
            translations = {"Welcome": {"es": "Bienvenido"}}
            source = {"Welcome": "Welcome"}
            stats = dj.write_translations(root, translations, source)
            assert stats["es"] == 1
            assert (root / "locale" / "es" / "LC_MESSAGES" / "django.po").is_file()

    def test_flutter_fallback_to_dart_scan(self):
        from ai_translate.platforms import flutter as fl

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pubspec.yaml").write_text("name: my_app")
            lib = root / "lib"
            lib.mkdir()
            (lib / "main.dart").write_text('Text("Hello".tr)\n')
            # NO .arb files
            strings = fl.scan_source(root)
            assert len(strings) >= 1

    def test_android_fallback_to_source_scan(self):
        from ai_translate.platforms import android as an

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "app").mkdir()
            (root / "app" / "build.gradle").write_text("android {}")
            java = root / "app" / "src" / "main" / "java"
            java.mkdir(parents=True)
            (java / "Main.kt").write_text('getString(R.string.hello_world)\n')
            # NO strings.xml
            strings = an.scan_source(root)
            assert len(strings) >= 1

    def test_ios_fallback_to_swift_scan(self):
        from ai_translate.platforms import ios

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "App.xcodeproj").mkdir()
            (root / "Sources").mkdir()
            (root / "Sources" / "App.swift").write_text(
                'let x = "Hello".localized\n'
            )
            # NO .strings or .xcstrings
            strings = ios.scan_source(root)
            assert len(strings) >= 1


# ── Scenario 3: Completely empty project ──────────────────────────────


class TestEmptyProject:
    """Tool should give helpful error when no strings found."""

    def test_empty_django_returns_empty(self):
        from ai_translate.platforms import django as dj

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").touch()
            # No .py files with _()
            strings = dj.scan_source(root)
            assert len(strings) == 0

    def test_empty_flutter_returns_empty(self):
        from ai_translate.platforms import flutter as fl

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pubspec.yaml").write_text("name: empty")
            strings = fl.scan_source(root)
            assert len(strings) == 0


# ── Scenario 4: Dynamic strings with placeholders ────────────────────


class TestDynamicStrings:
    """Tool should properly handle strings with variables/placeholders."""

    def test_django_detects_python_format(self):
        from ai_translate.platforms import django as dj

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").touch()
            app = root / "myapp"
            app.mkdir()
            (app / "__init__.py").touch()
            (app / "views.py").write_text(
                'from django.utils.translation import gettext_lazy as _\n'
                'msg = _("%(count)d Posts successfully deleted.")\n'
            )
            strings = dj.scan_source(root)
            assert "%(count)d Posts successfully deleted." in strings

    def test_django_detects_brace_format(self):
        from ai_translate.platforms import django as dj

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").touch()
            app = root / "myapp"
            app.mkdir()
            (app / "__init__.py").touch()
            (app / "views.py").write_text(
                'from django.utils.translation import gettext_lazy as _\n'
                'msg = _("Hello {name}, you have {count} new messages.")\n'
            )
            strings = dj.scan_source(root)
            found = [k for k in strings if "{name}" in k and "{count}" in k]
            assert len(found) == 1

    def test_django_detects_fstring_variables(self):
        from ai_translate.platforms import django as dj

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "manage.py").touch()
            app = root / "myapp"
            app.mkdir()
            (app / "__init__.py").touch()
            (app / "views.py").write_text(
                'from django.utils.translation import gettext_lazy as _\n'
                'msg = _(f"Welcome {username}")\n'
            )
            strings = dj.scan_source(root)
            found = [k for k in strings if "{username}" in k]
            assert len(found) == 1

    def test_flutter_icu_with_count_preserved(self):
        from ai_translate.platforms import flutter as fl

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pubspec.yaml").write_text("name: test")
            l10n = root / "lib" / "l10n"
            l10n.mkdir(parents=True)
            (l10n / "app_en.arb").write_text(json.dumps({
                "@@locale": "en",
                "deleteCount": "{count} Posts successfully deleted.",
            }))
            strings = fl.scan_source(root)
            assert "deleteCount" in strings
            assert "{count}" in strings["deleteCount"]


# ── Placeholder validation ────────────────────────────────────────────


class TestPlaceholderValidation:
    """Validate that translated strings preserve all placeholders."""

    def test_extract_python_percent_placeholders(self):
        from ai_translate.services.translators.base import extract_placeholders

        ph = extract_placeholders("%(count)d Posts deleted by %(name)s")
        assert "%(count)d" in ph
        assert "%(name)s" in ph

    def test_extract_brace_placeholders(self):
        from ai_translate.services.translators.base import extract_placeholders

        ph = extract_placeholders("{count} Posts successfully deleted.")
        assert "{count}" in ph

    def test_extract_android_placeholders(self):
        from ai_translate.services.translators.base import extract_placeholders

        ph = extract_placeholders("Hello %1$s, you have %2$d new messages")
        assert "%1$s" in ph
        assert "%2$d" in ph

    def test_extract_ios_placeholders(self):
        from ai_translate.services.translators.base import extract_placeholders

        ph = extract_placeholders("%@ has %d items")
        assert "%@" in ph
        assert "%d" in ph

    def test_validate_good_translation(self):
        from ai_translate.services.translators.base import validate_placeholders

        translations = {
            "{count} Posts deleted.": {
                "es": "{count} publicaciones eliminadas.",
                "fr": "{count} publications supprimées.",
            }
        }
        issues = validate_placeholders(translations)
        assert len(issues) == 0

    def test_validate_bad_translation_missing_placeholder(self):
        from ai_translate.services.translators.base import validate_placeholders

        translations = {
            "{count} Posts deleted.": {
                "es": "Publicaciones eliminadas.",  # Missing {count}!
                "fr": "{count} publications supprimées.",  # OK
            }
        }
        issues = validate_placeholders(translations)
        assert "{count} Posts deleted." in issues
        assert "es" in issues["{count} Posts deleted."]
        assert "fr" not in issues.get("{count} Posts deleted.", {})

    def test_validate_percent_format(self):
        from ai_translate.services.translators.base import validate_placeholders

        translations = {
            "%(count)d items in %(name)s": {
                "es": "%(count)d artículos en %(name)s",  # OK
            }
        }
        issues = validate_placeholders(translations)
        assert len(issues) == 0

    def test_validate_percent_format_missing(self):
        from ai_translate.services.translators.base import validate_placeholders

        translations = {
            "%d items by %s": {
                "es": "artículos",  # Missing both %d and %s!
            }
        }
        issues = validate_placeholders(translations)
        assert "%d items by %s" in issues
        assert "es" in issues["%d items by %s"]
