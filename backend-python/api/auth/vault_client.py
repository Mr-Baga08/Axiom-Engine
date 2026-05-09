"""
Fetches secrets from HashiCorp Vault KV-v2.
Falls back to environment variables if VAULT_ADDR is not set,
preserving dev-mode compatibility without any config change.
"""

import os
from functools import lru_cache

import httpx

VAULT_ADDR = os.getenv("VAULT_ADDR", "")
VAULT_TOKEN = os.getenv("VAULT_TOKEN", "")
SECRET_PATH = os.getenv("VAULT_SECRET_PATH", "secret/data/ai-insights")


@lru_cache(maxsize=1)
def get_secrets() -> dict:
    """
    Return all application secrets as a flat dict.
    Reads from Vault when VAULT_ADDR + VAULT_TOKEN are set;
    otherwise falls back to environment variables.
    The result is cached for the process lifetime — restart to refresh.
    """
    if not VAULT_ADDR or not VAULT_TOKEN:
        return {
            "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
            "langfuse_secret":   os.getenv("LANGFUSE_SECRET_KEY", ""),
            "langfuse_public":   os.getenv("LANGFUSE_PUBLIC_KEY", ""),
            "jwt_secret":        os.getenv("JWT_SECRET", "dev-secret"),
            "db_password":       os.getenv("DB_PASSWORD", "changeme"),
            "chunk_hmac_secret": os.getenv("CHUNK_HMAC_SECRET", "dev-hmac"),
            "slack_webhook":     os.getenv("SLACK_WEBHOOK_URL", ""),
        }

    resp = httpx.get(
        f"{VAULT_ADDR}/v1/{SECRET_PATH}",
        headers={"X-Vault-Token": VAULT_TOKEN},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()["data"]["data"]


def get_secret(key: str) -> str:
    """Return a single secret by key, or empty string if not found."""
    return get_secrets().get(key, "")
