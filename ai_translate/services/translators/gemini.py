"""Google Gemini translator backend."""

from __future__ import annotations

import logging

from ai_translate.services.translators.base import BaseTranslator

log = logging.getLogger(__name__)


class GeminiTranslator(BaseTranslator):
    name = "Google Gemini"

    def validate_key(self) -> bool:
        try:
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)
            model = genai.GenerativeModel("gemini-2.5-flash")
            resp = model.generate_content("Reply with: ok")
            return bool(resp.text)
        except Exception as exc:
            log.debug("Gemini key validation failed: %s", exc)
            return False

    def _call_api(self, prompt: str) -> str | None:
        try:
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)
            model = genai.GenerativeModel("gemini-2.5-flash")
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=8192,
                ),
            )
            return resp.text
        except Exception as exc:
            log.debug("Gemini API call failed: %s", exc)
            return None
