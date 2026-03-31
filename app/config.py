"""
Centralized configuration (backed by .env).

Keep it intentionally simple so interview discussions stay focused on the rule engine.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from repo root .env (if present).
_ROOT_DIR = Path(__file__).resolve().parent.parent
_ENV_PATH = _ROOT_DIR / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _get_str(name: str, default: str) -> str:
    return os.getenv(name, default)


# Database
DEBUGGER_DB_PATH = Path(_get_str("DEBUGGER_DB_PATH", "debugger.db"))

# Logging
LOG_LEVEL = _get_str("LOG_LEVEL", "INFO")

# Cache (in-memory, per-process)
CACHE_MAX_SIZE = _get_int("CACHE_MAX_SIZE", 100)
CACHE_TTL_MINUTES = _get_int("CACHE_TTL_MINUTES", 30)

# DB deduplication window
DB_DEDUP_MINUTES = _get_int("DB_DEDUP_MINUTES", 5)

# Rate limiting
# slowapi expects strings like "10/minute"
DEBUG_RATE_LIMIT = _get_str("DEBUG_RATE_LIMIT", "10/minute")

