.PHONY: dev build generate-data generate-examples compile-dspy \
        ingest-schema ingest-csv ingest-pdfs \
        test test-integration \
        prod-up prod-down vault-init build-graphrag secrets-init

# ── Development ────────────────────────────────────────────────────────────────

dev:
	docker compose up -d

build:
	docker compose up -d --build

generate-data:
	python scripts/generate_data.py

# ── DSPy ───────────────────────────────────────────────────────────────────────

generate-examples:
	# Generate 50 Q&A training examples from the live DuckDB database.
	# Run this after the stack is up and data is loaded.
	python3 scripts/generate_examples.py

compile-dspy:
	# Run BootstrapFewShot compilation and save to data/dspy_compiled.json.
	# Requires generate-examples to have been run first.
	# Restart api-python after this to pick up the new compiled prompt.
	docker compose exec \
		-e EXAMPLES_PATH=/repo/data/dspy_examples.json \
		-e DSPY_OUTPUT_PATH=/repo/data/dspy_compiled.json \
		api-python python -m pipeline.compile_dspy
	docker compose restart api-python

# ── Data ingestion ─────────────────────────────────────────────────────────────

ingest-schema:
	# Apply PostgreSQL schema (first time only — run if tables do not exist).
	docker exec -i axiom-postgres psql -U axiom_admin -d axiom_db < infra/postgres/init.sql

ingest-csv:
	# Restart the Go ingestion worker to reload CSVs into PostgreSQL.
	docker compose up -d --force-recreate go-ingestion

ingest-pdfs:
	# Upload PDFs via the ingest API (uses the UI upload button in the Insights panel).
	@echo "Upload PDFs via the Insights panel in the browser (localhost:3000)"
	@echo "Or call: curl -X POST http://localhost:8000/ingest/pdf -H 'Authorization: Bearer <token>' -F 'file=@your.pdf'"

# ── Testing ────────────────────────────────────────────────────────────────────

test:
	docker compose exec api-python pytest tests/ -v --tb=short

test-integration:
	docker compose exec api-python pytest tests/test_integration.py -v

# ── Production ─────────────────────────────────────────────────────────────────

prod-up:
	docker compose -f docker-compose.prod.yml up -d --build

prod-down:
	docker compose -f docker-compose.prod.yml down -v

vault-init:
	docker compose -f docker-compose.prod.yml exec vault sh /vault/config/init.sh

build-graphrag:
	docker compose exec api-python python -m graphrag.index --root /data/graphrag

secrets-init:
	mkdir -p infra/secrets
	@python3 -c "import secrets; print(secrets.token_hex(32))" > infra/secrets/jwt_secret.txt
	@python3 -c "import secrets; print(secrets.token_hex(32))" > infra/secrets/hmac_secret.txt
	@echo "axiom_admin"                                         > infra/secrets/db_user.txt
	@echo "changeme"                                            > infra/secrets/db_pass.txt
	@echo "postgresql://axiom_admin:changeme@postgres:5432/axiom_db" > infra/secrets/db_url.txt
	@echo "dev-vault-root-token"                                > infra/secrets/vault_token.txt
	@echo "Secrets written to infra/secrets/"
	@echo "Edit db_pass, db_url, and vault_token with real values before running prod-up"
