"""Tests for the translator registry, prompt building, and response parsing."""

from __future__ import annotations

import json

import pytest

from ai_translate.services.translators import get_translator
from ai_translate.services.translators.base import (
    PLATFORM_RULES,
    BaseTranslator,
    build_prompt,
    estimate_tokens,
    parse_response,
)


# ======================================================================
# get_translator factory
# ======================================================================


class TestGetTranslator:
    """Tests for the provider factory function."""

    @pytest.mark.parametrize(
        "provider",
        ["claude", "openai", "openrouter", "gemini", "mistral", "skip"],
    )
    def test_get_translator_all_providers(self, provider: str) -> None:
        translator = get_translator(provider, api_key="test-key-123")
        assert isinstance(translator, BaseTranslator)

    def test_skip_translator_returns_none(self) -> None:
        translator = get_translator("skip")
        result = translator._call_api("anything")
        assert result is None


# ======================================================================
# Auto-detect provider
# ======================================================================


class TestAutoDetectProvider:
    """Tests for auto_detect_provider."""

    def test_auto_detect_provider_finds_first(self) -> None:
        from ai_translate.services.translators import auto_detect_provider

        env_status = {
            "ANTHROPIC_API_KEY": "sk-ant-xxx",
            "OPENAI_API_KEY": None,
            "OPENROUTER_API_KEY": None,
            "GOOGLE_GEMINI_KEY": None,
            "MISTRAL_API_KEY": None,
        }
        result = auto_detect_provider(env_status)
        assert result is not None
        provider, reason = result
        assert provider == "claude"
        assert "ANTHROPIC_API_KEY" in reason

    def test_auto_detect_provider_none_when_empty(self) -> None:
        from ai_translate.services.translators import auto_detect_provider

        env_status = {
            "ANTHROPIC_API_KEY": None,
            "OPENAI_API_KEY": None,
            "OPENROUTER_API_KEY": None,
            "GOOGLE_GEMINI_KEY": None,
            "MISTRAL_API_KEY": None,
        }
        assert auto_detect_provider(env_status) is None


# ======================================================================
# Prompt building
# ======================================================================


class TestBuildPrompt:
    """Tests for the build_prompt function."""

    def test_build_prompt_basic(self) -> None:
        prompt = build_prompt(
            messages=["Hello", "Goodbye"],
            target_languages={"es": "Spanish"},
        )
        assert isinstance(prompt, str)
        assert "Hello" in prompt
        assert "Goodbye" in prompt
        assert "Spanish" in prompt
        assert "es" in prompt
        # Should contain the rules section
        assert "Preserve ALL placeholders" in prompt
        assert "JSON" in prompt

    def test_build_prompt_with_glossary(self) -> None:
        glossary = {
            "Dashboard": {"es": "Panel de control"},
        }
        prompt = build_prompt(
            messages=["Dashboard"],
            target_languages={"es": "Spanish"},
            glossary=glossary,
        )
        assert "MANDATORY TERMINOLOGY" in prompt
        assert "Panel de control" in prompt
        assert "Dashboard" in prompt

    def test_build_prompt_with_context(self) -> None:
        prompt = build_prompt(
            messages=["Submit"],
            target_languages={"es": "Spanish"},
            context="E-commerce checkout flow",
        )
        assert "PROJECT CONTEXT" in prompt
        assert "E-commerce checkout flow" in prompt

    @pytest.mark.parametrize(
        "platform",
        ["django", "flask", "fastapi", "flutter", "android", "ios"],
    )
    def test_build_prompt_all_platforms(self, platform: str) -> None:
        prompt = build_prompt(
            messages=["Hello"],
            target_languages={"es": "Spanish"},
            platform=platform,
        )
        assert isinstance(prompt, str)
        # Platform-specific rules should be injected
        assert PLATFORM_RULES[platform].splitlines()[0].lstrip("- ") in prompt


# ======================================================================
# Response parsing
# ======================================================================


class TestParseResponse:
    """Tests for the parse_response function."""

    def test_parse_response_valid_json(self) -> None:
        response_data = {
            "Hello": {"es": "Hola", "fr": "Bonjour"},
            "Goodbye": {"es": "Adios", "fr": "Au revoir"},
        }
        raw = json.dumps(response_data)
        result = parse_response(
            raw,
            expected_messages=["Hello", "Goodbye"],
            target_languages={"es": "Spanish", "fr": "French"},
        )
        assert result is not None
        assert "Hello" in result
        assert result["Hello"]["es"] == "Hola"
        assert result["Hello"]["fr"] == "Bonjour"
        assert result["Goodbye"]["es"] == "Adios"

    def test_parse_response_with_fences(self) -> None:
        response_data = {"Hello": {"es": "Hola"}}
        raw = "```json\n" + json.dumps(response_data) + "\n```"
        result = parse_response(
            raw,
            expected_messages=["Hello"],
            target_languages={"es": "Spanish"},
        )
        assert result is not None
        assert result["Hello"]["es"] == "Hola"

    def test_parse_response_invalid_json_returns_none(self) -> None:
        raw = "This is not JSON at all {{{{"
        result = parse_response(
            raw,
            expected_messages=["Hello"],
            target_languages={"es": "Spanish"},
        )
        assert result is None

    def test_parse_response_partial(self) -> None:
        """When some messages are present but others are missing."""
        response_data = {
            "Hello": {"es": "Hola"},
            # "Goodbye" is missing
        }
        raw = json.dumps(response_data)
        result = parse_response(
            raw,
            expected_messages=["Hello", "Goodbye"],
            target_languages={"es": "Spanish"},
        )
        # Should still return the partial result (Hello is valid)
        assert result is not None
        assert "Hello" in result
        assert "Goodbye" not in result


# ======================================================================
# Token estimation
# ======================================================================


class TestEstimateTokens:
    """Tests for the estimate_tokens function."""

    def test_estimate_tokens_returns_dict(self) -> None:
        costs = estimate_tokens(
            messages=["Hello", "Goodbye"],
            target_languages={"es": "Spanish", "fr": "French"},
        )
        assert isinstance(costs, dict)
        assert len(costs) > 0
        # Should contain known provider keys
        assert "openai" in costs or "anthropic" in costs or "google" in costs

    def test_estimate_tokens_values_are_floats(self) -> None:
        costs = estimate_tokens(
            messages=["Hello world", "Good morning"],
            target_languages={"es": "Spanish"},
        )
        for provider, cost in costs.items():
            assert isinstance(cost, float), f"Cost for {provider} is not a float: {type(cost)}"
            assert cost >= 0, f"Cost for {provider} is negative"
