"""Centralized configuration for ai-translate."""

# ── AI Provider Defaults ──
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 8192

# ── Retry Configuration ──
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds, exponential: 2^attempt

# ── Model IDs ──
CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_VALIDATION_MODEL = "claude-sonnet-4-20250514"
OPENAI_MODEL = "gpt-4o"
OPENAI_VALIDATION_MODEL = "gpt-4o-mini"
GEMINI_MODEL = "gemini-2.5-flash"
MISTRAL_MODEL = "mistral-large-latest"
OPENROUTER_DEFAULT_MODEL = "anthropic/claude-sonnet-4"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ── Batch Sizing ──
BATCH_THRESHOLDS = [
    (10, None),    # <= 10 strings: all in one batch
    (50, 15),      # <= 50: 15 per batch
    (200, 20),     # <= 200: 20 per batch
    (float('inf'), 25),  # > 200: 25 per batch
]

# ── Cache ──
CACHE_FILENAME = ".translation_cache.json"

# ── Glossary Auto-Discovery ──
GLOSSARY_FILENAMES = [
    ".ai-translate-glossary.json",
    "glossary.json",
    "locale/glossary.json",
    "translations/glossary.json",
]

# ── Token Estimation (for --estimate) ──
CHARS_PER_TOKEN = 4
PROMPT_OVERHEAD_TOKENS = 800
OUTPUT_MULTIPLIER = 1.5  # output is ~1.5x input for translations

# ── Provider Pricing (USD per 1M tokens) ──
PROVIDER_PRICING = {
    "anthropic": {"input": 3.0, "output": 15.0, "label": "Claude (Anthropic)", "time": "~12s", "quality": "Excellent"},
    "openai": {"input": 2.5, "output": 10.0, "label": "OpenAI GPT-4o", "time": "~15s", "quality": "High"},
    "google": {"input": 0.15, "output": 0.60, "label": "Google Gemini", "time": "~6s", "quality": "High"},
    "deepseek": {"input": 0.14, "output": 0.28, "label": "DeepSeek (OpenRouter)", "time": "~8s", "quality": "Good"},
}

# ── Lock File ──
import tempfile
LOCK_FILENAME = ".ai_translate.lock"
LOCK_DIR = tempfile.gettempdir()  # Cross-platform: /tmp on Linux, AppData\Local\Temp on Windows

# ── Directories to Skip ──
SKIP_DIRS = frozenset({
    "__pycache__", ".git", ".hg", ".svn",
    "node_modules", ".tox", ".nox", ".mypy_cache", ".ruff_cache",
    "venv", ".venv", "env", ".env",
    "htmlcov", ".pytest_cache", "dist", "egg-info",
    ".dart_tool", ".fvm", ".pub-cache", ".gradle",
    ".idea", ".vscode", "Pods", "DerivedData",
    "build", ".build", "intermediates", "generated",
    "migrations", "static", "media", "locale",
    "staticfiles", "collected_static",
})
