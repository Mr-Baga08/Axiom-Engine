.PHONY: dev generate-data migrate test ingest-csv ingest-pdfs compile-dspy \
        vault-init build-graphrag prod-up prod-down test-integration secrets-init

dev:
	docker compose up -d

generate-data:
	python data/generate_data.py

migrate:
	docker compose exec api alembic upgrade head

test:
	docker compose exec api pytest tests/ -v --tb=short

ingest-csv:
	docker compose exec api python -m ingestion.ingest_all

ingest-pdfs:
	docker compose exec api python -m ingestion.embed_pdfs

compile-dspy:
	docker compose exec api python -m pipeline.compile_dspy

vault-init:
	docker compose -f docker-compose.prod.yml exec vault sh /vault/config/init.sh

build-graphrag:
	docker compose exec api python -m graphrag.index --root /data/graphrag

prod-up:
	docker compose -f docker-compose.prod.yml up -d

prod-down:
	docker compose -f docker-compose.prod.yml down -v

test-integration:
	docker compose exec api pytest tests/test_integration.py -v

secrets-init:
	mkdir -p infra/secrets
	echo "app"         > infra/secrets/db_user.txt
	echo "changeme"    > infra/secrets/db_pass.txt
	echo "postgres://app:changeme@postgres:5432/insights" > infra/secrets/db_url.txt
	echo "changeme"    > infra/secrets/redis_pass.txt
	echo "redis://:changeme@redis:6379/0" > infra/secrets/redis_url.txt
	echo "dev-vault-root-token" > infra/secrets/vault_token.txt
	@echo "Secrets written — replace values before production deploy"
