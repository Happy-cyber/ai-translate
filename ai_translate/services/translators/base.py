"""Abstract base translator with retry logic and prompt construction."""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds; exponential: 2, 4, 8

# ── Token cost estimates per provider (USD per 1K tokens) ────────────
_TOKEN_COSTS: dict[str, dict[str, float]] = {
    "openai": {"input": 0.005, "output": 0.015},
    "anthropic": {"input": 0.003, "output": 0.015},
    "google": {"input": 0.00025, "output": 0.0005},
    "deepl": {"input": 0.00, "output": 0.00},  # flat-rate, not token-based
}

# Average characters per token (rough estimate across providers)
_CHARS_PER_TOKEN = 4

# ── Platform-specific translation rules ───────────────────────────────

PLATFORM_RULES: dict[str, str] = {
    "django": (
        "- Preserve Django template variables like {{ variable }} and {% tag %}\n"
        "- Preserve Python format specifiers like %(name)s, {0}, {name}\n"
        "- Do not translate Python string formatting patterns\n"
        "- For ngettext plural strings (marked with ::plural::), provide a JSON array of forms: [singular_translation, plural_translation]\n"
    ),
    "flask": (
        "- Preserve Jinja2 template variables like {{ variable }} and {% tag %}\n"
        "- Preserve Python format specifiers like %(name)s, {0}, {name}\n"
        "- Do not translate Python string formatting patterns\n"
        "- For ngettext plural strings (marked with ::plural::), provide a JSON array of forms: [singular_translation, plural_translation]\n"
    ),
    "fastapi": (
        "- Preserve Python format specifiers like %(name)s, {0}, {name}\n"
        "- Do not translate Python string formatting patterns\n"
        "- For ngettext plural strings (marked with ::plural::), provide a JSON array of forms: [singular_translation, plural_translation]\n"
    ),
    "flutter": (
        "- Preserve {placeholder} variables exactly as they appear\n"
        "- Preserve ICU plural/select syntax: {count, plural, =0{...} =1{...} other{...}}\n"
        "- Never translate @@ or @ metadata keys\n"
        "- For ICU plural patterns, translate each variant inside the braces while preserving the ICU structure\n"
    ),
    "android": (
        "- Preserve Android format specifiers exactly: %1$s, %d, %2$s, %s\n"
        "- Escape apostrophes as \\' in translated strings\n"
        "- Preserve XML entities: &amp; &lt; &gt; &quot; &apos;\n"
        "- For plural strings (marked with __plural__), return a JSON object with quantity keys: {\"one\": \"...\", \"other\": \"...\"}\n"
    ),
    "ios": (
        "- Preserve iOS format specifiers exactly: %@, %d, %ld, %f, %1$@\n"
        "- Preserve escape sequences: \\n \\t \\\\\n"
        "- Do not modify escaped quotes: \\\"\n"
        "- For plural strings, provide a JSON object with CLDR quantity keys: {\"one\": \"...\", \"other\": \"...\"}\n"
    ),
}


# ── Prompt builder ────────────────────────────────────────────────────


def build_prompt(
    messages: list[str],
    target_languages: dict[str, str],
    platform: str = "",
    glossary: dict[str, dict[str, str]] | None = None,
    context: str | None = None,
) -> str:
    """Build the translation prompt for the AI provider.

    Parameters
    ----------
    messages:
        Source strings to translate.
    target_languages:
        Mapping of language codes to language names, e.g. ``{"es": "Spanish"}``.
    platform:
        One of the supported platform keys (django, flask, etc.).
    glossary:
        Optional terminology dictionary.  Each key is a source term and its
        value maps language codes to mandatory translations, e.g.
        ``{"Dashboard": {"es": "Panel de control", "fr": "Tableau de bord"}}``.
    context:
        Optional free-text project context that should influence translation
        style and domain vocabulary.
    """
    platform_label = {
        "django": "Django web application",
        "flask": "Flask web application",
        "fastapi": "FastAPI web application",
        "flutter": "Flutter mobile application",
        "android": "Android mobile application",
        "ios": "iOS mobile application",
    }.get(platform, "software application")

    rules = (
        "1. Preserve ALL placeholders, variables, and format specifiers exactly.\n"
        "2. Return ONLY a valid JSON object — no markdown, no explanation.\n"
        "3. Translate EVERY message for EVERY target language.\n"
        "4. Keep translations natural and fluent for native speakers.\n"
        "5. Do not translate HTML tags, URLs, or code identifiers.\n"
        "6. Match the tone and formality of the source text.\n"
        "7. For plural forms (arrays or objects), translate EVERY variant for the target language's grammar.\n"
    )

    platform_section = ""
    if platform and platform in PLATFORM_RULES:
        platform_section = f"\nPlatform-specific rules:\n{PLATFORM_RULES[platform]}\n"

    # -- Glossary section --------------------------------------------------
    glossary_section = ""
    if glossary:
        glossary_lines: list[str] = []
        for term, lang_map in glossary.items():
            parts = ", ".join(f"{lc}: {tr}" for lc, tr in lang_map.items())
            glossary_lines.append(f'  "{term}" -> {parts}')
        glossary_section = (
            "\nMANDATORY TERMINOLOGY — you MUST use these exact translations "
            "whenever the source term appears:\n"
            + "\n".join(glossary_lines)
            + "\n"
        )

    # -- Context section ---------------------------------------------------
    context_section = ""
    if context:
        context_section = (
            f"\nPROJECT CONTEXT: {context}. Adapt translations to this domain.\n"
        )

    msgs_json = json.dumps(messages, ensure_ascii=False, indent=2)
    langs_json = json.dumps(target_languages, ensure_ascii=False, indent=2)

    example_msg = messages[0] if messages else "Hello"
    example_langs = list(target_languages.keys())[:2] or ["es", "fr"]
    example = json.dumps(
        {example_msg: {lc: f"<translated {lc}>" for lc in example_langs}},
        ensure_ascii=False,
        indent=2,
    )

    return (
        f"You are a professional translator for a {platform_label}.\n\n"
        f"Rules:\n{rules}\n"
        f"{platform_section}\n"
        f"{glossary_section}"
        f"{context_section}"
        f"Messages to translate:\n{msgs_json}\n\n"
        f"Target languages:\n{langs_json}\n\n"
        f"Expected output format:\n{example}\n\n"
        "Return ONLY the JSON object with translations for ALL messages and ALL languages."
    )


# ── Response parser ───────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?", re.IGNORECASE)


def parse_response(
    raw: str,
    expected_messages: list[str],
    target_languages: dict[str, str],
) -> dict[str, dict[str, str]] | None:
    """Parse and validate the AI response JSON."""
    text = _FENCE_RE.sub("", raw).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.debug("JSON parse failed for response: %.200s", text)
        return None

    if not isinstance(data, dict):
        return None

    result: dict[str, dict[str, str]] = {}
    for msg in expected_messages:
        entry = data.get(msg)
        if not isinstance(entry, dict):
            continue
        translations: dict[str, str] = {}
        for lang_code in target_languages:
            val = entry.get(lang_code)
            if isinstance(val, str) and val.strip():
                translations[lang_code] = val.strip()
        if translations:
            result[msg] = translations

    if not result:
        log.debug("No valid translations extracted from response.")
        return None

    missing = set(expected_messages) - set(result)
    if missing:
        log.debug("Partial response: missing %d/%d messages.", len(missing), len(expected_messages))

    return result


# ── Placeholder validation ────────────────────────────────────────────

# Patterns that match common placeholders across all platforms
_PLACEHOLDER_PATTERNS = [
    re.compile(r"\{[^}]+\}"),               # {name}, {count}, {0}
    re.compile(r"%(?:\d+\$)?[sdlfFeEgG@]"),  # %s, %d, %1$s, %@, %ld
    re.compile(r"%\([^)]+\)[sdlfFeEgG]"),    # %(name)s, %(count)d
    re.compile(r"%%"),                        # Literal %% (must be preserved)
]


def extract_placeholders(text: str) -> set[str]:
    """Extract all placeholder tokens from a string."""
    found: set[str] = set()
    for pat in _PLACEHOLDER_PATTERNS:
        for m in pat.finditer(text):
            found.add(m.group())
    return found


def validate_placeholders(
    translations: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Check that translated strings preserve all source placeholders.

    Returns ``{source_msg: {lang_code: "missing: {name}, %d"}}`` for
    translations that lost placeholders.  Empty dict means all valid.
    """
    issues: dict[str, dict[str, str]] = {}
    for source, lang_map in translations.items():
        source_ph = extract_placeholders(source)
        if not source_ph:
            continue
        for lang_code, translated in lang_map.items():
            translated_ph = extract_placeholders(translated)
            missing = source_ph - translated_ph
            if missing:
                issues.setdefault(source, {})[lang_code] = (
                    f"Missing placeholders: {', '.join(sorted(missing))}"
                )
                log.warning(
                    "Placeholder mismatch in '%s' [%s]: missing %s",
                    source[:50], lang_code, missing,
                )
    return issues


# ── Confidence scoring ───────────────────────────────────────────────


def _build_scoring_prompt(
    translations: dict[str, dict[str, str]],
    source_messages: list[str],
    target_languages: dict[str, str],
) -> str:
    """Build a prompt that asks the AI to rate translations 0-100."""
    translations_json = json.dumps(translations, ensure_ascii=False, indent=2)
    langs_json = json.dumps(target_languages, ensure_ascii=False, indent=2)

    example_msg = source_messages[0] if source_messages else "Hello"
    example_langs = list(target_languages.keys())[:2] or ["es", "fr"]
    example = json.dumps(
        {example_msg: {lc: 85 for lc in example_langs}},
        ensure_ascii=False,
        indent=2,
    )

    return (
        "You are a professional translation quality assessor.\n\n"
        "For each translation below, provide a single integer score from 0 to 100 "
        "based on the following criteria:\n"
        "- Accuracy: Does the translation convey the same meaning as the source?\n"
        "- Naturalness: Does it sound fluent to a native speaker?\n"
        "- Placeholder preservation: Are all placeholders, variables, and format "
        "specifiers kept intact?\n\n"
        "Score guidelines:\n"
        "  90-100 = Excellent, publish-ready\n"
        "  70-89  = Good, minor improvements possible\n"
        "  50-69  = Acceptable but needs review\n"
        "  0-49   = Poor, retranslation recommended\n\n"
        f"Source messages and their translations:\n{translations_json}\n\n"
        f"Target languages:\n{langs_json}\n\n"
        f"Expected output format (scores only, no explanations):\n{example}\n\n"
        "Return ONLY a JSON object mapping each source message to an object "
        "mapping each language code to its integer score."
    )


def score_translations(
    translator: "BaseTranslator",
    translations: dict[str, dict[str, str]],
    source_messages: list[str],
    target_languages: dict[str, str],
) -> dict[str, dict[str, float]]:
    """Send a second API call to score translations 0-100.

    Returns ``{source_message: {lang_code: score}}``.  On failure, returns
    an empty dict rather than ``None`` so callers can always iterate.
    """
    prompt = _build_scoring_prompt(translations, source_messages, target_languages)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = translator._call_api(prompt)
            if raw is None:
                log.warning(
                    "Scoring attempt %d/%d: empty response.", attempt, MAX_RETRIES
                )
            else:
                text = _FENCE_RE.sub("", raw).strip()
                data = json.loads(text)
                if isinstance(data, dict):
                    scores: dict[str, dict[str, float]] = {}
                    for msg in source_messages:
                        entry = data.get(msg)
                        if not isinstance(entry, dict):
                            continue
                        msg_scores: dict[str, float] = {}
                        for lc in target_languages:
                            val = entry.get(lc)
                            if isinstance(val, (int, float)) and 0 <= val <= 100:
                                msg_scores[lc] = float(val)
                        if msg_scores:
                            scores[msg] = msg_scores
                    if scores:
                        return scores
                log.warning(
                    "Scoring attempt %d/%d: invalid structure.", attempt, MAX_RETRIES
                )
        except Exception as exc:
            log.warning("Scoring attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)

        if attempt < MAX_RETRIES:
            wait = RETRY_BACKOFF**attempt
            log.debug("Retrying scoring in %ds...", wait)
            time.sleep(wait)

    log.error("All %d scoring attempts failed.", MAX_RETRIES)
    return {}


# ── Cost estimation ──────────────────────────────────────────────────


def estimate_tokens(
    messages: list[str],
    target_languages: dict[str, str],
) -> dict[str, float]:
    """Estimate cost (USD) per provider for translating *messages*.

    The estimate is rough: it counts characters in the prompt and assumes
    the output will be ~1.5x the source text (translated into N languages).

    Returns ``{provider_name: estimated_cost_usd}``.
    """
    # Build a representative prompt to measure input size
    dummy_prompt = build_prompt(messages, target_languages)
    input_chars = len(dummy_prompt)
    input_tokens = input_chars / _CHARS_PER_TOKEN

    # Estimate output: each message translated into each language, plus JSON
    # overhead.  Rough multiplier: 1.5x source length per language.
    source_chars = sum(len(m) for m in messages)
    num_langs = len(target_languages)
    output_chars = source_chars * num_langs * 1.5 + num_langs * len(messages) * 20  # JSON keys/braces
    output_tokens = output_chars / _CHARS_PER_TOKEN

    costs: dict[str, float] = {}
    for provider, rates in _TOKEN_COSTS.items():
        cost = (input_tokens / 1000) * rates["input"] + (output_tokens / 1000) * rates["output"]
        costs[provider] = round(cost, 6)

    return costs


# ── Base class ────────────────────────────────────────────────────────


class BaseTranslator(ABC):
    """Abstract base for all AI translation providers."""

    name: str = "base"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    @abstractmethod
    def validate_key(self) -> bool:
        """Make a minimal API call to verify the key works."""

    @abstractmethod
    def _call_api(self, prompt: str) -> str | None:
        """Send the translation prompt and return raw text."""

    def translate_batch(
        self,
        messages: list[str],
        target_languages: dict[str, str],
        platform: str = "",
        glossary: dict[str, dict[str, str]] | None = None,
        context: str | None = None,
        fallback_translators: list["BaseTranslator"] | None = None,
    ) -> dict[str, dict[str, str]] | None:
        """Translate a batch with retry logic and optional provider failover.

        Parameters
        ----------
        messages:
            Source strings to translate.
        target_languages:
            ``{lang_code: lang_name}`` mapping.
        platform:
            Target platform key for rule injection.
        glossary:
            Optional mandatory terminology dictionary.
        context:
            Optional project context string.
        fallback_translators:
            Optional list of backup translators to try if *this* translator
            exhausts all retries.
        """
        prompt = build_prompt(
            messages,
            target_languages,
            platform,
            glossary=glossary,
            context=context,
        )

        # -- Try primary translator ----------------------------------------
        result = self._attempt_translate(prompt, messages, target_languages)
        if result is not None:
            return result

        # -- Try fallback translators if provided --------------------------
        if fallback_translators:
            for fb in fallback_translators:
                log.info(
                    "Primary translator '%s' failed; trying fallback '%s'.",
                    self.name,
                    fb.name,
                )
                # Rebuild prompt via the same function (provider-agnostic)
                fb_result = fb._attempt_translate(
                    prompt, messages, target_languages
                )
                if fb_result is not None:
                    return fb_result
            log.error(
                "All fallback translators also failed for batch of %d messages.",
                len(messages),
            )

        return None

    # ── internal retry loop ──────────────────────────────────────────

    def _attempt_translate(
        self,
        prompt: str,
        messages: list[str],
        target_languages: dict[str, str],
    ) -> dict[str, dict[str, str]] | None:
        """Run the retry loop for a single translator.

        Returns parsed translations on success or ``None`` after all retries
        are exhausted.
        """
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                raw = self._call_api(prompt)
                if raw is None:
                    log.warning("Attempt %d/%d: empty response.", attempt, MAX_RETRIES)
                else:
                    result = parse_response(raw, messages, target_languages)
                    if result is not None:
                        return result
                    log.warning("Attempt %d/%d: invalid JSON structure.", attempt, MAX_RETRIES)
            except Exception as exc:
                log.warning("Attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)

            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF**attempt
                log.debug("Retrying in %ds...", wait)
                time.sleep(wait)

        log.error("All %d attempts failed for batch of %d messages.", MAX_RETRIES, len(messages))
        return None
