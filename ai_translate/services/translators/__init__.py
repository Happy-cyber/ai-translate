"""Translator registry, auto-detection, and model catalogue."""

from __future__ import annotations

from ai_translate.services.translators.base import BaseTranslator
from ai_translate.services.env_manager import ALL_ENV_KEYS, PROVIDER_ENV_KEYS

# ── Provider SDK mapping: (import_name, pip_name) ────────────────────

PROVIDER_SDK_MAP: dict[str, tuple[str, str]] = {
    "claude": ("anthropic", "anthropic"),
    "openai": ("openai", "openai"),
    "openrouter": ("openai", "openai"),
    "gemini": ("google.generativeai", "google-generativeai"),
    "mistral": ("mistralai", "mistralai"),
}

PROVIDER_PRIORITY = ("claude", "openai", "gemini", "openrouter", "mistral")

# ── OpenRouter model catalogue ────────────────────────────────────────

OPENROUTER_MODELS: list[dict[str, str]] = [
    {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4", "tag": "Best Quality", "color": "bright_cyan"},
    {"id": "openai/gpt-4o", "name": "GPT-4o", "tag": "Powerful", "color": "bright_green"},
    {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini", "tag": "Fast & Cheap", "color": "green"},
    {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash", "tag": "Ultra Fast", "color": "yellow"},
    {"id": "google/gemini-2.5-pro-preview", "name": "Gemini 2.5 Pro", "tag": "Premium", "color": "bright_yellow"},
    {"id": "meta-llama/llama-4-maverick", "name": "Llama 4 Maverick", "tag": "Open Source", "color": "bright_red"},
    {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B", "tag": "Balanced", "color": "red"},
    {"id": "mistralai/mistral-large-2411", "name": "Mistral Large", "tag": "European Languages", "color": "bright_magenta"},
    {"id": "deepseek/deepseek-chat-v3-0324", "name": "DeepSeek V3", "tag": "Budget King", "color": "bright_blue"},
    {"id": "qwen/qwen-2.5-72b-instruct", "name": "Qwen 2.5 72B", "tag": "Asian Languages", "color": "magenta"},
]


# ── Auto-detection ────────────────────────────────────────────────────


def auto_detect_provider(env_status: dict[str, str | None]) -> tuple[str, str] | None:
    """Return ``(provider_key, reason)`` for the first available key."""
    for provider in PROVIDER_PRIORITY:
        env_var = PROVIDER_ENV_KEYS.get(provider)
        if env_var and env_status.get(env_var):
            return provider, f"auto-detected from {env_var}"
    return None


# ── Factory ───────────────────────────────────────────────────────────


def get_translator(provider: str, api_key: str = "", model_id: str = "") -> BaseTranslator:
    """Instantiate the correct translator backend."""
    if provider == "skip":
        return _DummyTranslator()
    if provider == "claude":
        from ai_translate.services.translators.claude import ClaudeTranslator
        return ClaudeTranslator(api_key)
    if provider == "openai":
        from ai_translate.services.translators.openai_provider import OpenAITranslator
        return OpenAITranslator(api_key)
    if provider == "openrouter":
        from ai_translate.services.translators.openrouter import OpenRouterTranslator
        return OpenRouterTranslator(api_key, model_id=model_id or OPENROUTER_MODELS[0]["id"])
    if provider == "gemini":
        from ai_translate.services.translators.gemini import GeminiTranslator
        return GeminiTranslator(api_key)
    if provider == "mistral":
        from ai_translate.services.translators.mistral import MistralTranslator
        return MistralTranslator(api_key)
    raise ValueError(f"Unknown provider: {provider!r}")


# ── Dummy (skip) translator ──────────────────────────────────────────


class _DummyTranslator(BaseTranslator):
    name = "Skip (no AI)"

    def __init__(self) -> None:
        pass  # No API key needed

    def validate_key(self) -> bool:
        return True

    def _call_api(self, prompt: str) -> str | None:
        return None
