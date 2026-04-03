"""Mistral AI translator backend."""

from __future__ import annotations

import logging

from ai_translate.services.translators.base import BaseTranslator

log = logging.getLogger(__name__)


class MistralTranslator(BaseTranslator):
    name = "Mistral AI"

    def validate_key(self) -> bool:
        try:
            from mistralai import Mistral

            client = Mistral(api_key=self.api_key)
            resp = client.chat.complete(
                model="mistral-large-latest",
                max_tokens=10,
                messages=[{"role": "user", "content": "Reply with: ok"}],
            )
            return bool(resp.choices)
        except Exception as exc:
            log.debug("Mistral key validation failed: %s", exc)
            return False

    def _call_api(self, prompt: str) -> str | None:
        try:
            from mistralai import Mistral

            client = Mistral(api_key=self.api_key)
            resp = client.chat.complete(
                model="mistral-large-latest",
                max_tokens=8192,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": "You are a professional translator. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                ],
            )
            return resp.choices[0].message.content
        except Exception as exc:
            log.debug("Mistral API call failed: %s", exc)
            return None
