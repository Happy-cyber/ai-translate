"""OpenRouter translator backend (100+ models via OpenAI-compatible API)."""

from __future__ import annotations

import logging

from ai_translate.services.translators.base import BaseTranslator

log = logging.getLogger(__name__)

_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterTranslator(BaseTranslator):
    name = "OpenRouter"

    def __init__(self, api_key: str, model_id: str = "anthropic/claude-sonnet-4") -> None:
        super().__init__(api_key)
        self.model_id = model_id
        self.name = f"OpenRouter ({model_id.split('/')[-1]})"

    def validate_key(self) -> bool:
        try:
            import openai as openai_lib

            client = openai_lib.OpenAI(api_key=self.api_key, base_url=_BASE_URL)
            resp = client.chat.completions.create(
                model=self.model_id,
                max_tokens=10,
                messages=[{"role": "user", "content": "Reply with: ok"}],
            )
            return bool(resp.choices)
        except Exception as exc:
            log.debug("OpenRouter key validation failed: %s", exc)
            return False

    def _call_api(self, prompt: str) -> str | None:
        try:
            import openai as openai_lib

            client = openai_lib.OpenAI(api_key=self.api_key, base_url=_BASE_URL)
            resp = client.chat.completions.create(
                model=self.model_id,
                max_tokens=8192,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": "You are a professional translator. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                extra_headers={"X-Title": "AI Translate"},
            )
            return resp.choices[0].message.content
        except Exception as exc:
            log.debug("OpenRouter API call failed: %s", exc)
            return None
