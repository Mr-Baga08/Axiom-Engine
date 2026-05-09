"""
Schema introspection for DIN-SQL.

get_schema_ddl() returns a sanitised DDL string containing only the
information the LLM needs to generate correct SQL:
  - Table names
  - Column names and data types
  - Primary / foreign key relationships (as comments)

What it deliberately omits:
  - CHECK constraints (could reveal business rules)
  - Index definitions (irrelevant for query generation)
  - RLS policies (must never be exposed to the LLM)
  - Row counts or sample data (PII risk)

The output is deterministic — the same schema always returns the same string.
This allows it to be cached and used as a stable prefix in LLM prompts.
"""

from __future__ import annotations

import os

_DUCKDB_SCHEMA_SQL = """
SELECT
    table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'main'
ORDER BY table_name, ordinal_position
"""

_PG_SCHEMA_SQL = """
SELECT
    c.table_name,
    c.column_name,
    c.data_type,
    c.is_nullable,
    tc.constraint_type
FROM information_schema.columns c
LEFT JOIN information_schema.key_column_usage kcu
    ON  kcu.table_name  = c.table_name
    AND kcu.column_name = c.column_name
LEFT JOIN information_schema.table_constraints tc
    ON  tc.constraint_name = kcu.constraint_name
    AND tc.table_name      = c.table_name
WHERE c.table_schema = 'public'
  AND c.table_name NOT IN ('audit_log', 'data_lineage')  -- never expose security tables
ORDER BY c.table_name, c.ordinal_position
"""

# Tables the LLM is allowed to know about.
# marketing_spend is included in the schema but its rows are filtered by RLS.
_ALLOWED_TABLES = {
    "movies", "viewers", "watch_activity",
    "reviews", "marketing_spend", "regional_performance",
}


def get_schema_ddl() -> str:
    """
    Return a sanitised DDL string suitable for inclusion in LLM prompts.

    The string format is:
        TABLE table_name
          column_name  DATA_TYPE  [NOT NULL] [PK] [FK→other_table.col]
          ...
        (blank line between tables)

    Raises:
        RuntimeError: if the database connection fails.
    """
    from pipeline.db import get_db_connection

    backend = os.getenv("DB_BACKEND", "duckdb").lower()
    conn = get_db_connection()

    try:
        if backend == "duckdb":
            rows = conn.execute(_DUCKDB_SCHEMA_SQL).fetchall()
            columns = ["table_name", "column_name", "data_type", "is_nullable"]
        else:
            rows = conn.execute(_PG_SCHEMA_SQL).fetchall()
            columns = [
                "table_name",
                "column_name",
                "data_type",
                "is_nullable",
                "constraint_type",
            ]
    finally:
        conn.close()

    # Build a dict: table_name → list of column dicts
    tables: dict[str, list[dict]] = {}
    for row in rows:
        row_dict = dict(zip(columns, row))
        tname = row_dict["table_name"]
        if tname not in _ALLOWED_TABLES:
            continue
        tables.setdefault(tname, []).append(row_dict)

    # Render as a compact DDL-like string
    lines: list[str] = []
    for table, cols in sorted(tables.items()):
        lines.append(f"TABLE {table}")
        for col in cols:
            nullable = "" if col["is_nullable"] == "NO" else " NULL"
            constraint = ""
            if col.get("constraint_type") == "PRIMARY KEY":
                constraint = " PK"
            elif col.get("constraint_type") == "FOREIGN KEY":
                constraint = " FK"
            lines.append(
                f"  {col['column_name']}  {col['data_type'].upper()}{nullable}{constraint}"
            )
        lines.append("")  # blank line between tables

    return "\n".join(lines).strip()