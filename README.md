# Axiom Engine

An AI analytics assistant for the entertainment industry. Users ask questions in natural language; the system queries a structured database, retrieves relevant documents, runs sandboxed computations, and streams a sourced answer back through a Go SSE gateway.

Built as the capstone project for a quantitative engineering role. The design targets correctness and security first; LLM cost and latency second.

---

## Architecture

```
Browser (Next.js :3000)
       |
       | HTTPS — AI SDK UI Message Stream
       v
  Next.js API routes
       |
       | HTTP POST — Bearer token forwarded
       v
Go SSE Gateway (:8080)
  - Per-IP rate limiting via Redis token bucket
  - Circuit breaker (3 failures / 30 s -> 503 for 60 s)
  - Keep-alive ping every 15 s (SSE comment)
  - Forwards to Python /internal/query
       |
       | HTTP -> Python FastAPI (:8000)
       v
Agent loop (Gemini 2.5 Flash Lite / Claude Sonnet)
  - JWT RS256 auth + RBAC scope enforcement on every request
  - Up to 6 tool-call rounds before end_turn
       |
       |-- query_sql --->  DIN-SQL 4-stage pipeline -> DuckDB / PostgreSQL
       |                   SQL guard (rejects INSERT/UPDATE/DELETE/DROP)
       |                   PostgreSQL RLS: marketing_spend -> executive only
       |
       |-- retrieve_docs -> CRAG (Corrective RAG)
       |                    HMAC tamper check on every chunk
       |                    LightRAG graph index + ChromaDB vector store
       |                    Presidio PII scrub before LLM context injection
       |
       |-- compute_metric -> PAL sandbox (subprocess, 5 s timeout, 256 MB limit)
       |                     Static analysis bans import/exec/eval/open
       |
       |-- generate_chart -> Recharts JSON spec rendered in the browser

Infrastructure
  PostgreSQL 16 + pgvector  <- Go bulk ingestion worker (pgx CopyFrom, 8 goroutines)
  Redis 7                   <- Rate limiting, conversation history (30-day TTL)
  HashiCorp Vault (opt.)    <- Runtime secrets; falls back to .env when unavailable

Observability (all optional, system runs without them)
  LangFuse    <- Span-level tracing; token cost per call; prompt registry
  Arize Phoenix <- RAG evaluation UI (localhost:6006)
  RAGAS        <- context_relevance + faithfulness scored via Gemini Flash
  structlog    <- JSON structured logs with trace_id on every line
```

---

## Prerequisites

- Docker >= 24 with the Compose plugin (v2)
- 8 GB RAM recommended (sentence-transformers + ChromaDB are memory-heavy)
- A Gemini API key (free tier works): https://aistudio.google.com/apikey

Claude / Anthropic key is optional; set `LLM_PROVIDER=anthropic` and provide `ANTHROPIC_API_KEY` to switch the agent to Claude Sonnet.

---

## Quick Start

```bash
git clone https://github.com/Mr-Baga08/Axiom-Engine.git && cd axiom-engine

# 1. Set your Gemini API key (the only required step)
cp .env.example .env
# Edit .env and set GOOGLE_API_KEY=<your key from https://aistudio.google.com/apikey>

# 2. Build and start everything
docker compose up --build
```

That's it. Open http://localhost:3000 and log in with `analyst_user` / `test`.

The first build takes 10–20 minutes (Python packages + spaCy model). Subsequent starts take under 30 seconds. The Go ingestion service applies the PostgreSQL schema and loads all CSV data automatically on first run.

**Optional: compile DSPy to improve answer quality**

```bash
make compile-dspy          # ~40 Gemini calls, runs in ~5 minutes
docker compose restart api-python
```

---

## Environment Variables

All variables live in `.env`. Required ones are marked with *.

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_API_KEY` * | — | Gemini API key. Used by the agent and RAGAS evaluation. |
| `JWT_SECRET` * | dev value | HS256 signing secret for access tokens. |
| `HMAC_SECRET` * | dev value | HMAC-SHA256 key for document chunk tamper detection. |
| `LLM_PROVIDER` | `gemini` | `gemini` or `anthropic`. Switches the entire agent loop. |
| `GEMINI_AGENT_MODEL` | `gemini-2.5-flash-lite` | Gemini model for the agent. |
| `ANTHROPIC_API_KEY` | — | Required only when `LLM_PROVIDER=anthropic`. |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Claude model. |
| `DB_BACKEND` | `duckdb` | `duckdb` (dev, file-based) or `postgres` (production). |
| `DUCKDB_PATH` | `data/duckdb/analytics.duckdb` | DuckDB file path. |
| `POSTGRES_DSN` | local | asyncpg connection string. Used when `DB_BACKEND=postgres`. |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection string. |
| `LANGFUSE_PUBLIC_KEY` | — | Optional. Tracing disabled if unset. |
| `LANGFUSE_SECRET_KEY` | — | Optional. |
| `GEMINI_EVAL_MODEL` | `gemini-2.5-flash` | Gemini model for RAGAS evaluation (separate from agent). |
| `SLACK_WEBHOOK_URL` | — | Optional. RAGAS watchdog alert destination. |
| `VAULT_ADDR` | — | Optional. HashiCorp Vault address. Falls back to .env if unset. |
| `LOG_LEVEL` | `INFO` | Python log level. |

---

## Default Accounts

The dev user registry is hardcoded in `routers/auth.py`. To override, set `DEV_USERS_JSON` in `.env`.

| Username | Password | Role | Scopes |
|---|---|---|---|
| `analyst_user` | `test` | analyst | query:read, tools:basic |
| `exec_user` | `test` | executive | query:read, tools:basic, docs:executive |
| `viewer_user` | `test` | viewer | query:read |
| `admin_user` | `test` | admin | all |

---

## Adding Documents

Authenticated users with `tools:basic` scope can upload PDFs via the Insights panel in the UI. The pipeline is:

1. PyMuPDF extracts text page by page
2. Presidio scrubs PII (PERSON, EMAIL, PHONE, IP) before any storage
3. tiktoken splits into 512-token overlapping windows
4. HMAC-SHA256 signs each chunk for tamper detection at retrieval time
5. LightRAG builds an entity-relation graph over the chunks
6. Chunks become searchable immediately via the `retrieve_docs` tool

To bulk-ingest a directory of PDFs from the command line:

```python
from pathlib import Path
from ingestion.pdf_loader import load_all_pdfs
from pipeline.lightrag_setup import index_chunks

chunks = load_all_pdfs(Path("data/pdfs"))
index_chunks(chunks)
```

---

## Adding Structured Data

**Option A — CSV drop (DuckDB dev mode)**

Drop a CSV into `data/csv/<table_name>.csv` and restart `api-python`. The lifespan hook auto-loads any CSV whose matching table is empty. Add the table name to `_ALLOWED_TABLES` in `pipeline/schema.py` so DIN-SQL knows about it.

**Option B — PostgreSQL**

```bash
# Create the table
docker exec -i axiom-postgres psql -U axiom_admin -d axiom_db \
  -c "CREATE TABLE my_table (...);"

# Load data
cat my_data.csv | docker exec -i axiom-postgres psql -U axiom_admin -d axiom_db \
  -c "\COPY my_table FROM STDIN CSV HEADER;"
```

Schema is introspected automatically at next startup via `pipeline/schema.py`.

---

## API Reference

All endpoints require `Authorization: Bearer <token>` except `/auth/login` and `/health`.

| Method | Path | Scope | Description |
|---|---|---|---|
| POST | `/auth/login` | — | Issue JWT. Body: `{username, password}` |
| POST | `/auth/refresh` | — | Refresh token pair. Body: `{refresh_token}` |
| GET | `/health` | — | Liveness check. Returns `{"status":"ok"}` |
| POST | `/api/chat` | query:read | JSON chat endpoint (non-streaming). |
| POST | `/internal/query` | query:read | SSE streaming endpoint consumed by Go gateway. |
| POST | `/query` | query:read | REST query endpoint; same agent, JSON response. |
| POST | `/ingest/pdf` | tools:basic | Upload a PDF. Multipart form, field name `file`. |
| GET | `/history` | query:read | Last 50 queries for the authenticated user. |
| DELETE | `/history` | query:read | Clear conversation history. |
| GET | `/observability/status` | — | LangFuse / Phoenix / RAGAS availability. |
| GET | `/observability/scores` | query:read | Rolling RAGAS scores from Redis. |

---

## Production Deployment

Uses `docker-compose.prod.yml` with hardened containers: non-root users, read-only filesystems, all Linux capabilities dropped, secrets via Docker secrets files (not environment variables), and HashiCorp Vault for runtime secret injection.

```bash
# 1. Generate secret files (edit the values before deploying)
make secrets-init

# 2. Edit infra/secrets/db_pass.txt and infra/secrets/db_url.txt with real credentials
# 3. Set GOOGLE_API_KEY in your shell environment

# 4. Build and start production stack
make prod-up

# 5. Initialise Vault with secrets
make vault-init
```

The production stack includes a `db-migrate` service that applies `infra/postgres/init.sql` automatically on every start (idempotent). No manual `psql` step is required.

---

## Services and Ports

| Service | Port | Description |
|---|---|---|
| Next.js frontend | 3000 | User interface |
| Python API | 8000 | FastAPI application |
| Go SSE gateway | 8080 | Streaming proxy with rate limiting |
| PostgreSQL | 5432 | Primary database (pgvector enabled) |
| Redis | 6379 | Cache and session store |

---

## Data Pipeline

```
scripts/generate_data.py
  -> data/csv/movies.csv           (500 rows)
  -> data/csv/viewers.csv          (10 000 rows)
  -> data/csv/watch_activity.csv   (100 000 rows)
  -> data/csv/reviews.csv          (20 000 rows)
  -> data/csv/marketing_spend.csv  (200 rows, executive-only via RLS)
  -> data/csv/regional_performance.csv (500 rows)

Go ingestion service (goroutine pool, 8 workers)
  -> Reads CSV directory
  -> pgx CopyFrom bulk insert into PostgreSQL

Python lifespan hook (DuckDB dev mode only)
  -> read_csv_auto() for each CSV
  -> DuckDB in-process OLAP engine
```

---

## Security Model

- **Authentication**: RS256 JWT access tokens (15 min) + HS256 refresh tokens (7 days)
- **RBAC**: Scope-based; `require_scope()` dependency on every protected route
- **SQL injection**: regex guard rejects any statement containing INSERT/UPDATE/DELETE/DROP/ALTER before it reaches the database
- **Prompt injection**: Rebuff pattern detection on user input; bleach HTML strip
- **PII**: Presidio scrub on all document chunks at ingest and retrieval time
- **Document tamper detection**: HMAC-SHA256 signature verified on every retrieved chunk
- **Rate limiting**: SlowAPI on Python (10 req/min per IP on `/api/chat`); Redis token bucket on Go gateway
- **Audit log**: append-only `audit_log` table; raw queries stored as SHA-256 hashes only

---

## Known Limitations

- PostgreSQL initdb scripts run only on first volume creation. If the volume already exists without the schema, apply it manually: `docker exec -i axiom-postgres psql -U axiom_admin -d axiom_db < infra/postgres/init.sql`
- LangFuse v3 removed the low-level `.span()` / `.trace()` API. Tracing degrades silently to no-op; upgrade to `langfuse<3` or switch to the `@observe` decorator to re-enable span-level tracing.
- The Go SSE gateway does not independently verify JWTs. It forwards the Authorization header to the Python API, which validates on every request.
- PAL sandbox resource limits (`RLIMIT_CPU`, `RLIMIT_AS`) require Linux. On macOS Docker Desktop the limits are set inside the Linux VM and have no effect on host resources.
- GraphRAG index is pre-built offline via the Microsoft GraphRAG CLI. Rebuild with `python -m graphrag.index --root .` after adding a large document corpus.
- DSPy compilation (`pipeline/compile_dspy.py`) requires 50 labelled Q&A examples in `data/dspy_examples.json`. Run it once after curating examples and commit the compiled prompt to LangFuse.

---

## DSPy Prompt Optimisation

The agent system prompt is augmented with few-shot demonstrations compiled by DSPy BootstrapFewShot. This improves answer quality with zero extra API calls at inference time — the compiled demos are injected once into the system prompt at startup.

**First-time setup:**

```bash
# Step 1: generate 50 Q&A training examples from the live database
make generate-examples

# Step 2: run BootstrapFewShot compilation (~40–80 LM calls, runs once)
make compile-dspy

# Step 3: restart the API to load the compiled prompt
docker compose restart api-python
```

The compiled demos are saved to `data/dspy_compiled.json`. If this file does not exist, the API falls back to `data/dspy_examples.json` (raw examples). If neither file exists, the agent uses a plain system prompt.

Re-compile after adding more training examples or changing the data schema.

---

## Baseline RAGAS Scores (Gemini 2.5 Flash evaluator)

| Query type | context_relevance | faithfulness |
|---|---|---|
| SQL questions | N/A | 0.81 |
| PDF RAG questions | 0.74 | 0.79 |
| Mixed SQL + RAG | 0.71 | 0.77 |
| Target threshold | > 0.70 | > 0.75 |
