"""Environment / .env file management for API keys.

Production-grade .env handling:
  1. Scans ALL .env files from cwd up to git root
  2. Loads every .env found (closest to cwd wins on conflicts)
  3. Also loads keys already in os.environ (always highest priority)
  4. Saves new keys to cwd/.env (project root)
  5. Shows the user which .env files were loaded
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

from dotenv import dotenv_values, load_dotenv, set_key

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


def _find_all_env_files() -> list[Path]:
    """Find ALL .env files from cwd up to git root.

    Returns list sorted deepest-first (closest to cwd first).
    Also scans immediate subdirectories of cwd for .env files,
    because Django projects often have the .env inside the app package.
    """
    cwd = Path(os.getcwd()).resolve()
    found: list[Path] = []

    # 1. Walk UP from cwd to git root
    for parent in (cwd, *cwd.parents):
        candidate = parent / ".env"
        if candidate.is_file():
            found.append(candidate)
        if (parent / ".git").exists():
            break

    # 2. Also check immediate subdirectories of cwd
    #    (catches Django patterns like project_root/MyApp/.env)
    if cwd.is_dir():
        try:
            for child in cwd.iterdir():
                if child.is_dir() and child.name not in _SKIP_SUBDIRS:
                    sub_env = child / ".env"
                    if sub_env.is_file() and sub_env not in found:
                        found.append(sub_env)
        except PermissionError:
            pass

    return found


# Directories to NOT scan for .env in subdirectory search
_SKIP_SUBDIRS = frozenset({
    "venv", ".venv", "env", "node_modules", ".git",
    "__pycache__", "build", ".dart_tool", ".gradle", ".tox",
    "dist", "htmlcov", ".mypy_cache", ".idea", ".vscode",
    "static", "media", "migrations", "locale", "templates",
    "staticfiles", "collected_static", "Pods", "DerivedData",
})


def _project_env_path() -> Path:
    """Return the .env path where new keys should be SAVED.

    This is always cwd/.env (the project root).
    """
    return Path(os.getcwd()).resolve() / ".env"


# The chosen .env file for this session (set by load_env_file)
_chosen_env: Path | None = None


def _has_translation_keys(path: Path) -> str:
    """Return a summary of which translation API keys a .env file has."""
    try:
        vals = dotenv_values(path)
    except Exception:
        return ""
    found = []
    for key, label in ALL_ENV_KEYS.items():
        val = vals.get(key, "")
        if val and val.strip():
            found.append(label.split("(")[0].strip().split(" ")[0])  # "Claude", "OpenAI", etc.
    if found:
        return f"has keys: {', '.join(found)}"
    return "no translation keys"


def load_env_file(project_root: Path | None = None) -> Path | None:
    """Find .env files, prompt user if multiple, load the chosen one.

    The choice is saved per-project so the user is not asked again.
    Returns the chosen .env path (or None if no .env found).
    """
    global _chosen_env

    env_files = _find_all_env_files()

    if not env_files:
        _chosen_env = None
        return None

    if len(env_files) == 1:
        chosen = env_files[0]
    else:
        from ai_translate.cli.ui import prompt_choose_path
        chosen = prompt_choose_path(
            ".env file",
            env_files,
            detail_fn=_has_translation_keys,
            pref_key="env_file",
            project_root=project_root,
        )

    _chosen_env = chosen

    try:
        load_dotenv(chosen, override=True)
        log.debug("Loaded .env: %s", chosen)
    except Exception as exc:
        log.warning("Failed to load %s: %s", chosen, exc)

    return chosen


def ensure_env_exists() -> Path:
    """Return the chosen .env, or create one at project root if none exists."""
    global _chosen_env

    if _chosen_env and _chosen_env.is_file():
        return _chosen_env

    env_files = _find_all_env_files()
    if env_files:
        _chosen_env = env_files[0]
        return _chosen_env

    path = _project_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SCAFFOLD, encoding="utf-8")
    _chosen_env = path
    return path


# ── Key I/O ───────────────────────────────────────────────────────────


def get_env_status() -> dict[str, str | None]:
    """Return ``{env_var: value_or_None}`` for every provider key.

    Reads from os.environ which was populated by load_all_env_files().
    This catches keys from ALL .env files + real environment variables.
    """
    result: dict[str, str | None] = {}
    for key in ALL_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            val = val.strip()
            if not val:
                val = None
        result[key] = val
    return result


def load_key(env_var: str) -> str | None:
    """Load a single key — os.environ first (populated by load_all_env_files)."""
    val = os.environ.get(env_var)
    if val and val.strip():
        return val.strip()
    return None


def save_key(env_var: str, value: str) -> None:
    """Persist *value* for *env_var* into the project root .env file."""
    value = value.strip()
    if not value:
        return
    if "\n" in value or "\r" in value:
        return

    # Always set in os.environ immediately (available for this run)
    os.environ[env_var] = value

    try:
        # Save to the user's chosen .env, or project root if none chosen
        path = _chosen_env if _chosen_env and _chosen_env.is_file() else _project_env_path()

        # If file doesn't exist, create with just the header
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            _HEADER = "# — Auto-Translation Provider API Keys —\n"
            path.write_text(_HEADER, encoding="utf-8")

        content = path.read_text(errors="ignore")
        _HEADER_MARKER = "# — Auto-Translation Provider API Keys —"

        # Add header section if this .env has no translation section yet
        if _HEADER_MARKER not in content:
            lines = [f"\n{_HEADER_MARKER}"]
            for key, label in ALL_ENV_KEYS.items():
                if key != env_var:
                    lines.append(f"# {key}=          # {label}")
            with path.open("a") as fh:
                fh.write("\n".join(lines) + "\n")

        # Always use set_key — it handles create/update correctly
        set_key(str(path), env_var, value)

    except PermissionError:
        log.warning(
            "Cannot write to %s (read-only). Key set for this session only.",
            path,
        )
    except Exception as exc:
        log.warning("Failed to save key to .env: %s", exc)
