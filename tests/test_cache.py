"""Cache tests — project-isolated storage + global sharing + legacy migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _patch_cache_home(monkeypatch, tmp_path):
    """Redirect all cache storage to a temp directory."""
    from ai_translate.services import cache as mod

    home = tmp_path / "ai_translate_home"
    monkeypatch.setattr(mod, "AI_TRANSLATE_HOME", home)
    monkeypatch.setattr(mod, "GLOBAL_CACHE_FILE", home / "global_cache.json")
    monkeypatch.setattr(mod, "PROJECTS_DIR", home / "projects")
    return home


class TestLoadCache:
    def test_load_cache_missing_file(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        _patch_cache_home(monkeypatch, tmp_path)
        project = tmp_path / "myproject"
        project.mkdir()
        assert cache_mod.load_cache(project) == {}

    def test_load_cache_valid_project(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        home = _patch_cache_home(monkeypatch, tmp_path)
        project = tmp_path / "myproject"
        project.mkdir()

        proj_dir = home / "projects" / cache_mod._project_hash(project)
        proj_dir.mkdir(parents=True)
        (proj_dir / "cache.json").write_text(json.dumps({"Hello": {"es": "Hola"}}))

        result = cache_mod.load_cache(project)
        assert result["Hello"]["es"] == "Hola"

    def test_load_cache_corrupt_json(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        home = _patch_cache_home(monkeypatch, tmp_path)
        project = tmp_path / "myproject"
        project.mkdir()

        proj_dir = home / "projects" / cache_mod._project_hash(project)
        proj_dir.mkdir(parents=True)
        (proj_dir / "cache.json").write_text("{broken!!!")

        assert cache_mod.load_cache(project) == {}


class TestSaveCache:
    def test_creates_project_and_global(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        home = _patch_cache_home(monkeypatch, tmp_path)
        project = tmp_path / "myproject"
        project.mkdir()

        cache_mod.save_cache(project, {"Submit": {"es": "Enviar"}}, platform="django")

        # Project cache in ~/.ai-translate/projects/<hash>/
        h = cache_mod._project_hash(project)
        assert (home / "projects" / h / "cache.json").is_file()
        assert (home / "projects" / h / "meta.json").is_file()

        # Global cache
        assert (home / "global_cache.json").is_file()
        gd = json.loads((home / "global_cache.json").read_text())
        assert gd["Submit"]["es"] == "Enviar"

    def test_zero_project_pollution(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        _patch_cache_home(monkeypatch, tmp_path)
        project = tmp_path / "myproject"
        project.mkdir()
        (project / "manage.py").touch()

        cache_mod.save_cache(project, {"Hello": {"es": "Hola"}})

        # ONLY manage.py in project — nothing else
        assert sorted(f.name for f in project.iterdir()) == ["manage.py"]

    def test_meta_records_platform_and_path(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        home = _patch_cache_home(monkeypatch, tmp_path)
        project = tmp_path / "myproject"
        project.mkdir()

        cache_mod.save_cache(project, {}, platform="flutter")

        h = cache_mod._project_hash(project)
        meta = json.loads((home / "projects" / h / "meta.json").read_text())
        assert meta["platform"] == "flutter"
        assert meta["path"] == str(project.resolve())
        assert len(meta["hash"]) == 12


class TestGlobalSharing:
    def test_new_project_gets_global(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        _patch_cache_home(monkeypatch, tmp_path)

        proj_a = tmp_path / "project_a"
        proj_a.mkdir()
        cache_mod.save_cache(proj_a, {"Submit": {"es": "Enviar"}})

        proj_b = tmp_path / "project_b"
        proj_b.mkdir()
        merged = cache_mod.load_cache(proj_b)
        assert merged["Submit"]["es"] == "Enviar"

    def test_project_overrides_global(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        home = _patch_cache_home(monkeypatch, tmp_path)

        home.mkdir(parents=True)
        (home / "global_cache.json").write_text(json.dumps({"Cancel": {"es": "Cancelar"}}))

        project = tmp_path / "myproject"
        project.mkdir()
        cache_mod.save_cache(project, {"Cancel": {"es": "Anular"}})

        merged = cache_mod.load_cache(project)
        assert merged["Cancel"]["es"] == "Anular"

    def test_isolated_project_hashes(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        _patch_cache_home(monkeypatch, tmp_path)

        pa = tmp_path / "a"
        pb = tmp_path / "b"
        pa.mkdir()
        pb.mkdir()
        assert cache_mod._project_hash(pa) != cache_mod._project_hash(pb)


class TestLegacyMigration:
    def test_migrates_old_cache_and_removes(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        home = _patch_cache_home(monkeypatch, tmp_path)
        project = tmp_path / "myproject"
        project.mkdir()

        legacy = project / ".translation_cache.json"
        legacy.write_text(json.dumps({"Legacy": {"es": "Legado"}}))

        merged = cache_mod.load_cache(project)

        assert merged["Legacy"]["es"] == "Legado"
        assert not legacy.exists()  # Removed from project

        h = cache_mod._project_hash(project)
        assert (home / "projects" / h / "cache.json").is_file()


class TestLookupCached:
    def test_full_hit(self):
        from ai_translate.services.cache import lookup_cached

        cache = {"Hello": {"es": "Hola", "fr": "Bonjour"}}
        cached, uncached, _ = lookup_cached(cache, ["Hello"], {"es": "Spanish", "fr": "French"})
        assert "Hello" in cached
        assert len(uncached) == 0

    def test_partial_hit(self):
        from ai_translate.services.cache import lookup_cached

        cache = {"Hello": {"es": "Hola"}}
        cached, uncached, uncached_langs = lookup_cached(
            cache, ["Hello"], {"es": "Spanish", "fr": "French"}
        )
        assert cached["Hello"]["es"] == "Hola"
        assert "Hello" in uncached
        assert "fr" in uncached_langs["Hello"]

    def test_no_hit(self):
        from ai_translate.services.cache import lookup_cached

        cached, uncached, _ = lookup_cached({}, ["Hello"], {"es": "Spanish"})
        assert len(cached) == 0
        assert "Hello" in uncached


class TestUpdateCache:
    def test_merges(self):
        from ai_translate.services.cache import update_cache

        cache = {"Hello": {"es": "Hola"}}
        updated = update_cache(cache, {"World": {"es": "Mundo"}})
        assert updated["Hello"]["es"] == "Hola"
        assert updated["World"]["es"] == "Mundo"

    def test_adds_lang_not_overwrite(self):
        from ai_translate.services.cache import update_cache

        cache = {"Hello": {"es": "Hola"}}
        updated = update_cache(cache, {"Hello": {"fr": "Bonjour"}})
        assert updated["Hello"]["es"] == "Hola"
        assert updated["Hello"]["fr"] == "Bonjour"


class TestCacheInfo:
    def test_returns_info(self, tmp_path, monkeypatch):
        from ai_translate.services import cache as cache_mod

        home = _patch_cache_home(monkeypatch, tmp_path)
        project = tmp_path / "myproject"
        project.mkdir()

        info = cache_mod.get_cache_info(project)
        assert info["home"] == str(home)
        assert len(info["project_hash"]) == 12
        assert info["project_entries"] == "0"
        assert info["global_entries"] == "0"
