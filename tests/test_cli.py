"""Tests for CLI argument parsing, glossary loading, and batch sizing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_translate.cli.main import (
    _compute_batch_size,
    _load_glossary,
    build_parser,
)


# ======================================================================
# Argument parser
# ======================================================================


class TestParser:
    """Tests for the CLI argument parser."""

    def test_parser_all_flags(self) -> None:
        """Parse all 16 flags and verify their values."""
        parser = build_parser()
        args = parser.parse_args([
            "--provider", "claude",
            "--model", "my-model-id",
            "--dry-run",
            "--batch-size", "42",
            "--no-auto-install",
            "--debug",
            "--review",
            "--estimate",
            "--quiet",
            "--json",
            "--lang", "es,fr,de",
            "--min-quality", "80",
            "--glossary", "/tmp/glossary.json",
            "--context", "Medical app for doctors",
            "--check",
            "--changed-only",
        ])

        assert args.provider == "claude"
        assert args.model == "my-model-id"
        assert args.dry_run is True
        assert args.batch_size == 42
        assert args.no_auto_install is True
        assert args.debug is True
        assert args.review is True
        assert args.estimate is True
        assert args.quiet is True
        assert args.json is True
        assert args.lang == "es,fr,de"
        assert args.min_quality == 80
        assert args.glossary == "/tmp/glossary.json"
        assert args.context == "Medical app for doctors"
        assert args.check is True
        assert args.changed_only is True

    def test_parser_defaults(self) -> None:
        """Verify default values when no flags are given."""
        parser = build_parser()
        args = parser.parse_args([])

        assert args.provider is None
        assert args.model is None
        assert args.dry_run is False
        assert args.batch_size == 0
        assert args.no_auto_install is False
        assert args.debug is False
        assert args.review is False
        assert args.estimate is False
        assert args.quiet is False
        assert args.json is False
        assert args.lang is None
        assert args.min_quality == 0
        assert args.glossary is None
        assert args.context is None
        assert args.check is False
        assert args.changed_only is False

    def test_version_flag(self) -> None:
        from ai_translate import __version__

        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0


# ======================================================================
# Glossary loading
# ======================================================================


class TestLoadGlossary:
    """Tests for the _load_glossary helper."""

    def test_load_glossary_auto_discover(self, tmp_path: Path) -> None:
        """Auto-discover .ai-translate-glossary.json in project root."""
        glossary_data = {
            "Dashboard": {"es": "Panel"},
            "Settings": {"es": "Configuracion"},
        }
        glossary_path = tmp_path / ".ai-translate-glossary.json"
        glossary_path.write_text(json.dumps(glossary_data), encoding="utf-8")

        result = _load_glossary(None, tmp_path)
        assert result is not None
        assert result == glossary_data

    def test_load_glossary_explicit_path(self, tmp_path: Path) -> None:
        """Load glossary from an explicit path."""
        glossary_data = {"Profile": {"fr": "Profil"}}
        explicit_path = tmp_path / "my_glossary.json"
        explicit_path.write_text(json.dumps(glossary_data), encoding="utf-8")

        result = _load_glossary(str(explicit_path), tmp_path)
        assert result is not None
        assert result == glossary_data

    def test_load_glossary_missing_returns_none(self, tmp_path: Path) -> None:
        """When no glossary file exists, return None."""
        result = _load_glossary(None, tmp_path)
        assert result is None


# ======================================================================
# Batch size computation
# ======================================================================


class TestComputeBatchSize:
    """Tests for _compute_batch_size."""

    def test_compute_batch_size_small(self) -> None:
        """For <= 10 strings, batch size equals count."""
        assert _compute_batch_size(1) == 1
        assert _compute_batch_size(5) == 5
        assert _compute_batch_size(10) == 10

    def test_compute_batch_size_medium(self) -> None:
        """For 11-50 strings, batch size is 15."""
        assert _compute_batch_size(11) == 15
        assert _compute_batch_size(30) == 15
        assert _compute_batch_size(50) == 15

    def test_compute_batch_size_large(self) -> None:
        """For 51-200 strings, batch size is 20. For >200, batch size is 25."""
        assert _compute_batch_size(51) == 20
        assert _compute_batch_size(100) == 20
        assert _compute_batch_size(200) == 20
        assert _compute_batch_size(201) == 25
        assert _compute_batch_size(500) == 25
