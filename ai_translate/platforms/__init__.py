"""Platform detection and shared utilities for all platform handlers."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# ── Directories to skip during project traversal ─────────────────────

SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__", ".git", ".hg", ".svn",
    "node_modules", ".tox", ".nox", ".mypy_cache", ".ruff_cache",
    "venv", ".venv", "env", ".env",
    "htmlcov", ".pytest_cache", "dist", "egg-info",
    # Mobile-specific
    ".dart_tool", ".fvm", ".pub-cache", ".gradle",
    ".idea", ".vscode", "Pods", "DerivedData",
    "build", ".build", "intermediates", "generated",
    # Django/Flask
    "migrations", "static", "media", "locale",
    "staticfiles", "collected_static",
})

# ── Supported platforms ───────────────────────────────────────────────

PLATFORMS = ("django", "flask", "fastapi", "flutter", "android", "ios")


# ── File system helpers ───────────────────────────────────────────────


def walk_project(root: Path, extra_skip: set[str] | None = None):
    """Recursively walk *root*, skipping irrelevant directories."""
    skip = SKIP_DIRS | (extra_skip or set())
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.endswith(".egg-info")]
        yield Path(dirpath), dirnames, filenames


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write *content* to *path* atomically (temp-file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(content)
        shutil.move(tmp, path)
    except BaseException:
        with open(os.devnull, "w") as _:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        shutil.move(tmp, path)
    except BaseException:
        with open(os.devnull, "w") as _:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


# ── Platform detection ────────────────────────────────────────────────


def detect_platform(project_root: Path) -> str | None:
    """Return the detected platform key or *None*."""
    children = {p.name for p in project_root.iterdir()} if project_root.is_dir() else set()

    # Flutter -- pubspec.yaml
    if "pubspec.yaml" in children:
        return "flutter"

    # Django -- manage.py + settings pattern
    if "manage.py" in children:
        return "django"

    # FastAPI -- main.py or app.py with fastapi import
    for name in ("main.py", "app.py", "app/__init__.py"):
        candidate = project_root / name
        if candidate.is_file():
            try:
                head = candidate.read_text(errors="ignore")[:4096]
                if "fastapi" in head.lower() or "FastAPI" in head:
                    return "fastapi"
            except OSError:
                pass

    # Flask -- app.py or wsgi.py with flask import
    for name in ("app.py", "wsgi.py", "application.py", "app/__init__.py"):
        candidate = project_root / name
        if candidate.is_file():
            try:
                head = candidate.read_text(errors="ignore")[:4096]
                if "flask" in head.lower() or "Flask" in head:
                    return "flask"
            except OSError:
                pass

    # Android -- app/build.gradle or build.gradle.kts
    for gradle in ("app/build.gradle", "app/build.gradle.kts"):
        if (project_root / gradle).is_file():
            return "android"

    # iOS -- .xcodeproj or .xcworkspace
    for child in children:
        if child.endswith(".xcodeproj") or child.endswith(".xcworkspace"):
            return "ios"

    return None


def get_platform_handler(platform: str):
    """Lazily import and return the platform module."""
    if platform == "django":
        from ai_translate.platforms import django as mod
    elif platform in ("flask", "fastapi"):
        from ai_translate.platforms import flask_fastapi as mod
    elif platform == "flutter":
        from ai_translate.platforms import flutter as mod
    elif platform == "android":
        from ai_translate.platforms import android as mod
    elif platform == "ios":
        from ai_translate.platforms import ios as mod
    else:
        raise ValueError(f"Unknown platform: {platform!r}")
    return mod
