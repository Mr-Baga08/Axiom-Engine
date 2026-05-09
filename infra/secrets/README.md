# Infrastructure Secrets

Each file in this directory is mounted into containers as a Docker secret at `/run/secrets/<filename>`.
These files are **gitignored**. Create them locally before booting the application layer.

Run `make secrets-init` to generate placeholder files, then replace them with real values.

## Files Required

| File | Content | Example |
|------|---------|---------|
| `db_user.txt` | PostgreSQL username | `axiom_admin` |
| `db_pass.txt` | PostgreSQL password | `<strong-password>` |
| `redis_pass.txt` | Redis password | `<strong-password>` |
| `vault_token.txt` | Vault scoped service token | `<vault-token-from-vault-operator-init>` |

*Note: Full connection URLs (DSNs) are constructed internally by the application using these secrets combined with environment variables (e.g., `DB_HOST`, `DB_PORT`). Do not store full URLs as secrets.*

## Security Notes

- Never commit `*.txt` files — they are listed in `.gitignore`.
- Ensure secret files do not contain trailing newlines (use `printf`, not `echo`).
- Vault token should be a narrowly-scoped service token, not the root token.
- Keep file permissions restrictive (`chmod 600`).

## Creating Secrets (Production)

```bash
# 1. Generate core secrets without trailing newlines
printf "app" > infra/secrets/db_user.txt
openssl rand -hex 16 | tr -d '\n' > infra/secrets/db_pass.txt
openssl rand -hex 16 | tr -d '\n' > infra/secrets/redis_pass.txt

# 2. Restrict permissions
chmod 600 infra/secrets/*.txt

# 3. Boot infrastructure layer (DBs, Vault)
make infra-up

# 4. Initialize Vault and capture the token
# (Assuming make vault-init writes to vault_token.txt without newlines)
make vault-init

# 5. Boot application layer
make app-up