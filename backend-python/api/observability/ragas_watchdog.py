"""
observability/ragas_watchdog.py
────────────────────────────────
Background task that polls RAGAS scores from Redis every 10 minutes.

Behaviour
---------
- If average `context_relevance` across the last N scores < THRESHOLD (0.7),
  it fires a Slack alert and writes a WARNING to the audit_log table.
- Runs as a FastAPI background task started in the app lifespan.

Environment variables
---------------------
SLACK_WEBHOOK_URL           : Slack Incoming Webhook URL
RAGAS_ALERT_THRESHOLD       : float, default 0.7
RAGAS_POLL_INTERVAL_SECONDS : int,   default 600 (10 min)
RAGAS_WINDOW_SIZE           : int,   default 50 (last N evals to average)
DB_URL                      : PostgreSQL DSN for audit_log inserts
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Optional

import httpx
import structlog

log = structlog.get_logger(__name__)

THRESHOLD      = float(os.getenv("RAGAS_ALERT_THRESHOLD",        "0.7"))
POLL_INTERVAL  = int(os.getenv("RAGAS_POLL_INTERVAL_SECONDS",    "600"))
WINDOW_SIZE    = int(os.getenv("RAGAS_WINDOW_SIZE",              "50"))
SLACK_WEBHOOK  = os.getenv("SLACK_WEBHOOK_URL", "")


# ---------------------------------------------------------------------------
# Slack alerting
# ---------------------------------------------------------------------------

async def _send_slack_alert(avg_score: float, sample_size: int) -> None:
    if not SLACK_WEBHOOK:
        log.warning("slack_webhook_not_configured")
        return

    payload = {
        "text": (
            f":warning: *RAG Quality Alert*\n"
            f"Average `context_relevance` = *{avg_score:.3f}* "
            f"(threshold: {THRESHOLD}) over last {sample_size} evaluations.\n"
            f"Check Phoenix dashboard and review retrieval pipeline."
        ),
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *RAG Quality Alert — Action Required*\n"
                        f"Avg `context_relevance` dropped to *{avg_score:.3f}* "
                        f"(min threshold: `{THRESHOLD}`).\n"
                        f"Window: last *{sample_size}* evaluations.\n"
                        f"_Possible causes: stale embeddings, poor chunking, "
                        f"off-topic queries._"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Triggered at <!date^{int(time.time())}^{{date_time}}|now>"}
                ],
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(SLACK_WEBHOOK, json=payload)
            resp.raise_for_status()
            log.info("slack_alert_sent", avg_score=avg_score)
    except Exception as exc:
        log.error("slack_alert_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Audit log insert
# ---------------------------------------------------------------------------

async def _write_audit_log(avg_score: float, sample_size: int) -> None:
    db_url = os.getenv("DB_URL", "")
    if not db_url:
        return

    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        await conn.execute(
            """
            INSERT INTO audit_log
                (id, timestamp, action, resource, user_id, hashed_query, metadata)
            VALUES
                (gen_random_uuid(), NOW(), $1, $2, $3, $4, $5)
            """,
            "RAGAS_ALERT",
            "ragas_watchdog",
            "system",
            hashlib.sha256(f"ragas_alert_{time.time()}".encode()).hexdigest(),
            json.dumps({
                "avg_context_relevance": avg_score,
                "sample_size": sample_size,
                "threshold": THRESHOLD,
            }),
        )
        await conn.close()
        log.info("audit_log_written", action="RAGAS_ALERT")
    except Exception as exc:
        log.warning("audit_log_write_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Watchdog loop
# ---------------------------------------------------------------------------

async def ragas_watchdog() -> None:
    """
    Infinite loop — run as a background task via asyncio.create_task().

    Polls Redis for rolling RAGAS scores. If the rolling average of
    `context_relevance` falls below THRESHOLD, fires Slack alert and
    writes to audit_log.

    Will retry indefinitely; errors are logged but never crash the loop.
    """
    log.info(
        "ragas_watchdog_started",
        threshold=THRESHOLD,
        poll_interval_s=POLL_INTERVAL,
        window=WINDOW_SIZE,
    )

    # Track last alert time to avoid flooding (min 30-min gap between alerts)
    last_alert_ts: float = 0.0
    alert_cooldown: float = 1800.0

    while True:
        await asyncio.sleep(POLL_INTERVAL)

        try:
            from observability.phoenix_eval import get_rolling_scores
            scores = await get_rolling_scores(n=WINDOW_SIZE)

            if not scores:
                log.debug("ragas_watchdog_no_scores")
                continue

            relevance_vals = [
                s["context_relevance"]
                for s in scores
                if "context_relevance" in s
            ]

            if not relevance_vals:
                continue

            avg = sum(relevance_vals) / len(relevance_vals)
            n   = len(relevance_vals)

            log.info(
                "ragas_watchdog_tick",
                avg_context_relevance=round(avg, 4),
                sample_size=n,
                threshold=THRESHOLD,
            )

            if avg < THRESHOLD:
                now = time.time()
                if (now - last_alert_ts) >= alert_cooldown:
                    log.warning(
                        "ragas_below_threshold",
                        avg=round(avg, 4),
                        threshold=THRESHOLD,
                        sample_size=n,
                    )
                    await _send_slack_alert(avg_score=avg, sample_size=n)
                    await _write_audit_log(avg_score=avg, sample_size=n)
                    last_alert_ts = now
                else:
                    log.debug(
                        "ragas_alert_suppressed",
                        cooldown_remaining=round(alert_cooldown - (now - last_alert_ts)),
                    )

        except asyncio.CancelledError:
            log.info("ragas_watchdog_cancelled")
            return
        except Exception as exc:
            log.error("ragas_watchdog_error", error=str(exc), exc_info=True)
            # Back-off on repeated errors
            await asyncio.sleep(60)