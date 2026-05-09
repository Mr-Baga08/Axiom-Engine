#!/bin/sh
# One-time Vault initialisation script.
# Run via: make vault-init
# Requires VAULT_TOKEN to be set (root token from vault operator init).
set -e

export VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"

vault secrets enable -path=secret kv-v2

vault kv put secret/ai-insights \
  anthropic_api_key="${ANTHROPIC_API_KEY}" \
  langfuse_secret="${LANGFUSE_SECRET_KEY}" \
  langfuse_public="${LANGFUSE_PUBLIC_KEY}" \
  jwt_secret="${JWT_SECRET}" \
  db_password="changeme" \
  chunk_hmac_secret="${CHUNK_HMAC_SECRET}" \
  slack_webhook="${SLACK_WEBHOOK_URL}"

echo "Vault initialised — secrets stored at secret/ai-insights"
