"""
GraphRAG retriever — wraps the Microsoft graphrag query CLI.
The index is pre-built and mounted at GRAPHRAG_ROOT (default /data/graphrag).
Used as a fallback for high-level community-summary queries when CRAG returns
fewer than 2 chunks and USE_GRAPHRAG=true.
"""

import os
import subprocess

from observability.tracing import observe

GRAPHRAG_ROOT = os.getenv("GRAPHRAG_ROOT", "/data/graphrag")
GRAPHRAG_METHOD = os.getenv("GRAPHRAG_METHOD", "global")  # global | local


@observe(name="graphrag_retrieve", tags=["rag", "graphrag"])
def graphrag_query(query: str) -> str:
    """
    Run the graphrag query CLI and return text output.

    Raises:
        RuntimeError: if the CLI exits with a non-zero code.
        subprocess.TimeoutExpired: if the query takes longer than 30 seconds.
    """
    result = subprocess.run(
        [
            "python", "-m", "graphrag.query",
            "--root", GRAPHRAG_ROOT,
            "--method", GRAPHRAG_METHOD,
            query,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"graphrag error: {result.stderr[:200]}")
    return result.stdout.strip()
