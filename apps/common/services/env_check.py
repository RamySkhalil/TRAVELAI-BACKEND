"""Environment and readiness checks that never expose secret values.

These helpers report presence/completeness flags and connectivity status only.
They must never return or log actual secret values (DATABASE_URL, R2 keys,
OpenAI keys, Django secret key, etc.).
"""
from __future__ import annotations

import os

from django.conf import settings
from django.db import connections

DEFAULT_SECRET_KEY = "dev-only-change-me"

OPENAI_REQUIRED_ENV_NAMES = ("OPENAI_API_KEY",)


def check_database_connection() -> bool:
    """Return True when the default database accepts a trivial query."""
    try:
        connection = connections["default"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Exception:  # noqa: BLE001 - connectivity check is best-effort and must not raise
        return False


def r2_missing_env_names(env=os.environ) -> list[str]:
    """Return the Cloudflare R2 variable names that are not configured."""
    return [name for name in settings.CLOUDFLARE_R2_REQUIRED_ENV_NAMES if not env.get(name)]


def openai_missing_env_names(env=os.environ) -> list[str]:
    """Return the OpenAI variable names that are not configured."""
    return [name for name in OPENAI_REQUIRED_ENV_NAMES if not env.get(name)]


def secret_key_is_default() -> bool:
    """Return True when the Django secret key is still the development default."""
    return settings.SECRET_KEY == DEFAULT_SECRET_KEY


def storage_status(env=os.environ) -> dict:
    """Describe storage configuration without revealing any credential values."""
    r2_enabled = bool(getattr(settings, "CLOUDFLARE_R2_ENABLED", False))
    return {
        "r2_enabled": r2_enabled,
        "backend": "r2" if r2_enabled else "local",
        "r2_missing_env_names": r2_missing_env_names(env),
    }


def collect_environment_report(env=os.environ, *, check_database: bool = True) -> dict:
    """Collect a redacted environment readiness report.

    The returned structure contains only booleans, names, and status strings.
    No secret values are included.
    """
    storage = storage_status(env)
    openai_missing = openai_missing_env_names(env)
    allowed_hosts = [host for host in settings.ALLOWED_HOSTS if host]
    cors_origins = [origin for origin in getattr(settings, "CORS_ALLOWED_ORIGINS", []) if origin]

    report = {
        "debug": bool(settings.DEBUG),
        "database_url_enabled": bool(getattr(settings, "DATABASE_URL_ENABLED", False)),
        "storage": storage,
        "openai_configured": not openai_missing,
        "openai_missing_env_names": openai_missing,
        "allowed_hosts_configured": bool(allowed_hosts),
        "allowed_hosts_count": len(allowed_hosts),
        "cors_configured": bool(cors_origins),
        "cors_origin_count": len(cors_origins),
        "secret_key_is_default": secret_key_is_default(),
    }
    if check_database:
        report["database_connected"] = check_database_connection()
    return report
