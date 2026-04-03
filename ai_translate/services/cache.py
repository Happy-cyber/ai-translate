"""Translation cache — all data stored in ~/.ai-translate/, zero project pollution.

Storage layout::

    ~/.ai-translate/
    ├── global_cache.json              Shared translations (cross-project)
    └── projects/
        ├── <hash>/
        │   ├── cache.json             This project's translations
        │   └── meta.json             {"path": "...", "platform": "...", "last_run": "..."}
        └── ...

The project hash is derived from the absolute project path, ensuring each
project gets its own isolated cache directory without writing anything
into the user's project folder.

Cache lookup order:
    1. Project cache  (``~/.ai-translate/projects/<hash>/cache.json``)
    2. Global cache   (``~/.ai-translate/global_cache.json``)

Write order:
    1. Always write to project cache
    2. Also merge into global cache (so other projects benefit)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

# ── Central storage directory ─────────────────────────────────────────

AI_TRANSLATE_HOME = Path.home() / ".ai-translate"
GLOBAL_CACHE_FILE = AI_TRANSLATE_HOME / "global_cache.json"
PROJECTS_DIR = AI_TRANSLATE_HOME / "projects"


# ── Project identity ─────────────────────────────────────────────────


def _project_hash(project_root: Path) -> str:
    """Deterministic short hash from the absolute project path."""
    canonical = str(project_root.resolve())
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def _project_dir(project_root: Path) -> Path:
    """Return ``~/.ai-translate/projects/<hash>/``."""
    return PROJECTS_DIR / _project_hash(project_root)


def _project_cache_path(project_root: Path) -> Path:
    return _project_dir(project_root) / "cache.json"


def _project_meta_path(project_root: Path) -> Path:
    return _project_dir(project_root) / "meta.json"


# ── Atomic I/O helpers ────────────────────────────────────────────────


def _atomic_write_json(path: Path, data: dict) -> None:
    """Atomically persist a dict as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        shutil.move(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_json(path: Path) -> dict[str, dict[str, str]]:
    """Load a JSON cache file. Returns empty dict on any failure."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Cache load failed for %s: %s", path, exc)
    return {}


# ── Meta file (project registry) ─────────────────────────────────────


def _save_meta(project_root: Path, platform: str = "") -> None:
    """Record project path and metadata for discoverability."""
    meta_path = _project_meta_path(project_root)
    meta = {
        "path": str(project_root.resolve()),
        "platform": platform,
        "last_run": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hash": _project_hash(project_root),
    }
    try:
        _atomic_write_json(meta_path, meta)
    except OSError as exc:
        log.debug("Could not save project meta: %s", exc)


# ── Load / Save ───────────────────────────────────────────────────────


def load_cache(project_root: Path) -> dict[str, dict[str, str]]:
    """Load merged cache: project-specific overrides global.

    Returns ``{source: {lang: text}}``.
    """
    global_data = _load_json(GLOBAL_CACHE_FILE)
    project_data = _load_json(_project_cache_path(project_root))

    # Also check for legacy project-local cache and migrate it
    legacy_path = project_root / ".translation_cache.json"
    if legacy_path.is_file():
        legacy_data = _load_json(legacy_path)
        if legacy_data:
            log.info("Migrating legacy cache from project folder to ~/.ai-translate/")
            # Merge legacy into project cache
            for msg, langs in legacy_data.items():
                if msg not in project_data:
                    project_data[msg] = {}
                for lang, text in langs.items():
                    if text and lang not in project_data[msg]:
                        project_data[msg][lang] = text
            # Save migrated data and remove legacy file
            _atomic_write_json(_project_cache_path(project_root), project_data)
            try:
                legacy_path.unlink()
                log.info("Removed legacy cache file: %s", legacy_path)
            except OSError:
                pass

    # Merge: project takes priority over global
    merged: dict[str, dict[str, str]] = {}
    for msg in set(global_data) | set(project_data):
        merged[msg] = {}
        if msg in global_data:
            merged[msg].update(global_data[msg])
        if msg in project_data:
            merged[msg].update(project_data[msg])

    return merged


def save_cache(
    project_root: Path,
    cache: dict[str, dict[str, str]],
    platform: str = "",
) -> None:
    """Persist cache to BOTH project-specific and global locations.

    Everything goes to ``~/.ai-translate/``. Nothing is written to the
    user's project folder.
    """
    # 1. Save project-specific cache
    _atomic_write_json(_project_cache_path(project_root), cache)

    # 2. Update project metadata
    _save_meta(project_root, platform)

    # 3. Merge into global cache (non-destructive)
    try:
        global_data = _load_json(GLOBAL_CACHE_FILE)
        for msg, langs in cache.items():
            if msg not in global_data:
                global_data[msg] = {}
            for lang, text in langs.items():
                if text:
                    global_data[msg][lang] = text
        _atomic_write_json(GLOBAL_CACHE_FILE, global_data)
    except OSError as exc:
        log.debug("Could not update global cache: %s", exc)


# ── Lookup / Update ───────────────────────────────────────────────────


def lookup_cached(
    cache: dict[str, dict[str, str]],
    messages: list[str],
    target_languages: dict[str, str],
) -> tuple[dict[str, dict[str, str]], list[str], dict[str, dict[str, str]]]:
    """Check which messages are already cached, per-language.

    Returns
    -------
    cached_translations
        ``{source: {lang: translated}}`` — ready to use.
    uncached_messages
        Source strings that need at least one language translated.
    uncached_langs
        ``{source: {lang_code: lang_name}}`` — only the missing languages.
    """
    cached: dict[str, dict[str, str]] = {}
    uncached_msgs: list[str] = []
    uncached_langs: dict[str, dict[str, str]] = {}

    for msg in messages:
        cached_entry = cache.get(msg, {})
        msg_cached: dict[str, str] = {}
        msg_uncached: dict[str, str] = {}

        for code, name in target_languages.items():
            if code in cached_entry and cached_entry[code]:
                msg_cached[code] = cached_entry[code]
            else:
                msg_uncached[code] = name

        if msg_cached:
            cached[msg] = msg_cached
        if msg_uncached:
            uncached_msgs.append(msg)
            uncached_langs[msg] = msg_uncached

    return cached, uncached_msgs, uncached_langs


def update_cache(
    cache: dict[str, dict[str, str]],
    new_translations: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Merge *new_translations* into *cache* (non-destructive)."""
    for msg, langs in new_translations.items():
        if msg not in cache:
            cache[msg] = {}
        for lang, text in langs.items():
            if text:
                cache[msg][lang] = text
    return cache


# ── Cache info (for UI/debugging) ────────────────────────────────────


def get_cache_info(project_root: Path) -> dict[str, str]:
    """Return cache location info for display."""
    proj_dir = _project_dir(project_root)
    proj_cache = _project_cache_path(project_root)
    proj_entries = len(_load_json(proj_cache)) if proj_cache.is_file() else 0
    global_entries = len(_load_json(GLOBAL_CACHE_FILE)) if GLOBAL_CACHE_FILE.is_file() else 0

    return {
        "home": str(AI_TRANSLATE_HOME),
        "project_dir": str(proj_dir),
        "project_hash": _project_hash(project_root),
        "project_entries": str(proj_entries),
        "global_entries": str(global_entries),
    }
