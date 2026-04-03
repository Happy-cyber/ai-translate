"""Environment / .env file management for API keys."""

from __future__ import annotations

import functools
import os
from pathlib import Path

from dotenv import dotenv_values, set_key

# ── Provider ↔ env-var mapping ────────────────────────────────────────

PROVIDER_ENV_KEYS: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GOOGLE_GEMINI_KEY",
    "mistral": "MISTRAL_API_KEY",
}

ALL_ENV_KEYS: dict[str, str] = {
    "ANTHROPIC_API_KEY": "Claude (Anthropic)",
    "OPENAI_API_KEY": "OpenAI GPT",
    "OPENROUTER_API_KEY": "OpenRouter (100+ models)",
    "GOOGLE_GEMINI_KEY": "Google Gemini",
    "MISTRAL_API_KEY": "Mistral AI",
}

API_KEY_URLS: dict[str, str] = {
    "claude": "https://console.anthropic.com/settings/keys",
    "openai": "https://platform.openai.com/api-keys",
    "gemini": "https://aistudio.google.com/apikey",
    "openrouter": "https://openrouter.ai/settings/keys",
    "mistral": "https://console.mistral.ai/api-keys",
}

_SKIP_DIRS = frozenset({
    "venv", ".venv", "env", ".env", "node_modules", ".git",
    "__pycache__", "build", ".dart_tool", ".gradle", ".tox",
    "dist", "htmlcov", ".mypy_cache",
})

_SCAFFOLD = """\
# ──────────────────────────────────────────────────────────────
#  AI Translate — API Key Configuration
#  Set at least ONE key to start translating.
# ──────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY=
OPENAI_API_KEY=
OPENROUTER_API_KEY=
GOOGLE_GEMINI_KEY=
MISTRAL_API_KEY=
"""


# ── .env discovery ────────────────────────────────────────────────────


@functools.lru_cache(maxsize=1)
def _env_path() -> Path:
    """Walk up from cwd looking for an existing .env, or return cwd/.env."""
    cwd = Path(os.getcwd()).resolve()
    for parent in (cwd, *cwd.parents):
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
        if (parent / ".git").exists():
            break
    return cwd / ".env"


def ensure_env_exists() -> Path:
    """Create .env with scaffold if it does not exist. Return its path."""
    path = _env_path()
    if path.is_file():
        # Ensure all keys have at least a placeholder line
        content = path.read_text(errors="ignore")
        additions: list[str] = []
        for key in ALL_ENV_KEYS:
            if key not in content:
                additions.append(f"{key}=\n")
        if additions:
            with path.open("a") as fh:
                fh.write("\n" + "".join(additions))
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SCAFFOLD, encoding="utf-8")
    return path


# ── Key I/O ───────────────────────────────────────────────────────────


def get_env_status() -> dict[str, str | None]:
    """Return ``{env_var: value_or_None}`` for every provider key."""
    path = _env_path()
    dotenv_vals = dotenv_values(path) if path.is_file() else {}
    result: dict[str, str | None] = {}
    for key in ALL_ENV_KEYS:
        val = os.environ.get(key) or dotenv_vals.get(key) or None
        if val is not None:
            val = val.strip()
            if not val:
                val = None
        result[key] = val
    return result


def load_key(env_var: str) -> str | None:
    """Load a single key from env or .env file."""
    val = os.environ.get(env_var)
    if val and val.strip():
        return val.strip()
    path = _env_path()
    if path.is_file():
        vals = dotenv_values(path)
        val = vals.get(env_var)
        if val and val.strip():
            return val.strip()
    return None


def save_key(env_var: str, value: str) -> None:
    """Persist *value* for *env_var* into the .env file."""
    value = value.strip()
    if not value:
        return
    if "\n" in value or "\r" in value:
        return
    path = ensure_env_exists()
    success, *_ = set_key(str(path), env_var, value)
    if success:
        os.environ[env_var] = value
