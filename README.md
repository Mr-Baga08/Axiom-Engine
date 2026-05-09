# AI-Insights: Entertainment Analytics Agent

An enterprise-grade AI analytics platform for the entertainment industry.
Natural-language queries answered by an LLM agent backed by PostgreSQL, pgvector RAG, and a Go SSE gateway.

---

## Architecture

```
Browser
  │
  ▼
Go SSE Gateway (:8080)
  │  Per-IP rate limiting (Redis)
  │  Circuit breaker (3 failures → 60s open)
  │  Streams token-by-token to browser via SSE
  │
  ▼
Python FastAPI (:8000)
  │  JWT auth (RS256/HS256)  ·  RBAC scopes
  │  Input sanitisation      ·  Audit logging
  │
  ├──► DIN-SQL → PostgreSQL
  │      4-stage text-to-SQL pipeline
  │      RLS enforces executive-only marketing_spend
  │
  ├──► CRAG → pgvector (document_chunks)
  │      Corrective RAG with HMAC tamper detection
  │      GraphRAG fallback when chunks < 2
  │
  ├──► PAL Sandbox (subprocess)
  │      Restricted Python for metric computation
  │
  └──► Chart Generator (Recharts JSON)
         Declarative chart specs for the frontend

Infrastructure
──────────────
  PostgreSQL (pgvector) ← Go ingestion worker (CopyFrom bulk load)
  Redis                 ← Rate limiting · RAGAS rolling window
  Vault (KV-v2)         ← Secrets at runtime (Anthropic key, JWT, HMAC)

Observability
─────────────
  LangFuse    ← Trace every span; token cost per call
  Phoenix     ← RAG evaluation UI (http://localhost:6006)
  RAGAS       ← context_relevance + faithfulness scored via Gemini Flash
  structlog   ← JSON logs with trace_id on every line
  Slack alert ← context_relevance < 0.7 triggers watchdog alert
```

---

## Prerequisites

- Docker ≥ 24
- Docker Compose ≥ 2.24
- `make`
- Python 3.12 (for local data generation only)

---

## Quick Start (Development)

```bash
git clone <repo-url> && cd ai-insights/axiom-engine
cp .env.example .env          # fill in ANTHROPIC_API_KEY at minimum
make generate-data
make dev                      # docker compose up -d
make ingest-csv
make ingest-pdfs
# Open http://localhost:3000
```

---

## Production Deployment

```bash
make secrets-init              # creates infra/secrets/*.txt with placeholders
# Edit infra/secrets/*.txt with real values (see infra/secrets/README.md)
make prod-up
make vault-init
make ingest-csv
make ingest-pdfs
make compile-dspy              # optional: recompile DSPy with 50 labelled examples
```

---

## Running Tests

```bash
make test                      # unit tests (requires stack running)
make test-integration          # full integration tests (requires stack running)
```

---

## Assumption Log

- GraphRAG index is pre-built; building takes ~10 min on first run via `make build-graphrag`.
- DSPy `examples.json` requires 50 human-labelled examples; 50 synthetic ones are included as a baseline.
- Vault runs with file storage (single-node); migrate to Consul or Raft backend for HA production deployments.
- The SSE gateway does not independently verify JWTs — it forwards auth headers to the Python API, which validates tokens on every request.
- PAL sandbox uses subprocess isolation with `RLIMIT_CPU` / `RLIMIT_AS`; Docker `--security-opt seccomp=unconfined` is **not** needed and not recommended.
- `marketing_spend` table is executive-only via PostgreSQL RLS; analyst queries return zero rows without an error.

---

## Known Limitations

- GraphRAG requires an OpenAI-compatible embedding endpoint; set `ANTHROPIC_API_BASE` to a compatible proxy if using Anthropic-only infrastructure.
- RAGAS faithfulness metric benefits from ground-truth labels in production; the current evaluation uses auto-evaluation via Gemini Flash which may not match human judgement for all query types.
- The Go ingestion worker uses `pgx.CopyFrom` which requires the role to have `COPY` privilege or superuser; ensure `app_user` has been granted appropriate permissions post-init.
- SSE gateway circuit breaker state is in-process memory — restarting the container resets it.
- Vault token rotation is not automated; run `make vault-init` manually after token rotation.

---

## Baseline RAGAS Scores

| Query Type | context_relevance | faithfulness |
|---|---|---|
| SQL questions | N/A | 0.81 |
| PDF RAG questions | 0.74 | 0.79 |
| Mixed (SQL+RAG) | 0.71 | 0.77 |
| Target threshold | >0.70 | >0.75 |
