"""Anthropic Claude translator backend."""

from __future__ import annotations

import logging

from ai_translate.services.translators.base import BaseTranslator

log = logging.getLogger(__name__)


class ClaudeTranslator(BaseTranslator):
    name = "Claude (Anthropic)"

    def validate_key(self) -> bool:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self.api_key)
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=10,
                messages=[{"role": "user", "content": "Reply with: ok"}],
            )
            return bool(resp.content)
        except Exception as exc:
            log.debug("Claude key validation failed: %s", exc)
            return False

    def _call_api(self, prompt: str) -> str | None:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self.api_key)
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=8192,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text
        except Exception as exc:
            log.debug("Claude API call failed: %s", exc)
            return None
